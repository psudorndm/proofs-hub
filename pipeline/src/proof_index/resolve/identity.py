"""Conservative work identity resolution (spec section 11.1).

The normalized source key always identifies the source record. A record is
attached to a work only on strong evidence; everything weaker becomes an
identity review while the record gets its own work. Records already assigned to
a work are never moved automatically (curation moves them), so splits and
manual assignments survive later runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.repository import new_id
from proof_index.hashing import sha256_json
from proof_index.ingest.hooks import CurrentChange
from proof_index.models.source import NormalizedRecord
from proof_index.normalize.identifiers import qualified_identifier
from proof_index.normalize.text import meaningful_title_tokens, title_key
from proof_index.resolve import candidates as c


@dataclass
class Resolution:
    action: str  # "attach" | "new" | "none"
    work_id: uuid.UUID | None = None
    basis: str = ""
    reviews: list[tuple[uuid.UUID, str, dict[str, Any]]] = field(default_factory=list)


def _compatible(conn: Connection, record: NormalizedRecord, other_ids: list[uuid.UUID]) -> bool:
    """Explicit-ID / DOI compatibility: related titles and overlapping authors."""
    mine = set(meaningful_title_tokens(record.title))
    my_authors = set(c.authors_of(record))
    for other_id in other_ids:
        other = c.current_record(conn, other_id)
        if other is None:
            continue
        theirs = set(meaningful_title_tokens(other.title))
        title_ok = c.jaccard(mine, theirs) >= 0.5 or title_key(record.title) == title_key(
            other.title
        )
        their_authors = set(c.authors_of(other))
        authors_ok = not my_authors or not their_authors or bool(my_authors & their_authors)
        if title_ok and authors_ok:
            return True
    return False


def _work_dois(conn: Connection, work_id: uuid.UUID) -> set[str]:
    dois = set(
        conn.execute(
            select(t.source_records.c.doi)
            .where(t.source_records.c.work_id == work_id)
            .where(t.source_records.c.doi.is_not(None))
        ).scalars()
    )
    dois |= set(
        conn.execute(
            select(t.publications.c.doi)
            .where(t.publications.c.work_id == work_id)
            .where(t.publications.c.doi.is_not(None))
        ).scalars()
    )
    return dois


def _group_by_work(conn: Connection, rows: list[Any]) -> dict[uuid.UUID, list[uuid.UUID]]:
    grouped: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in rows:
        live = c.resolve_live(conn, row.work_id)
        grouped.setdefault(live, []).append(row.id)
    return grouped


def resolve_record(conn: Connection, record_id: uuid.UUID, record: NormalizedRecord) -> Resolution:
    # 1. Explicit curator assignment.
    override = conn.execute(
        select(t.source_assignment_overrides.c.target_work_id).where(
            t.source_assignment_overrides.c.source_record_id == record_id
        )
    ).scalar_one_or_none()
    if override is not None:
        return Resolution("attach", c.resolve_live(conn, override), "curator_assignment")

    reviews: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    # 2. Explicit cross-source identifiers, in either direction.
    explicit_rows: list[Any] = []
    for qid in c.explicit_refs(record):
        row = c.record_by_qualified_id(conn, qid)
        if row is not None and row.work_id is not None:
            explicit_rows.append(row)
    own = qualified_identifier(record.source, record.source_id)
    explicit_rows += c.records_referencing(conn, own, record_id)
    explicit = _group_by_work(conn, explicit_rows)
    if len(explicit) == 1:
        work_id, members = next(iter(explicit.items()))
        if _compatible(conn, record, members):
            return Resolution("attach", work_id, "explicit_identifier")
        reviews.append(
            (work_id, "explicit_identifier_incompatible", {"members": [str(m) for m in members]})
        )
    elif len(explicit) > 1:
        for work_id in explicit:
            reviews.append(
                (work_id, "explicit_identifier_conflict", {"works": [str(w) for w in explicit]})
            )

    # 3. Exact DOI.
    if record.doi and not reviews:
        doi_rows = c.records_with_doi(conn, record.doi, record_id)
        by_work = _group_by_work(conn, doi_rows)
        for pub_work in c.works_with_publication_doi(conn, record.doi):
            by_work.setdefault(c.resolve_live(conn, pub_work), [])
        if len(by_work) == 1:
            work_id, members = next(iter(by_work.items()))
            if members and _compatible(conn, record, members):
                return Resolution("attach", work_id, "doi")
            reviews.append((work_id, "doi_match_incompatible_metadata", {"doi": record.doi}))
        elif len(by_work) > 1:
            for work_id in by_work:
                reviews.append((work_id, "doi_claimed_by_multiple_works", {"doi": record.doi}))

    # 4–5. Exact normalized title, then the conservative title+author rule.
    tokens = meaningful_title_tokens(record.title)
    if not reviews:
        title_rows = c.records_with_title_key(conn, title_key(record.title), record_id)
        by_work = _group_by_work(conn, title_rows)
        if len(by_work) == 1 and len(tokens) >= c.MIN_TITLE_TOKENS and record.authors:
            work_id, members = next(iter(by_work.items()))
            mine = c.authors_of(record)
            work_dois = _work_dois(conn, work_id)
            doi_conflict = bool(record.doi and work_dois and record.doi not in work_dois)
            same_authors = any(
                (other := c.current_record(conn, m)) is not None and c.authors_of(other) == mine
                for m in members
            )
            if same_authors and not doi_conflict:
                return Resolution("attach", work_id, "title_and_authors")
            reviews.append(
                (
                    work_id,
                    "exact_title_insufficient_evidence",
                    {"doi_conflict": doi_conflict, "authors_match": same_authors},
                )
            )
        else:
            for work_id in by_work:
                reviews.append((work_id, "exact_title_ambiguous_or_short", {"tokens": len(tokens)}))

    # 6. Similarity fallback: review only.
    if not reviews:
        my_keys = set(c.authors_of(record))
        for row, score in c.similar_title_records(conn, tokens, record_id):
            other = c.current_record(conn, row.id)
            if other is None or not my_keys & set(c.authors_of(other)):
                continue
            reviews.append(
                (
                    c.resolve_live(conn, row.work_id),
                    "similar_title_and_author",
                    {"jaccard": round(score, 3)},
                )
            )

    # 7. No supported match: a new work (reviews link it to possible duplicates).
    return Resolution("new", None, "no_supported_match", reviews)


def _queue_reviews(
    conn: Connection,
    record_id: uuid.UUID,
    reviews: list[tuple[uuid.UUID, str, dict[str, Any]]],
    clock: Clock,
) -> None:
    for work_id, reason, evidence in reviews:
        key = sha256_json({"record": str(record_id), "work": str(work_id), "reason": reason})
        conn.execute(
            pg_insert(t.identity_reviews)
            .values(
                id=new_id(),
                candidate_key=key,
                source_record_id=record_id,
                candidate_work_id=work_id,
                reason=reason,
                evidence=evidence,
                state="pending",
                created_at=clock.now(),
            )
            .on_conflict_do_nothing(index_elements=["candidate_key"])
        )


def create_work(conn: Connection, clock: Clock) -> uuid.UUID:
    work_id = new_id()
    now = clock.now()
    conn.execute(
        t.works.insert().values(
            id=work_id,
            visibility="review",
            record_status="active",
            canonical={},
            canonical_revision=0,
            is_foundational=False,
            created_at=now,
            updated_at=now,
        )
    )
    return work_id


def current_decision(
    conn: Connection, record_id: uuid.UUID, content_sha256: str, rules_version: str
) -> str | None:
    return conn.execute(
        select(t.classifications.c.decision)
        .where(t.classifications.c.source_record_id == record_id)
        .where(t.classifications.c.content_sha256 == content_sha256)
        .where(t.classifications.c.rules_version == rules_version)
    ).scalar_one_or_none()


@dataclass(frozen=True)
class Assignment:
    work_id: uuid.UUID | None
    newly_attached: bool = False
    new_work: bool = False


ChangeCallback = Callable[[Connection, CurrentChange, uuid.UUID, bool, Any], None]


class IdentityResolver:
    """Assign works to records and rebuild affected canonical projections."""

    def __init__(
        self,
        clock: Clock,
        rules_version: str,
        rebuild: Callable[[Connection, uuid.UUID], Any],
        on_change: ChangeCallback | None = None,
    ) -> None:
        self.clock = clock
        self.rules_version = rules_version
        self.rebuild = rebuild
        self.on_change = on_change

    def ensure_work(self, conn: Connection, record_id: uuid.UUID) -> Assignment:
        """Resolve a record without a work if its classification makes it visible."""
        row = conn.execute(
            select(t.source_records.c.work_id).where(t.source_records.c.id == record_id)
        ).one()
        if row.work_id is not None:
            return Assignment(c.resolve_live(conn, row.work_id))
        record = c.current_record(conn, record_id)
        if record is None:
            return Assignment(None)
        if record.source == "crossref":
            return self._enrichment_attach(conn, record_id, record)
        decision = current_decision(conn, record_id, record.content_sha256(), self.rules_version)
        always = record.source == "curated" or self._has_override(conn, record_id)
        if decision not in ("include", "review") and not always:
            return Assignment(None)
        resolution = resolve_record(conn, record_id, record)
        _queue_reviews(conn, record_id, resolution.reviews, self.clock)
        is_new = resolution.work_id is None
        work_id = resolution.work_id or create_work(conn, self.clock)
        conn.execute(
            update(t.source_records)
            .where(t.source_records.c.id == record_id)
            .values(work_id=work_id)
        )
        return Assignment(work_id, newly_attached=True, new_work=is_new)

    def _enrichment_attach(
        self, conn: Connection, record_id: uuid.UUID, record: NormalizedRecord
    ) -> Assignment:
        """Crossref records enrich a known work by DOI; they never create works."""
        rows = c.records_with_doi(conn, record.source_id, record_id)
        by_work = _group_by_work(conn, rows)
        for pub_work in c.works_with_publication_doi(conn, record.source_id):
            by_work.setdefault(c.resolve_live(conn, pub_work), [])
        if len(by_work) == 1:
            work_id, members = next(iter(by_work.items()))
            if members and _compatible(conn, record, members):
                conn.execute(
                    update(t.source_records)
                    .where(t.source_records.c.id == record_id)
                    .values(work_id=work_id)
                )
                return Assignment(work_id, newly_attached=True, new_work=False)
            reason = "crossref_metadata_conflicts_with_work"
        else:
            reason = "doi_claimed_by_multiple_works" if by_work else "crossref_doi_without_work"
        _queue_reviews(
            conn, record_id, [(w, reason, {"doi": record.source_id}) for w in by_work], self.clock
        )
        return Assignment(None)

    def _has_override(self, conn: Connection, record_id: uuid.UUID) -> bool:
        return (
            conn.execute(
                select(t.source_assignment_overrides.c.source_record_id).where(
                    t.source_assignment_overrides.c.source_record_id == record_id
                )
            ).first()
            is not None
        )

    def current_changed(self, conn: Connection, change: CurrentChange) -> None:
        c.update_identity_keys(conn, change.source_record_id, change.item)
        assignment = self.ensure_work(conn, change.source_record_id)
        if assignment.work_id is None:
            return
        result = self.rebuild(conn, assignment.work_id)
        if self.on_change is not None:
            attached_to_existing = assignment.newly_attached and not assignment.new_work
            self.on_change(conn, change, assignment.work_id, attached_to_existing, result)
