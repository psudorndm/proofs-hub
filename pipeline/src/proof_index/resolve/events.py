"""Work events and feed eligibility (spec section 11.3).

* One ``publication`` event per work (key ``publication:<work>``); its date follows
  the canonical original publication and can be corrected. Feed-eligible only for
  day-precision dates; the New list applies its recent window at export time, so
  an old paper discovered today never appears as new.
* ``source_added`` when another source attaches to an existing work (not feed-eligible).
* ``source_revision`` when a provider version advances after a known observation,
  dated by the provider's update date.
* ``metadata_updated`` when public content changes without a version change; an
  observed date is used only after a baseline, never during bootstrap.
* ``withdrawn``/``restored`` on explicit source status transitions.
* Reclassification and bookkeeping-only changes create no events.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.repository import new_id
from proof_index.ingest.hooks import CurrentChange
from proof_index.ingest.promote import version_number
from proof_index.models.source import NormalizedRecord, Tombstone
from proof_index.resolve.canonical import RebuildResult

BOOTSTRAP_KINDS = {"bootstrap", "snapshot"}


def _upsert_event(
    conn: Connection,
    *,
    key: str,
    work_id: uuid.UUID,
    kind: str,
    event_date: date | None,
    basis: str,
    feed: bool,
    clock: Clock,
    observation_id: uuid.UUID | None = None,
    source_record_id: uuid.UUID | None = None,
    command_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    correctable: bool = False,
) -> None:
    values = {
        "work_id": work_id,
        "kind": kind,
        "event_date": event_date,
        "date_basis": basis if event_date is not None else "none",
        "feed_eligible": feed,
        "observation_id": observation_id,
        "source_record_id": source_record_id,
        "curation_command_id": command_id,
        "details": details or {},
    }
    stmt = pg_insert(t.work_events).values(
        id=new_id(), event_key=key, created_at=clock.now(), **values
    )
    if correctable:
        stmt = stmt.on_conflict_do_update(index_elements=["event_key"], set_=values)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_key"])
    conn.execute(stmt)


def sync_publication_event(
    conn: Connection, work_id: uuid.UUID, canonical: Any, clock: Clock
) -> None:
    exact = canonical.published.exact()
    _upsert_event(
        conn,
        key=f"publication:{work_id}",
        work_id=work_id,
        kind="publication",
        event_date=exact,
        basis="source",
        feed=exact is not None,
        clock=clock,
        details={"published": canonical.published.model_dump(mode="json")},
        correctable=True,
    )


def _status(item: NormalizedRecord | Tombstone) -> str:
    return "deleted" if isinstance(item, Tombstone) else item.status


def _source_date(item: NormalizedRecord | Tombstone) -> date | None:
    stamp: datetime | None = getattr(item, "source_modified_at", None) or item.harvest_datestamp
    return stamp.date() if stamp is not None else None


def _previous_item(
    conn: Connection, observation_id: uuid.UUID | None
) -> NormalizedRecord | Tombstone | None:
    if observation_id is None:
        return None
    row = conn.execute(
        select(t.source_observations.c.normalized, t.source_observations.c.item_kind).where(
            t.source_observations.c.id == observation_id
        )
    ).first()
    if row is None:
        return None
    if row.item_kind == "tombstone":
        return Tombstone.model_validate(row.normalized)
    return NormalizedRecord.model_validate(row.normalized)


class EventRecorder:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def on_change(
        self,
        conn: Connection,
        change: CurrentChange,
        work_id: uuid.UUID,
        attached_to_existing: bool,
        rebuild: RebuildResult,
    ) -> None:
        if rebuild.canonical is not None:
            sync_publication_event(conn, work_id, rebuild.canonical, self.clock)
        bootstrap = change.stream_kind in BOOTSTRAP_KINDS or change.run_mode == "backfill"
        observed = change.observed_at.date()
        if attached_to_existing:
            _upsert_event(
                conn,
                key=f"source_added:{change.source_record_id}",
                work_id=work_id,
                kind="source_added",
                event_date=observed,
                basis="observed",
                feed=False,
                clock=self.clock,
                observation_id=change.observation_id,
                source_record_id=change.source_record_id,
                details={"source": change.item.source, "source_id": change.item.source_id},
            )
        previous = _previous_item(conn, change.previous_observation_id)
        if previous is None:
            return
        new, old = change.item, previous
        key_suffix = str(change.observation_id)
        ref = {"source": new.source, "source_id": new.source_id}

        old_status, new_status = _status(old), _status(new)
        if old_status != new_status:
            kind = "restored" if new_status == "active" else "withdrawn"
            source_date = _source_date(new)
            if source_date is not None or not bootstrap:
                _upsert_event(
                    conn,
                    key=f"{kind}:{key_suffix}",
                    work_id=work_id,
                    kind=kind,
                    event_date=source_date or observed,
                    basis="source" if source_date else "observed",
                    feed=True,
                    clock=self.clock,
                    observation_id=change.observation_id,
                    source_record_id=change.source_record_id,
                    details={**ref, "from": old_status, "to": new_status},
                )
            return
        if not isinstance(new, NormalizedRecord) or not isinstance(old, NormalizedRecord):
            return

        new_v, old_v = version_number(new.source_version), version_number(old.source_version)
        version_advanced = new_v is not None and (old_v is None or new_v > old_v)
        modified_advanced = (
            new.source_modified_at is not None
            and old.source_modified_at is not None
            and new.source_modified_at > old.source_modified_at
            and new.source_version is None
            and old.source_version is None
            and new.source == "arxiv"  # arXiv's `updated` marks a new version (D012)
        )
        if version_advanced or modified_advanced:
            source_date = new.source_modified_at.date() if new.source_modified_at else None
            if source_date is None and bootstrap:
                return
            _upsert_event(
                conn,
                key=f"source_revision:{key_suffix}",
                work_id=work_id,
                kind="source_revision",
                event_date=source_date or observed,
                basis="source" if source_date else "observed",
                feed=True,
                clock=self.clock,
                observation_id=change.observation_id,
                source_record_id=change.source_record_id,
                details={
                    **ref,
                    "from_version": old.source_version,
                    "to_version": new.source_version,
                },
            )
            return
        if not rebuild.changed:
            return  # no reader-visible change
        source_date = None
        if new.source_modified_at and new.source_modified_at != old.source_modified_at:
            source_date = new.source_modified_at.date()
        if source_date is None and bootstrap:
            return
        _upsert_event(
            conn,
            key=f"metadata_updated:{key_suffix}",
            work_id=work_id,
            kind="metadata_updated",
            event_date=source_date or observed,
            basis="source" if source_date else "observed",
            feed=True,
            clock=self.clock,
            observation_id=change.observation_id,
            source_record_id=change.source_record_id,
            details=ref,
        )


def record_curation_event(
    conn: Connection,
    work_id: uuid.UUID,
    command_row_id: uuid.UUID,
    command_id: str,
    action: str,
    clock: Clock,
) -> None:
    _upsert_event(
        conn,
        key=f"curation:{command_id}:{work_id}",
        work_id=work_id,
        kind="curation",
        event_date=clock.today(),
        basis="observed",
        feed=False,
        clock=clock,
        command_id=command_row_id,
        details={"command_id": command_id, "action": action},
    )
