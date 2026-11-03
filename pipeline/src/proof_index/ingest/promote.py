"""Promotion of staged rows (spec section 8, step 5 and the observation rules).

Each batch of up to 250 staged rows commits in one transaction together with
source identity upserts, observations, current-state updates, hook writes, and
the stage rows' promoted state. A resumed promoted row reuses its recorded
observation instead of recomputing a transition.

Observation keys:
* Provider-ordered records (any of modification time, version, harvest stamp):
  SHA-256 of identity, content hash, version, modification and harvest stamps.
* Records without ordering fields: identical hash to current → reuse current;
  otherwise key the transition on (identity, previous current id or "initial",
  incoming hash). This keeps an undated A→B→A history once and replay-safe.

Current selection compares provider ordering fields. Newer wins; equal fields
with identical content is a no-op; equal fields with different content is a
``conflict`` observation that does not replace current. Older observations are
retained (``older``) and never overwrite a newer current one. Without comparable
ordering, the same adapter falls back to fetch-snapshot order; different
adapters of one provider (arXiv Atom vs OAI) are treated as equal so neither
representation flip-flops over the other (decision D010).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, Engine, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.repository import new_id, transaction
from proof_index.hashing import sha256_json
from proof_index.http.rate_limit import Budget
from proof_index.ingest.hooks import ChangeKind, CurrentChange, PromotionHooks
from proof_index.models.source import NormalizedRecord, Tombstone

BATCH_SIZE = 250
_VERSION = re.compile(r"^v(\d+)$")

Comparison = Literal["newer", "older", "equal"]


@dataclass(frozen=True)
class Ordering:
    modified: datetime | None
    version: int | None
    harvest: datetime | None
    adapter: str
    fetch_order: tuple[datetime, int, int]  # (run started_at, page ordinal, item ordinal)

    @property
    def provider_ordered(self) -> bool:
        return self.modified is not None or self.version is not None or self.harvest is not None


def version_number(value: str | None) -> int | None:
    if value is None:
        return None
    match = _VERSION.match(value)
    return int(match.group(1)) if match else None


def _cmp(a: Any, b: Any) -> Comparison | None:
    if a is None or b is None or a == b:
        return None
    return "newer" if a > b else "older"


def compare(incoming: Ordering, current: Ordering) -> Comparison:
    for a, b in (
        (incoming.modified, current.modified),
        (incoming.version, current.version),
        (incoming.harvest, current.harvest),
    ):
        result = _cmp(a, b)
        if result is not None:
            return result
    both_have = any(
        a is not None and b is not None
        for a, b in (
            (incoming.modified, current.modified),
            (incoming.version, current.version),
            (incoming.harvest, current.harvest),
        )
    )
    if both_have:
        return "equal"
    if incoming.adapter != current.adapter:
        return "equal"
    fetch = _cmp(incoming.fetch_order, current.fetch_order)
    return fetch or "equal"


def item_from_payload(payload: dict[str, Any], item_kind: str) -> NormalizedRecord | Tombstone:
    if item_kind == "tombstone":
        return Tombstone.model_validate(payload)
    return NormalizedRecord.model_validate(payload)


def _ordering(
    item: NormalizedRecord | Tombstone, adapter: str, fetch_order: tuple[datetime, int, int]
) -> Ordering:
    if isinstance(item, Tombstone):
        return Ordering(None, None, item.harvest_datestamp, adapter, fetch_order)
    return Ordering(
        item.source_modified_at,
        version_number(item.source_version),
        item.harvest_datestamp,
        adapter,
        fetch_order,
    )


def observation_key(
    item: NormalizedRecord | Tombstone,
    content_hash: str,
    previous_current: uuid.UUID | None,
    ordered: bool,
) -> str:
    identity = {"source": item.source, "source_id": item.source_id}
    if ordered:
        modified = item.source_modified_at if isinstance(item, NormalizedRecord) else None
        version = item.source_version if isinstance(item, NormalizedRecord) else None
        return sha256_json(
            {
                **identity,
                "content": content_hash,
                "version": version,
                "modified": modified.isoformat() if modified else None,
                "harvest": item.harvest_datestamp.isoformat() if item.harvest_datestamp else None,
            }
        )
    return sha256_json(
        {
            **identity,
            "previous": str(previous_current) if previous_current else "initial",
            "content": content_hash,
        }
    )


@dataclass
class PromotionResult:
    promoted: int = 0
    current_changes: int = 0
    paused: bool = False


def _current_ordering(conn: Connection, observation_id: uuid.UUID) -> tuple[Ordering, str]:
    row = conn.execute(
        select(
            t.source_observations.c.normalized,
            t.source_observations.c.item_kind,
            t.source_observations.c.content_sha256,
            t.source_observations.c.adapter,
            t.harvest_runs.c.started_at,
            t.harvest_pages.c.ordinal,
        )
        .select_from(
            t.source_observations.join(
                t.harvest_runs, t.harvest_runs.c.id == t.source_observations.c.run_id
            ).join(t.harvest_pages, t.harvest_pages.c.id == t.source_observations.c.page_id)
        )
        .where(t.source_observations.c.id == observation_id)
    ).one()
    item = item_from_payload(row.normalized, row.item_kind)
    return _ordering(item, row.adapter, (row.started_at, row.ordinal, 0)), row.content_sha256


def promote_row(
    conn: Connection,
    row: Any,
    *,
    run: Any,
    adapter: str,
    parser_version: str,
    stream_kind: str,
    clock: Clock,
    hooks: PromotionHooks,
) -> tuple[uuid.UUID, bool]:
    """Promote one staged row. Returns (observation id recorded on the stage row, changed)."""
    item = item_from_payload(row.payload, row.item_kind)
    now = clock.now()
    upsert = (
        pg_insert(t.source_records)
        .values(
            id=new_id(),
            source=item.source,
            source_id=item.source_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(index_elements=["source", "source_id"], set_={"last_seen_at": now})
        .returning(t.source_records.c.id, t.source_records.c.current_observation_id)
    )
    record_id, current_id = conn.execute(upsert).one()
    content_hash = row.content_sha256
    fetch_order = (run.started_at, row.page_ordinal, row.item_ordinal)
    incoming = _ordering(item, adapter, fetch_order)

    current: tuple[Ordering, str] | None = None
    if current_id is not None:
        current = _current_ordering(conn, current_id)
        if current[1] == content_hash:
            return current_id, False  # identical content: no-op, reuse current

    if current is None:
        decision: Comparison = "newer"
    else:
        decision = compare(incoming, current[0])

    if current is None:
        change_kind = "tombstone" if isinstance(item, Tombstone) else "initial"
    elif decision == "older":
        change_kind = "older"
    elif decision == "equal":
        change_kind = "conflict"
    elif isinstance(item, Tombstone):
        change_kind = "tombstone"
    else:
        reverted = conn.execute(
            select(t.source_observations.c.id)
            .where(t.source_observations.c.source_record_id == record_id)
            .where(t.source_observations.c.content_sha256 == content_hash)
            .limit(1)
        ).first()
        change_kind = "reverted" if reverted else "changed"

    key = observation_key(item, content_hash, current_id, incoming.provider_ordered)
    becomes_current = decision == "newer"
    inserted = conn.execute(
        pg_insert(t.source_observations)
        .values(
            id=new_id(),
            source_record_id=record_id,
            observation_key=key,
            run_id=run.id,
            page_id=row.page_id,
            normalized=row.payload,
            content_sha256=content_hash,
            source_modified_at=getattr(item, "source_modified_at", None),
            harvest_datestamp=item.harvest_datestamp,
            source_version=getattr(item, "source_version", None),
            observed_at=now,
            change_kind=change_kind,
            is_current_eligible=becomes_current,
            item_kind=row.item_kind,
            parser_version=parser_version,
            previous_observation_id=current_id,
            adapter=adapter,
        )
        .on_conflict_do_nothing(index_elements=["observation_key"])
        .returning(t.source_observations.c.id)
    ).first()
    if inserted is None:
        # Replay of an already-recorded observation: reuse it; do not recompute.
        existing = conn.execute(
            select(t.source_observations.c.id).where(t.source_observations.c.observation_key == key)
        ).scalar_one()
        return existing, False
    observation_id = inserted[0]
    if not becomes_current:
        return observation_id, False
    conn.execute(
        update(t.source_records)
        .where(t.source_records.c.id == record_id)
        .values(current_observation_id=observation_id)
    )
    hook_kind: ChangeKind = change_kind  # type: ignore[assignment]
    hooks.current_changed(
        conn,
        CurrentChange(
            source_record_id=record_id,
            observation_id=observation_id,
            previous_observation_id=current_id,
            item=item,
            change_kind=hook_kind,
            run_id=run.id,
            run_mode=run.mode,
            stream_kind=stream_kind,
            observed_at=now,
        ),
    )
    return observation_id, True


def promote_run(
    engine: Engine,
    run_id: uuid.UUID,
    *,
    adapter: str,
    parser_version: str,
    stream_kind: str,
    clock: Clock,
    hooks: PromotionHooks,
    budget: Budget | None = None,
    reserve_s: float = 60.0,
    batch_size: int = BATCH_SIZE,
    fail_after_batches: int | None = None,
) -> PromotionResult:
    """Promote all pending rows of ``run_id`` in batches. Caller holds the writer lock.

    ``fail_after_batches`` injects a crash after N committed batches (tests only).
    """
    result = PromotionResult()
    batches = 0
    while True:
        if budget is not None and budget.nearly_exhausted(reserve_s):
            result.paused = True
            return result
        with transaction(engine) as conn:
            run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).one()
            rows = conn.execute(
                select(
                    t.staged_records.c.id,
                    t.staged_records.c.payload,
                    t.staged_records.c.item_kind,
                    t.staged_records.c.content_sha256,
                    t.staged_records.c.page_id,
                    t.staged_records.c.item_ordinal,
                    t.harvest_pages.c.ordinal.label("page_ordinal"),
                )
                .select_from(
                    t.staged_records.join(
                        t.harvest_pages, t.harvest_pages.c.id == t.staged_records.c.page_id
                    )
                )
                .where(t.staged_records.c.run_id == run_id)
                .where(t.staged_records.c.state == "pending")
                .order_by(t.harvest_pages.c.ordinal, t.staged_records.c.item_ordinal)
                .limit(batch_size)
                .with_for_update(of=t.staged_records)
            ).all()
            if not rows:
                return result
            for row in rows:
                observation_id, changed = promote_row(
                    conn,
                    row,
                    run=run,
                    adapter=adapter,
                    parser_version=parser_version,
                    stream_kind=stream_kind,
                    clock=clock,
                    hooks=hooks,
                )
                conn.execute(
                    update(t.staged_records)
                    .where(t.staged_records.c.id == row.id)
                    .values(state="promoted", promoted_observation_id=observation_id)
                )
                result.promoted += 1
                result.current_changes += int(changed)
            conn.execute(
                update(t.harvest_runs)
                .where(t.harvest_runs.c.id == run_id)
                .values(records_promoted=t.harvest_runs.c.records_promoted + len(rows))
            )
        batches += 1
        if fail_after_batches is not None and batches >= fail_after_batches:
            raise RuntimeError("injected promotion failure (test)")
