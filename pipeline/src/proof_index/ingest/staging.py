"""Durable page staging (spec section 8, step 3d).

One short transaction inserts page metadata, staged records, quarantine details,
and the page's next cursor. Inserts are idempotent so a replayed page cannot
duplicate staged rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Connection, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.adapters.base import ParsedPage
from proof_index.db import tables as t
from proof_index.db.repository import new_id
from proof_index.models.source import Tombstone
from proof_index.storage.base import BlobRef


def stage_page(
    conn: Connection,
    *,
    run_id: uuid.UUID,
    ordinal: int,
    epoch: int,
    request_fingerprint: str,
    blob: BlobRef,
    page: ParsedPage,
    fetched_at: datetime,
) -> tuple[uuid.UUID, int]:
    """Insert the page and its items. Returns (page_id, newly staged row count)."""
    page_id = new_id()
    conn.execute(
        t.harvest_pages.insert().values(
            id=page_id,
            run_id=run_id,
            ordinal=ordinal,
            epoch=epoch,
            request_fingerprint=request_fingerprint,
            blob_key=blob.key,
            raw_sha256=blob.sha256,
            next_cursor=page.next_cursor,
            is_terminal=page.terminal,
            parsed_count=len(page.records),
            quarantined_count=len(page.quarantined_items),
            filtered_count=page.filtered_count,
            reported_total=page.reported_total,
            provider_response_at=page.provider_response_at,
            state="durable",
            fetched_at=fetched_at,
        )
    )
    staged = 0
    for item_ordinal, item in enumerate(page.records):
        is_tomb = isinstance(item, Tombstone)
        result = conn.execute(
            pg_insert(t.staged_records)
            .values(
                id=new_id(),
                run_id=run_id,
                page_id=page_id,
                source=item.source,
                source_id=item.source_id,
                payload=item.model_dump(mode="json"),
                content_sha256=item.content_sha256(),
                item_kind="tombstone" if is_tomb else "record",
                state="pending",
                item_ordinal=item_ordinal,
            )
            .on_conflict_do_nothing(
                index_elements=["run_id", "source", "source_id", "content_sha256"]
            )
        )
        staged += result.rowcount or 0
    for failure in page.quarantined_items:
        conn.execute(
            t.quarantine_items.insert().values(
                id=new_id(),
                run_id=run_id,
                page_id=page_id,
                source=failure.source,
                source_id=failure.source_id,
                item_ordinal=failure.item_ordinal,
                error_code=failure.error_code,
                message=failure.message,
                raw_excerpt=failure.raw_excerpt,
                created_at=fetched_at,
            )
        )
    conn.execute(
        update(t.harvest_runs)
        .where(t.harvest_runs.c.id == run_id)
        .values(
            pages_fetched=t.harvest_runs.c.pages_fetched + 1,
            records_staged=t.harvest_runs.c.records_staged + staged,
            records_quarantined=t.harvest_runs.c.records_quarantined + len(page.quarantined_items),
        )
    )
    return page_id, staged
