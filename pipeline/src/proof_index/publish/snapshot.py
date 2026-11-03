"""Consistent catalog snapshot for release export (spec section 8).

The export takes the catalog-writer lock only while it opens a REPEATABLE READ,
read-only transaction and checks the pending-promotion guard; it releases the
lock once the snapshot is established and reads everything else from it. It
never holds the provider locks.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select, text

from proof_index.db import tables as t
from proof_index.db.locks import CATALOG_WRITER, advisory_lock
from proof_index.ingest.reconcile import assert_export_allowed


@dataclass
class WorkRow:
    id: uuid.UUID
    canonical: dict[str, Any]
    revision: int
    foundational: bool
    record_status: str


@dataclass
class CatalogSnapshot:
    snapshot_at: datetime
    works: list[WorkRow]
    events: dict[uuid.UUID, list[Any]]
    relations: list[Any]
    aliases: dict[str, str]
    streams: list[Any]
    source_record_counts: dict[str, int]
    visibility_counts: dict[str, int]
    classified_records: int
    total_records: int
    extra: dict[str, Any] = field(default_factory=dict)


def _live_target(merged: dict[uuid.UUID, uuid.UUID | None], start: uuid.UUID) -> uuid.UUID | None:
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = start
    while current is not None and merged.get(current) is not None:
        if current in seen:
            return None
        seen.add(current)
        current = merged[current]
    return current


def take_snapshot(engine: Engine) -> CatalogSnapshot:
    conn = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    tx = conn.begin()
    try:
        with advisory_lock(engine, CATALOG_WRITER):
            conn.execute(text("SET TRANSACTION READ ONLY"))
            snapshot_at = conn.execute(select(func.now())).scalar_one()  # snapshot established
            assert_export_allowed(conn)
        # Writer lock released; the transaction keeps reading the same snapshot.
        works = [
            WorkRow(r.id, r.canonical, r.canonical_revision, r.is_foundational, r.record_status)
            for r in conn.execute(
                select(t.works)
                .where(t.works.c.visibility == "included")
                .where(t.works.c.merged_into.is_(None))
                .order_by(t.works.c.id)
            )
        ]
        included = {w.id for w in works}
        events: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for row in conn.execute(
            select(t.work_events, t.source_records.c.source.label("provider"))
            .select_from(
                t.work_events.outerjoin(
                    t.source_records, t.source_records.c.id == t.work_events.c.source_record_id
                )
            )
            .order_by(
                t.work_events.c.work_id, t.work_events.c.event_date, t.work_events.c.event_key
            )
        ):
            if row.work_id in included:
                events[row.work_id].append(row)
        relations = [
            r for r in conn.execute(select(t.work_relations).order_by(t.work_relations.c.id))
        ]
        merged = {
            r.id: r.merged_into for r in conn.execute(select(t.works.c.id, t.works.c.merged_into))
        }
        aliases: dict[str, str] = {}
        for work_id, target in merged.items():
            if target is None:
                continue
            live = _live_target(merged, work_id)
            if live is not None and live in included:
                aliases[str(work_id)] = str(live)
        streams = list(
            conn.execute(
                select(
                    t.provider_streams,
                    t.coverage_slices.c.state.label("coverage_state"),
                    t.coverage_slices.c.expected_count,
                    t.coverage_slices.c.seen_count,
                    t.coverage_slices.c.valid_count,
                    t.coverage_slices.c.quarantined_count,
                    t.coverage_slices.c.notes,
                )
                .select_from(
                    t.provider_streams.outerjoin(
                        t.coverage_slices, t.coverage_slices.c.stream_id == t.provider_streams.c.id
                    )
                )
                .order_by(t.provider_streams.c.stream_key)
            )
        )
        counts: dict[str, int] = {}
        for source, n in conn.execute(
            select(t.source_records.c.source, func.count())
            .select_from(t.source_records.join(t.works, t.works.c.id == t.source_records.c.work_id))
            .where(t.works.c.visibility == "included")
            .where(t.works.c.merged_into.is_(None))
            .group_by(t.source_records.c.source)
        ):
            counts[source] = int(n)
        visibility = {
            v: int(n)
            for v, n in conn.execute(
                select(t.works.c.visibility, func.count())
                .where(t.works.c.merged_into.is_(None))
                .group_by(t.works.c.visibility)
            )
        }
        total_records = int(
            conn.execute(select(func.count()).select_from(t.source_records)).scalar_one()
        )
        classified = int(
            conn.execute(
                select(func.count(func.distinct(t.classifications.c.source_record_id)))
            ).scalar_one()
        )
        return CatalogSnapshot(
            snapshot_at=snapshot_at,
            works=works,
            events=dict(events),
            relations=relations,
            aliases=aliases,
            streams=streams,
            source_record_counts=counts,
            visibility_counts=visibility,
            classified_records=classified,
            total_records=total_records,
        )
    finally:
        tx.rollback()
        conn.close()
