"""Pending-promotion guard and deliberate reconciliation (spec section 8).

A run whose promotion started but did not succeed leaves partial internal
changes. Until it is resumed to completion or deliberately reconciled, no
release may be exported. The condition is durable (``promotion_started_at`` set,
state not ``succeeded``, ``reconciled_at`` null), so a crash cannot permit export.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection, select, update

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.errors import DataValidationError, InputError


class PendingPromotionError(DataValidationError):
    code = "pending_promotion"


def pending_promotions(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(
            t.harvest_runs.c.id,
            t.harvest_runs.c.state,
            t.harvest_runs.c.promotion_started_at,
            t.provider_streams.c.stream_key,
        )
        .select_from(
            t.harvest_runs.join(
                t.provider_streams, t.provider_streams.c.id == t.harvest_runs.c.stream_id
            )
        )
        .where(t.harvest_runs.c.promotion_started_at.is_not(None))
        .where(t.harvest_runs.c.state != "succeeded")
        .where(t.harvest_runs.c.reconciled_at.is_(None))
        .order_by(t.harvest_runs.c.promotion_started_at)
    ).all()
    return [
        {
            "run_id": str(r.id),
            "state": r.state,
            "stream_key": r.stream_key,
            "promotion_started_at": r.promotion_started_at.isoformat(),
        }
        for r in rows
    ]


def assert_export_allowed(conn: Connection) -> None:
    pending = pending_promotions(conn)
    if pending:
        runs = ", ".join(f"{p['stream_key']} run {p['run_id']} ({p['state']})" for p in pending)
        raise PendingPromotionError(
            f"export blocked: partial promotion pending for {runs}. Resume the run "
            "(backfill/sync --resume) or reconcile it deliberately."
        )


def mark_reconciled(conn: Connection, run_id: uuid.UUID, clock: Clock) -> None:
    """Deliberately accept a failed run's partial changes (operator action)."""
    run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).first()
    if run is None:
        raise InputError(f"run {run_id} not found")
    if run.state != "failed":
        raise InputError("only failed runs can be reconciled; resume paused runs instead")
    conn.execute(
        update(t.harvest_runs)
        .where(t.harvest_runs.c.id == run_id)
        .values(reconciled_at=clock.now())
    )
