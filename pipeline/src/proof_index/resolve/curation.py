"""Curator commands (spec section 11.4).

YAML commands are validated first, applied transactionally under the catalog
writer lock (held by the caller), and recorded as immutable audit rows.
Re-applying the same command is a no-op; reusing a command ID with different
content is an error; a stale ``expected_canonical_revision`` is a readable conflict.

State-type commands (include, exclude, set_labels, set_fields) are folded from the
active (non-reverted) command list at every canonical rebuild, so a revert never
restores an old snapshot over later source observations.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.repository import new_id
from proof_index.errors import DataValidationError, InputError
from proof_index.hashing import sha256_json
from proof_index.models.classification import Area, Contribution, Taxonomy
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author
from proof_index.normalize.identifiers import normalize_doi
from proof_index.resolve.candidates import resolve_live
from proof_index.resolve.canonical import CanonicalBuilder, CurationState
from proof_index.resolve.events import record_curation_event, sync_publication_event

STATE_KINDS = ("include", "exclude", "set_labels", "set_fields")
_COMMAND_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{2,127}$")
_LABEL = re.compile(r"^new:[a-z0-9][a-z0-9\-]{0,40}$")


class StaleRevisionError(DataValidationError):
    code = "stale_revision"


class CurationConflict(DataValidationError):
    code = "curation_conflict"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    reason: str = Field(min_length=3)
    reviewer_label: str = Field(min_length=1)
    review_kind: Literal["agent-provisional", "human-reviewed"]

    @field_validator("command_id")
    @classmethod
    def _cid(cls, value: str) -> str:
        if not _COMMAND_ID.match(value):
            raise ValueError("command_id must be 3-128 chars of [A-Za-z0-9._-]")
        return value


class IncludeCommand(_Base):
    action: Literal["include"]
    work_id: uuid.UUID
    expected_canonical_revision: int


class ExcludeCommand(_Base):
    action: Literal["exclude"]
    work_id: uuid.UUID
    expected_canonical_revision: int


class SetLabelsCommand(_Base):
    action: Literal["set_labels"]
    work_id: uuid.UUID
    expected_canonical_revision: int
    areas: list[Area] | None = None
    topics: list[str] | None = None
    contributions: list[Contribution] | None = None


class FieldValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = None
    authors: list[Author] | None = None
    abstract: str | None = None
    published: PartialDate | None = None
    doi: str | None = None

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        if value is not None and normalize_doi(value) is None:
            raise ValueError("invalid DOI")
        return normalize_doi(value) if value else None


class SetFieldsCommand(_Base):
    action: Literal["set_fields"]
    work_id: uuid.UUID
    expected_canonical_revision: int
    fields: FieldValues


class MarkFoundationalCommand(_Base):
    action: Literal["mark_foundational"]
    work_id: uuid.UUID
    expected_canonical_revision: int
    foundational: bool


class MergeCommand(_Base):
    action: Literal["merge"]
    survivor: uuid.UUID
    absorbed: list[uuid.UUID] = Field(min_length=1)
    expected_canonical_revision: dict[uuid.UUID, int]


class SplitCommand(_Base):
    action: Literal["split"]
    work_id: uuid.UUID
    expected_canonical_revision: int
    assignments: dict[uuid.UUID, str]  # source_record_id -> "keep" | "new:<label>"
    publications: dict[uuid.UUID, str] = Field(default_factory=dict)

    @field_validator("assignments", "publications")
    @classmethod
    def _targets(cls, value: dict[uuid.UUID, str]) -> dict[uuid.UUID, str]:
        for target in value.values():
            if target != "keep" and not _LABEL.match(target):
                raise ValueError("split targets are 'keep' or 'new:<label>'")
        return value


class RelateCommand(_Base):
    action: Literal["relate"]
    from_work: uuid.UUID
    to_work: uuid.UUID
    relation: Literal["extended_by", "related_to"]


class RevertCommand(_Base):
    action: Literal["revert"]
    reverts: str


Command = Annotated[
    IncludeCommand
    | ExcludeCommand
    | SetLabelsCommand
    | SetFieldsCommand
    | MarkFoundationalCommand
    | MergeCommand
    | SplitCommand
    | RelateCommand
    | RevertCommand,
    Field(discriminator="action"),
]


class _Envelope(BaseModel):
    command: Command


def parse_command(data: Any, taxonomy: Taxonomy | None = None) -> Any:
    try:
        command = _Envelope.model_validate({"command": data}).command
    except ValidationError as exc:
        raise InputError(f"invalid curation command: {exc}") from None
    if taxonomy is not None and isinstance(command, SetLabelsCommand):
        problems = taxonomy.validate_labels(
            list(command.areas or []), list(command.topics or []), list(command.contributions or [])
        )
        if problems:
            raise InputError("; ".join(problems))
    return command


def load_command_file(path: Path, taxonomy: Taxonomy | None = None) -> Any:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"command file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise InputError(f"command file is not valid YAML: {exc}") from None
    return parse_command(data, taxonomy)


def command_hash(command: Any) -> str:
    return sha256_json(command.model_dump(mode="json"))


# --- curation state for canonical rebuilds ------------------------------------------


def _active_state_rows(conn: Connection, work_id: uuid.UUID) -> list[Any]:
    reverted = select(t.curation_commands.c.reverts_command_id).where(
        t.curation_commands.c.reverts_command_id.is_not(None)
    )
    return list(
        conn.execute(
            select(t.curation_commands)
            .where(t.curation_commands.c.kind.in_(STATE_KINDS))
            .where(t.curation_commands.c.payload["work_id"].astext == str(work_id))
            .where(t.curation_commands.c.id.not_in(reverted))
            .order_by(t.curation_commands.c.applied_at, t.curation_commands.c.id)
        ).all()
    )


def fold_state(rows: list[dict[str, Any]]) -> CurationState:
    state = CurationState()
    for row in rows:
        payload, kind = row["payload"], row["kind"]
        state.review_kind = row["review_kind"]
        state.reason = row["reason"]
        if kind == "include":
            state.visibility = "included"
        elif kind == "exclude":
            state.visibility = "excluded"
        elif kind == "set_labels":
            for name in ("areas", "topics", "contributions"):
                if payload.get(name) is not None:
                    setattr(state, name, list(payload[name]))
        elif kind == "set_fields":
            for name, value in payload["fields"].items():
                if value is not None:
                    state.fields[name] = value
                    state.command_ids[name] = row["command_id"]
    return state


def load_curation_state(
    conn: Connection, work_id: uuid.UUID, pending: list[dict[str, Any]] | None = None
) -> CurationState:
    pending = list(pending or [])
    excluded = {p["exclude_command_id"] for p in pending if "exclude_command_id" in p}
    rows = [
        {
            "payload": r.payload,
            "kind": r.kind,
            "review_kind": r.review_kind,
            "reason": r.reason,
            "command_id": r.command_id,
        }
        for r in _active_state_rows(conn, work_id)
        if r.command_id not in excluded
    ]
    return fold_state(rows + [p for p in pending if "exclude_command_id" not in p])


# --- applying --------------------------------------------------------------------------


def _work(conn: Connection, work_id: uuid.UUID) -> Any:
    row = conn.execute(select(t.works).where(t.works.c.id == work_id).with_for_update()).first()
    if row is None:
        raise InputError(f"work {work_id} does not exist")
    return row


def _check_revision(conn: Connection, work_id: uuid.UUID, expected: int) -> Any:
    row = _work(conn, work_id)
    if row.merged_into is not None:
        live = resolve_live(conn, work_id)
        raise StaleRevisionError(
            f"work {work_id} was merged into {live}; re-target the command at the survivor"
        )
    if row.canonical_revision != expected:
        title = (row.canonical or {}).get("title", "?")
        raise StaleRevisionError(
            f"stale revision for work {work_id} ({title!r}): expected "
            f"{expected}, current {row.canonical_revision}. Review the current state "
            "(including intervening source updates) and resubmit with the new revision."
        )
    return row


def _snapshot(conn: Connection, work_ids: list[uuid.UUID]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for work_id in sorted(set(work_ids)):
        row = conn.execute(select(t.works).where(t.works.c.id == work_id)).one()
        records = (
            conn.execute(
                select(t.source_records.c.id)
                .where(t.source_records.c.work_id == work_id)
                .order_by(t.source_records.c.id)
            )
            .scalars()
            .all()
        )
        pubs = (
            conn.execute(
                select(t.publications.c.id)
                .where(t.publications.c.work_id == work_id)
                .order_by(t.publications.c.id)
            )
            .scalars()
            .all()
        )
        out[str(work_id)] = {
            "canonical_revision": row.canonical_revision,
            "visibility": row.visibility,
            "is_foundational": row.is_foundational,
            "merged_into": str(row.merged_into) if row.merged_into else None,
            "source_records": [str(r) for r in records],
            "publications": [str(p) for p in pubs],
        }
    return out


def _override(
    conn: Connection, record_id: uuid.UUID, work_id: uuid.UUID, command_row: uuid.UUID
) -> None:
    conn.execute(
        pg_insert(t.source_assignment_overrides)
        .values(source_record_id=record_id, target_work_id=work_id, command_id=command_row)
        .on_conflict_do_update(
            index_elements=["source_record_id"],
            set_={"target_work_id": work_id, "command_id": command_row},
        )
    )


def _separate(conn: Connection, a: uuid.UUID, b: uuid.UUID, command_row: uuid.UUID) -> None:
    lo, hi = sorted([a, b])
    conn.execute(
        pg_insert(t.separation_constraints)
        .values(work_a=lo, work_b=hi, command_id=command_row)
        .on_conflict_do_nothing()
    )


def _separated(conn: Connection, a: uuid.UUID, b: uuid.UUID) -> bool:
    lo, hi = sorted([a, b])
    return (
        conn.execute(
            select(t.separation_constraints.c.work_a).where(
                and_(
                    t.separation_constraints.c.work_a == lo, t.separation_constraints.c.work_b == hi
                )
            )
        ).first()
        is not None
    )


class CurationService:
    def __init__(
        self, clock: Clock, builder: CanonicalBuilder, taxonomy: Taxonomy | None = None
    ) -> None:
        self.clock = clock
        self.builder = builder
        self.taxonomy = taxonomy

    def _rebuild(
        self,
        conn: Connection,
        work_ids: list[uuid.UUID],
        pending: list[dict[str, Any]] | None = None,
    ) -> None:
        for work_id in sorted(set(work_ids)):
            result = self.builder.rebuild(conn, work_id, pending)
            if result.canonical is not None:
                sync_publication_event(conn, work_id, result.canonical, self.clock)

    def validate(self, conn: Connection, command: Any) -> dict[str, Any]:
        """Check references and revisions without writing (``curate validate``)."""
        status = self._existing(conn, command)
        if status == "already_applied":
            return {"command_id": command.command_id, "status": status}
        for work_id, expected in self._expected(command):
            _check_revision(conn, work_id, expected)
        return {"command_id": command.command_id, "status": "valid"}

    def _existing(self, conn: Connection, command: Any) -> str | None:
        row = conn.execute(
            select(t.curation_commands.c.command_hash).where(
                t.curation_commands.c.command_id == command.command_id
            )
        ).first()
        if row is None:
            return None
        if row.command_hash == command_hash(command):
            return "already_applied"
        raise CurationConflict(
            f"command_id {command.command_id!r} was already used with different content"
        )

    def _expected(self, command: Any) -> list[tuple[uuid.UUID, int]]:
        if isinstance(command, MergeCommand):
            involved = {command.survivor, *command.absorbed}
            missing = involved - set(command.expected_canonical_revision)
            if missing:
                raise InputError(
                    f"merge needs expected_canonical_revision for {sorted(map(str, missing))}"
                )
            return [(w, command.expected_canonical_revision[w]) for w in sorted(involved)]
        if isinstance(command, (RelateCommand, RevertCommand)):
            return []
        return [(command.work_id, command.expected_canonical_revision)]

    def apply(self, conn: Connection, command: Any) -> dict[str, Any]:
        if self._existing(conn, command) == "already_applied":
            return {"command_id": command.command_id, "status": "already_applied"}
        for work_id, expected in self._expected(command):
            _check_revision(conn, work_id, expected)
        row_id = new_id()
        payload = command.model_dump(mode="json")
        involved = self._involved(conn, command)
        before = _snapshot(conn, involved)
        reverts: uuid.UUID | None = None
        pending: list[dict[str, Any]] | None = None

        if command.action in STATE_KINDS:
            pending = [
                {
                    "payload": payload,
                    "kind": command.action,
                    "review_kind": command.review_kind,
                    "reason": command.reason,
                    "command_id": command.command_id,
                }
            ]
        elif isinstance(command, MarkFoundationalCommand):
            conn.execute(
                update(t.works)
                .where(t.works.c.id == command.work_id)
                .values(is_foundational=command.foundational)
            )
        elif isinstance(command, MergeCommand):
            involved = self._merge(conn, command, row_id)
        elif isinstance(command, SplitCommand):
            involved = self._split(conn, command, row_id)
        elif isinstance(command, RelateCommand):
            self._relate(conn, command, row_id)
        elif isinstance(command, RevertCommand):
            reverts, involved = self._revert(conn, command)
            pending = [{"exclude_command_id": command.reverts}]

        self._rebuild(conn, [w for w in involved if self._is_live(conn, w)], pending)
        for work_id in involved:
            record_curation_event(
                conn, work_id, row_id, command.command_id, command.action, self.clock
            )
        after = _snapshot(conn, involved)
        conn.execute(
            t.curation_commands.insert().values(
                id=row_id,
                command_id=command.command_id,
                command_hash=command_hash(command),
                kind=command.action,
                payload=payload,
                reviewer_label=command.reviewer_label,
                review_kind=command.review_kind,
                reason=command.reason,
                applied_at=self.clock.now(),
                before_state=before,
                after_state=after,
                reverts_command_id=reverts,
            )
        )
        return {
            "command_id": command.command_id,
            "status": "applied",
            "works": [str(w) for w in involved],
            "after": after,
        }

    def _is_live(self, conn: Connection, work_id: uuid.UUID) -> bool:
        return (
            conn.execute(select(t.works.c.merged_into).where(t.works.c.id == work_id)).scalar_one()
            is None
        )

    def _involved(self, conn: Connection, command: Any) -> list[uuid.UUID]:
        if isinstance(command, MergeCommand):
            return [command.survivor, *command.absorbed]
        if isinstance(command, RelateCommand):
            return [command.from_work, command.to_work]
        if isinstance(command, RevertCommand):
            original = self._original(conn, command.reverts)
            return [uuid.UUID(w) for w in original.after_state]
        return [command.work_id]

    # -- structural actions ---------------------------------------------------------------

    def _merge(self, conn: Connection, command: MergeCommand, row_id: uuid.UUID) -> list[uuid.UUID]:
        survivor = command.survivor
        absorbed = list(dict.fromkeys(command.absorbed))
        if survivor in absorbed:
            raise InputError("survivor cannot also be absorbed")
        everyone = [survivor, *absorbed]
        for i, a in enumerate(everyone):
            for b in everyone[i + 1 :]:
                if _separated(conn, a, b):
                    raise CurationConflict(f"works {a} and {b} have a separation constraint")
        for work_id in absorbed:
            moved = (
                conn.execute(
                    select(t.source_records.c.id).where(t.source_records.c.work_id == work_id)
                )
                .scalars()
                .all()
            )
            conn.execute(
                update(t.source_records)
                .where(t.source_records.c.work_id == work_id)
                .values(work_id=survivor)
            )
            conn.execute(
                update(t.publications)
                .where(t.publications.c.work_id == work_id)
                .values(work_id=survivor)
            )
            for record_id in moved:
                _override(conn, record_id, survivor, row_id)
            conn.execute(
                update(t.works)
                .where(t.works.c.id == work_id)
                .values(merged_into=survivor, updated_at=self.clock.now())
            )
        if resolve_live(conn, survivor) != survivor:  # cycle / alias safety
            raise CurationConflict("merge would create an alias cycle")
        return everyone

    def _split(self, conn: Connection, command: SplitCommand, row_id: uuid.UUID) -> list[uuid.UUID]:
        work_id = command.work_id
        current = set(
            conn.execute(
                select(t.source_records.c.id).where(t.source_records.c.work_id == work_id)
            ).scalars()
        )
        assigned = set(command.assignments)
        if assigned != current:
            missing = sorted(map(str, current - assigned))
            extra = sorted(map(str, assigned - current))
            raise InputError(
                f"split must assign every source record of the work exactly; missing={missing} "
                f"not-in-work={extra}"
            )
        pubs = set(
            conn.execute(
                select(t.publications.c.id).where(t.publications.c.work_id == work_id)
            ).scalars()
        )
        if set(command.publications) != pubs:
            raise InputError("split must assign every publication of the work")
        if "keep" not in command.assignments.values():
            raise InputError("at least one source record must keep the existing work ID")
        labels = sorted(
            {
                v
                for v in [*command.assignments.values(), *command.publications.values()]
                if v != "keep"
            }
        )
        if not labels:
            raise InputError("split creates no new work")
        from proof_index.resolve.identity import create_work

        targets = {"keep": work_id} | {label: create_work(conn, self.clock) for label in labels}
        for record_id, target in command.assignments.items():
            conn.execute(
                update(t.source_records)
                .where(t.source_records.c.id == record_id)
                .values(work_id=targets[target])
            )
            _override(conn, record_id, targets[target], row_id)
        for pub_id, target in command.publications.items():
            conn.execute(
                update(t.publications)
                .where(t.publications.c.id == pub_id)
                .values(work_id=targets[target])
            )
        all_works = list(targets.values())
        for i, a in enumerate(all_works):
            for b in all_works[i + 1 :]:
                _separate(conn, a, b, row_id)
        return all_works

    def _relate(self, conn: Connection, command: RelateCommand, row_id: uuid.UUID) -> None:
        if command.from_work == command.to_work:
            raise InputError("a work cannot relate to itself")
        for work_id in (command.from_work, command.to_work):
            _work(conn, work_id)
        conn.execute(
            pg_insert(t.work_relations)
            .values(
                id=new_id(),
                from_work=command.from_work,
                to_work=command.to_work,
                relation=command.relation,
                evidence={"command_id": command.command_id, "reason": command.reason},
                created_at=self.clock.now(),
            )
            .on_conflict_do_nothing()
        )

    def _original(self, conn: Connection, command_id: str) -> Any:
        row = conn.execute(
            select(t.curation_commands).where(t.curation_commands.c.command_id == command_id)
        ).first()
        if row is None:
            raise InputError(f"no applied command {command_id!r} to revert")
        return row

    def _revert(
        self, conn: Connection, command: RevertCommand
    ) -> tuple[uuid.UUID, list[uuid.UUID]]:
        original = self._original(conn, command.reverts)
        if original.kind == "revert":
            raise InputError("reverting a revert is not supported; re-apply a new command instead")
        already = conn.execute(
            select(func.count())
            .select_from(t.curation_commands)
            .where(t.curation_commands.c.reverts_command_id == original.id)
        ).scalar_one()
        if already:
            raise CurationConflict(f"command {command.reverts!r} was already reverted")
        involved = [uuid.UUID(w) for w in original.after_state]
        current = _snapshot(conn, involved)
        for work_id, expected in original.after_state.items():
            now = current[work_id]
            if (now["source_records"], now["publications"], now["merged_into"]) != (
                expected["source_records"],
                expected["publications"],
                expected["merged_into"],
            ):
                raise CurationConflict(
                    f"cannot revert {command.reverts}: assignments of work {work_id} changed "
                    "since it was applied; resolve by review"
                )
        if original.kind == "mark_foundational":
            work_id = uuid.UUID(original.payload["work_id"])
            if (
                current[str(work_id)]["is_foundational"]
                != original.after_state[str(work_id)]["is_foundational"]
            ):
                raise CurationConflict("foundational flag changed since the command was applied")
            conn.execute(
                update(t.works)
                .where(t.works.c.id == work_id)
                .values(is_foundational=original.before_state[str(work_id)]["is_foundational"])
            )
        elif original.kind in ("merge", "split"):
            self._restore_assignments(conn, original)
        elif original.kind == "relate":
            p = original.payload
            conn.execute(
                delete(t.work_relations).where(
                    and_(
                        t.work_relations.c.from_work == uuid.UUID(p["from_work"]),
                        t.work_relations.c.to_work == uuid.UUID(p["to_work"]),
                        t.work_relations.c.relation == p["relation"],
                    )
                )
            )
        return original.id, involved

    def _restore_assignments(self, conn: Connection, original: Any) -> None:
        conn.execute(
            delete(t.source_assignment_overrides).where(
                t.source_assignment_overrides.c.command_id == original.id
            )
        )
        conn.execute(
            delete(t.separation_constraints).where(
                t.separation_constraints.c.command_id == original.id
            )
        )
        before = original.before_state
        for work_key, state in before.items():
            work_id = uuid.UUID(work_key)
            for record in state["source_records"]:
                conn.execute(
                    update(t.source_records)
                    .where(t.source_records.c.id == uuid.UUID(record))
                    .values(work_id=work_id)
                )
            for pub in state["publications"]:
                conn.execute(
                    update(t.publications)
                    .where(t.publications.c.id == uuid.UUID(pub))
                    .values(work_id=work_id)
                )
            merged = state["merged_into"]
            conn.execute(
                update(t.works)
                .where(t.works.c.id == work_id)
                .values(merged_into=uuid.UUID(merged) if merged else None)
            )
        if original.kind == "split":
            # Works created by the split become aliases of the original, keeping URLs valid.
            created = set(original.after_state) - set(before)
            target = uuid.UUID(next(iter(before)))
            for work_key in created:
                conn.execute(
                    update(t.works)
                    .where(t.works.c.id == uuid.UUID(work_key))
                    .values(merged_into=target)
                )
