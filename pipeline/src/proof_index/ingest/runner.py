"""Resumable harvest runner (spec section 8).

State machine::

    planned -> fetching -> staged -> promoting -> succeeded
    fetching -> paused -> fetching      promoting -> paused -> promoting
    fetching -> failed                  promoting -> failed

Pages become durable one at a time; a crash or budget stop resumes from the
last durable cursor. Watermarks and success timestamps change only on
``succeeded``. No SQL transaction is open during network requests or sleeps.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.adapters.base import (
    Checkpoint,
    ExpiredCursor,
    HarvestRequest,
    ParseContext,
    ParsedPage,
    SourceAdapter,
)
from proof_index.adapters.registry import adapter_for
from proof_index.clock import Clock
from proof_index.config.sources import SourcesConfig, StreamConfig
from proof_index.db import tables as t
from proof_index.db.locks import CATALOG_WRITER, advisory_lock, provider_lock_name
from proof_index.db.repository import new_id, transaction
from proof_index.errors import (
    ConfigError,
    DataValidationError,
    LockHeldError,
    ProofIndexError,
    ProviderError,
)
from proof_index.hashing import sha256_hex
from proof_index.http.client import ProviderHttpClient
from proof_index.http.errors import BudgetExhausted, HttpStatusError, TransportFailure
from proof_index.http.rate_limit import Budget
from proof_index.ingest.hooks import NoopHooks, PromotionHooks
from proof_index.ingest.promote import promote_run
from proof_index.ingest.staging import stage_page
from proof_index.storage.base import BlobRef, BlobStore, raw_response_key

RESUMABLE_STATES = ("planned", "fetching", "paused", "staged", "promoting")
MAX_CURSOR_REPLAYS = 3
DEFAULT_RESERVE_S = 120.0

HttpFactory = Callable[[str, Budget], ProviderHttpClient]


class RunFailure(DataValidationError):
    """Run-level invariant failure (cursor loop, count mismatch, ...)."""

    code = "run_invariant"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RunSummary:
    stream_key: str
    run_id: str | None
    state: str
    complete: bool
    exit_code: int
    pages_fetched: int = 0
    records_staged: int = 0
    records_promoted: int = 0
    records_quarantined: int = 0
    error_code: str | None = None
    message: str | None = None
    window_from: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FaultInjection:
    """Test-only crash points (simulate a process dying between durable steps)."""

    crash_after_pages: int | None = None
    crash_after_promotion_batches: int | None = None


class HarvestRunner:
    def __init__(
        self,
        engine: Engine,
        blobs: BlobStore,
        config: SourcesConfig,
        clock: Clock,
        http_factory: HttpFactory,
        *,
        hooks: PromotionHooks | None = None,
        adapter_factory: Callable[[str, SourcesConfig], SourceAdapter] = adapter_for,
        strict_totals: bool = True,
        reserve_s: float = DEFAULT_RESERVE_S,
        batch_size: int = 250,
    ) -> None:
        self.engine = engine
        self.blobs = blobs
        self.config = config
        self.clock = clock
        self.http_factory = http_factory
        self.hooks: PromotionHooks = hooks or NoopHooks()
        self.adapter_factory = adapter_factory
        self.strict_totals = strict_totals
        self.reserve_s = reserve_s
        self.batch_size = batch_size

    # -- public ---------------------------------------------------------------

    def run_stream(
        self,
        stream_key: str,
        mode: str,
        budget: Budget,
        *,
        faults: FaultInjection | None = None,
        request_params: dict[str, Any] | None = None,
    ) -> RunSummary:
        stream = self.config.stream(stream_key)
        if not stream.enabled:
            raise ConfigError(f"stream {stream_key} is disabled")
        try:
            with advisory_lock(self.engine, provider_lock_name(stream.provider)):
                return self._run_locked(
                    stream, mode, budget, faults or FaultInjection(), request_params
                )
        except LockHeldError as exc:
            return RunSummary(
                stream_key,
                None,
                "not_started",
                False,
                exc.exit_code,
                error_code=exc.code,
                message=str(exc),
            )

    # -- internals -----------------------------------------------------------------

    def _run_locked(
        self,
        stream: StreamConfig,
        mode: str,
        budget: Budget,
        faults: FaultInjection,
        request_params: dict[str, Any] | None = None,
    ) -> RunSummary:
        adapter = self.adapter_factory(stream.adapter, self.config)
        stream_id = self._ensure_stream(stream)
        run_id = self._resumable_or_new_run(stream, stream_id, mode, request_params)
        try:
            state = self._run_state(run_id)
            if state in ("planned", "fetching") or (
                state == "paused" and self._paused_phase(run_id) == "fetching"
            ):
                if not self._fetch(stream, adapter, run_id, budget, faults):
                    return self._summary(stream, run_id)
            if self._run_state(run_id) in ("staged", "promoting", "paused"):
                if not self._promote(stream, adapter, run_id, budget, faults):
                    return self._summary(stream, run_id)
            self._finalize(stream, stream_id, run_id)
            return self._summary(stream, run_id)
        except BudgetExhausted as exc:
            phase = "promoting" if self._run_state(run_id) == "promoting" else "fetching"
            self._pause(run_id, phase, exc.code)
            return self._summary(stream, run_id)
        except (TransportFailure, HttpStatusError) as exc:
            if isinstance(exc, HttpStatusError) and exc.status not in (429, 500, 502, 503, 504):
                self._fail(stream_id, run_id, exc)
            else:
                # Network trouble after retries: keep durable progress, resume later.
                self._pause(run_id, "fetching", exc.code, str(exc))
            summary = self._summary(stream, run_id)
            summary.exit_code = exc.exit_code
            return summary
        except ProofIndexError as exc:
            self._fail(stream_id, run_id, exc)
            summary = self._summary(stream, run_id)
            summary.exit_code = exc.exit_code
            return summary

    def _ensure_stream(self, stream: StreamConfig) -> uuid.UUID:
        config_hash = stream.config_hash()
        with transaction(self.engine) as conn:
            conn.execute(
                pg_insert(t.provider_streams)
                .values(
                    id=new_id(),
                    provider=stream.provider,
                    stream_key=stream.key,
                    config_hash=config_hash,
                    created_at=self.clock.now(),
                )
                .on_conflict_do_nothing(index_elements=["stream_key"])
            )
            row = conn.execute(
                select(t.provider_streams.c.id, t.provider_streams.c.config_hash).where(
                    t.provider_streams.c.stream_key == stream.key
                )
            ).one()
        if row.config_hash != config_hash:
            raise ConfigError(
                f"stream {stream.key} configuration changed (hash {row.config_hash[:12]} -> "
                f"{config_hash[:12]}); create a new stream key (:vN+1) instead of reusing cursors"
            )
        return row.id  # type: ignore[no-any-return]

    def _resumable_or_new_run(
        self,
        stream: StreamConfig,
        stream_id: uuid.UUID,
        mode: str,
        request_params: dict[str, Any] | None = None,
        snapshot_key: str | None = None,
    ) -> uuid.UUID:
        now = self.clock.now()
        with transaction(self.engine) as conn:
            existing = conn.execute(
                select(t.harvest_runs.c.id)
                .where(t.harvest_runs.c.stream_id == stream_id)
                .where(t.harvest_runs.c.state.in_(RESUMABLE_STATES))
                .where(t.harvest_runs.c.config_hash == stream.config_hash())
                .order_by(t.harvest_runs.c.started_at.desc())
                .limit(1)
                .with_for_update()
            ).first()
            if existing is not None:
                existing_row = conn.execute(
                    select(t.harvest_runs).where(t.harvest_runs.c.id == existing.id)
                ).one()
                if snapshot_key is None or existing_row.snapshot_key == snapshot_key:
                    return existing.id  # type: ignore[no-any-return]
                # A changed snapshot is a new logical snapshot, never a continuation.
                self._set_state(
                    conn,
                    existing.id,
                    "failed",
                    finished_at=now,
                    error_code="superseded_snapshot",
                    error_summary=(
                        f"snapshot {existing_row.snapshot_key} superseded by {snapshot_key}"
                    ),
                )
            stream_row = conn.execute(
                select(t.provider_streams).where(t.provider_streams.c.id == stream_id)
            ).one()
            window_from: datetime | None = None
            if stream.kind in ("incremental", "supplemental") and stream_row.completed_watermark:
                window_from = stream_row.completed_watermark - timedelta(days=stream.overlap_days)
            if stream.kind == "bootstrap":
                self._capture_incremental_boundary(conn, stream, now)
            run_id = new_id()
            conn.execute(
                t.harvest_runs.insert().values(
                    id=run_id,
                    stream_id=stream_id,
                    mode=mode,
                    state="planned",
                    window_from=window_from,
                    window_until=None,
                    started_at=now,
                    config_hash=stream.config_hash(),
                    request_params=request_params,
                    snapshot_key=snapshot_key,
                )
            )
            conn.execute(
                update(t.provider_streams)
                .where(t.provider_streams.c.id == stream_id)
                .values(last_attempt_at=now)
            )
            self._upsert_coverage(conn, stream_id, run_id, "running", None)
        return run_id

    def _capture_incremental_boundary(
        self, conn: Connection, bootstrap: StreamConfig, now: datetime
    ) -> None:
        """Before a bootstrap starts, fix the paired incremental stream's start boundary."""
        for paired in self.config.streams:
            if paired.pairs_with != bootstrap.key:
                continue
            conn.execute(
                pg_insert(t.provider_streams)
                .values(
                    id=new_id(),
                    provider=paired.provider,
                    stream_key=paired.key,
                    config_hash=paired.config_hash(),
                    created_at=now,
                    completed_watermark=now,
                    checkpoint={"initial_boundary": now.isoformat(), "bootstrap": bootstrap.key},
                )
                .on_conflict_do_nothing(index_elements=["stream_key"])
            )
            # Never rewind an incremental watermark that is already set.
            conn.execute(
                update(t.provider_streams)
                .where(t.provider_streams.c.stream_key == paired.key)
                .where(t.provider_streams.c.completed_watermark.is_(None))
                .values(
                    completed_watermark=now,
                    checkpoint={"initial_boundary": now.isoformat(), "bootstrap": bootstrap.key},
                )
            )

    def _run_state(self, run_id: uuid.UUID) -> str:
        with self.engine.connect() as conn:
            return str(
                conn.execute(
                    select(t.harvest_runs.c.state).where(t.harvest_runs.c.id == run_id)
                ).scalar_one()
            )

    def _paused_phase(self, run_id: uuid.UUID) -> str | None:
        with self.engine.connect() as conn:
            value = conn.execute(
                select(t.harvest_runs.c.paused_phase).where(t.harvest_runs.c.id == run_id)
            ).scalar_one()
        return None if value is None else str(value)

    def _set_state(self, conn: Connection, run_id: uuid.UUID, state: str, **values: Any) -> None:
        conn.execute(
            update(t.harvest_runs)
            .where(t.harvest_runs.c.id == run_id)
            .values(state=state, paused_phase=values.pop("paused_phase", None), **values)
        )

    # -- fetch phase -----------------------------------------------------------------

    def _fetch(
        self,
        stream: StreamConfig,
        adapter: SourceAdapter,
        run_id: uuid.UUID,
        budget: Budget,
        faults: FaultInjection,
    ) -> bool:
        """Fetch until terminal. Returns False when paused (budget)."""
        with transaction(self.engine) as conn:
            self._set_state(conn, run_id, "fetching")
            run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).one()
        request = HarvestRequest(
            stream_key=stream.key,
            mode=run.mode,
            window_from=run.window_from,
            window_until=run.window_until,
            params=run.request_params or {},
        )
        plan = adapter.plan(request, Checkpoint())
        pages_this_call = 0
        with self.http_factory(stream.provider, budget) as http:
            while True:
                epoch, last, seen_cursors, next_ordinal = self._fetch_position(run_id)
                if last is not None and last.is_terminal:
                    break
                if budget.nearly_exhausted(self.reserve_s):
                    self._pause(run_id, "fetching", "budget")
                    return False
                cursor = last.next_cursor if last is not None else None
                spec = adapter.request_page(plan, cursor)
                response = http.fetch(spec)
                digest = sha256_hex(response.body)
                blob = self.blobs.put_if_absent(
                    raw_response_key(stream.provider, digest), response.body, digest
                )
                context = ParseContext(
                    provider=stream.provider,
                    stream_key=stream.key,
                    parser_version=adapter.parser_version,
                    fetched_at=response.fetched_at,
                    plan_params=plan.params,
                    cursor=cursor,
                    http_status=response.status,
                )
                try:
                    page = adapter.parse_page(response.body, context)
                except ExpiredCursor:
                    self._replay_window(run_id, epoch)
                    continue
                if page.next_cursor is not None and _cursor_key(page.next_cursor) in seen_cursors:
                    raise RunFailure(
                        "repeated_cursor",
                        f"cursor repeated at page {next_ordinal}; refusing to loop",
                    )
                with transaction(self.engine) as conn:
                    stage_page(
                        conn,
                        run_id=run_id,
                        ordinal=next_ordinal,
                        epoch=epoch,
                        request_fingerprint=spec.fingerprint(),
                        blob=blob,
                        page=page,
                        fetched_at=response.fetched_at,
                    )
                pages_this_call += 1
                if (
                    faults.crash_after_pages is not None
                    and pages_this_call >= faults.crash_after_pages
                ):
                    raise RuntimeError("injected crash after durable page (test)")
        self._validate_fetch(run_id)
        with transaction(self.engine) as conn:
            first = conn.execute(
                select(t.harvest_pages.c.provider_response_at)
                .where(t.harvest_pages.c.run_id == run_id)
                .where(t.harvest_pages.c.epoch == epoch)
                .order_by(t.harvest_pages.c.ordinal)
                .limit(1)
            ).scalar_one()
            self._set_state(conn, run_id, "staged", response_time=first or run.started_at)
        return True

    def _fetch_position(self, run_id: uuid.UUID) -> tuple[int, Any, set[str], int]:
        with self.engine.connect() as conn:
            epoch = conn.execute(
                select(t.harvest_runs.c.cursor_epoch).where(t.harvest_runs.c.id == run_id)
            ).scalar_one()
            pages = conn.execute(
                select(
                    t.harvest_pages.c.next_cursor,
                    t.harvest_pages.c.is_terminal,
                    t.harvest_pages.c.ordinal,
                )
                .where(t.harvest_pages.c.run_id == run_id)
                .where(t.harvest_pages.c.epoch == epoch)
                .order_by(t.harvest_pages.c.ordinal)
            ).all()
            max_ordinal = conn.execute(
                select(func.max(t.harvest_pages.c.ordinal)).where(
                    t.harvest_pages.c.run_id == run_id
                )
            ).scalar_one()
        seen = {_cursor_key(p.next_cursor) for p in pages if p.next_cursor is not None}
        last = pages[-1] if pages else None
        return int(epoch), last, seen, 0 if max_ordinal is None else int(max_ordinal) + 1

    def _replay_window(self, run_id: uuid.UUID, epoch: int) -> None:
        """Expired cursor: discard it and replay the run's fixed window from the start."""
        if epoch + 1 > MAX_CURSOR_REPLAYS:
            raise RunFailure("cursor_replays_exhausted", "resumption cursor expired repeatedly")
        with transaction(self.engine) as conn:
            conn.execute(
                update(t.harvest_runs)
                .where(t.harvest_runs.c.id == run_id)
                .values(cursor_epoch=epoch + 1)
            )

    def _validate_fetch(self, run_id: uuid.UUID) -> None:
        with self.engine.connect() as conn:
            epoch = conn.execute(
                select(t.harvest_runs.c.cursor_epoch).where(t.harvest_runs.c.id == run_id)
            ).scalar_one()
            pages = conn.execute(
                select(t.harvest_pages)
                .where(t.harvest_pages.c.run_id == run_id)
                .where(t.harvest_pages.c.epoch == epoch)
                .order_by(t.harvest_pages.c.ordinal)
            ).all()
        if not pages or not pages[-1].is_terminal:
            raise RunFailure("not_terminal", "fetch ended without a terminal page")
        if not self.strict_totals:
            return
        # Totals are per partition (e.g. one arXiv Atom query); a page's request
        # cursor is the previous page's next cursor.
        partitions: dict[Any, list[Any]] = {}
        request_cursor: dict[str, Any] | None = None
        for page in pages:
            key = (request_cursor or {}).get("query_index", 0)
            partitions.setdefault(key, []).append(page)
            request_cursor = page.next_cursor
        for key, group in partitions.items():
            totals = {p.reported_total for p in group if p.reported_total is not None}
            if not totals:
                continue
            items = sum(p.parsed_count + p.quarantined_count + p.filtered_count for p in group)
            if len(totals) > 1 or totals.pop() != items:
                raise RunFailure(
                    "count_mismatch",
                    f"partition {key}: provider totals disagree with {items} items received",
                )

    # -- promotion phase --------------------------------------------------------------

    def _promote(
        self,
        stream: StreamConfig,
        adapter: SourceAdapter,
        run_id: uuid.UUID,
        budget: Budget,
        faults: FaultInjection,
    ) -> bool:
        with advisory_lock(self.engine, CATALOG_WRITER):
            with transaction(self.engine) as conn:
                run = conn.execute(
                    select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)
                ).one()
                self._set_state(
                    conn,
                    run_id,
                    "promoting",
                    promotion_started_at=run.promotion_started_at or self.clock.now(),
                )
            result = promote_run(
                self.engine,
                run_id,
                adapter=adapter.name,
                parser_version=adapter.parser_version,
                stream_kind=stream.kind,
                clock=self.clock,
                hooks=self.hooks,
                budget=budget,
                reserve_s=self.reserve_s,
                batch_size=self.batch_size,
                fail_after_batches=faults.crash_after_promotion_batches,
            )
        if result.paused:
            self._pause(run_id, "promoting", "budget")
            return False
        return True

    def _finalize(self, stream: StreamConfig, stream_id: uuid.UUID, run_id: uuid.UUID) -> None:
        now = self.clock.now()
        with transaction(self.engine) as conn:
            pending = conn.execute(
                select(func.count())
                .select_from(t.staged_records)
                .where(t.staged_records.c.run_id == run_id)
                .where(t.staged_records.c.state == "pending")
            ).scalar_one()
            if pending:
                raise RunFailure("pending_rows", f"{pending} staged rows remain unpromoted")
            run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).one()
            self._set_state(conn, run_id, "succeeded", finished_at=now)
            stream_row = conn.execute(
                select(t.provider_streams).where(t.provider_streams.c.id == stream_id)
            ).one()
            boundary = run.response_time or run.started_at
            watermark = stream_row.completed_watermark
            if watermark is None or boundary > watermark:
                watermark = boundary
            conn.execute(
                update(t.provider_streams)
                .where(t.provider_streams.c.id == stream_id)
                .values(completed_watermark=watermark, last_success_at=now, last_error_code=None)
            )
            epoch = run.cursor_epoch
            totals = conn.execute(
                select(
                    func.coalesce(func.sum(t.harvest_pages.c.parsed_count), 0),
                    func.coalesce(func.sum(t.harvest_pages.c.quarantined_count), 0),
                    func.coalesce(func.sum(t.harvest_pages.c.filtered_count), 0),
                    func.max(t.harvest_pages.c.reported_total),
                )
                .where(t.harvest_pages.c.run_id == run_id)
                .where(t.harvest_pages.c.epoch == epoch)
            ).one()
            parsed, quarantined, filtered, reported = totals
            self._upsert_coverage(
                conn,
                stream_id,
                run_id,
                "complete",
                {
                    "expected_count": reported,
                    "seen_count": int(parsed) + int(quarantined) + int(filtered),
                    "valid_count": int(parsed),
                    "quarantined_count": int(quarantined),
                    "notes": {
                        "kind": stream.kind,
                        "window_from": run.window_from.isoformat() if run.window_from else None,
                        "filtered_by_local_rule": int(filtered),
                        "boundary": boundary.isoformat(),
                    },
                },
            )

    def _upsert_coverage(
        self,
        conn: Connection,
        stream_id: uuid.UUID,
        run_id: uuid.UUID,
        state: str,
        counts: dict[str, Any] | None,
    ) -> None:
        values: dict[str, Any] = {"state": state, "run_id": run_id, "updated_at": self.clock.now()}
        if counts:
            values.update(counts)
        conn.execute(
            pg_insert(t.coverage_slices)
            .values(id=new_id(), stream_id=stream_id, slice_key="stream", **values)
            .on_conflict_do_update(index_elements=["stream_id", "slice_key"], set_=values)
        )

    # -- local snapshots (DBLP dump, curated file) -----------------------------------

    def run_snapshot(
        self,
        stream_key: str,
        mode: str,
        budget: Budget,
        *,
        snapshot_key: str,
        blob: BlobRef,
        batches: Callable[[int], Iterator[ParsedPage]],
        adapter_name: str,
        parser_version: str,
        faults: FaultInjection | None = None,
    ) -> RunSummary:
        """Stage a local snapshot in bounded batches, then promote it.

        ``batches(after_ordinal)`` re-streams the snapshot and yields pages for items
        after ``after_ordinal`` (``-1`` at the start). Each page's ``next_cursor`` is
        ``{"after_ordinal": n}``; the final page has none. A different
        ``snapshot_key`` starts a new logical snapshot instead of continuing.
        """
        stream = self.config.stream(stream_key)
        faults = faults or FaultInjection()
        try:
            with advisory_lock(self.engine, provider_lock_name(stream.provider)):
                stream_id = self._ensure_stream(stream)
                run_id = self._resumable_or_new_run(
                    stream, stream_id, mode, snapshot_key=snapshot_key
                )
                adapter = _SnapshotAdapter(adapter_name, parser_version)
                try:
                    state = self._run_state(run_id)
                    if state in ("planned", "fetching") or (
                        state == "paused" and self._paused_phase(run_id) == "fetching"
                    ):
                        if not self._stage_snapshot(run_id, blob, batches, budget, faults):
                            return self._summary(stream, run_id)
                    if self._run_state(run_id) in ("staged", "promoting", "paused"):
                        if not self._promote(stream, adapter, run_id, budget, faults):  # type: ignore[arg-type]
                            return self._summary(stream, run_id)
                    self._finalize(stream, stream_id, run_id)
                    return self._summary(stream, run_id)
                except ProofIndexError as exc:
                    self._fail(stream_id, run_id, exc)
                    summary = self._summary(stream, run_id)
                    summary.exit_code = exc.exit_code
                    return summary
        except LockHeldError as exc:
            return RunSummary(
                stream_key,
                None,
                "not_started",
                False,
                exc.exit_code,
                error_code=exc.code,
                message=str(exc),
            )

    def _stage_snapshot(
        self,
        run_id: uuid.UUID,
        blob: BlobRef,
        batches: Callable[[int], Iterator[ParsedPage]],
        budget: Budget,
        faults: FaultInjection,
    ) -> bool:
        with transaction(self.engine) as conn:
            self._set_state(conn, run_id, "fetching")
        epoch, last, _seen, next_ordinal = self._fetch_position(run_id)
        if last is not None and last.is_terminal:
            pass
        else:
            after = int(last.next_cursor["after_ordinal"]) if last is not None else -1
            pages = 0
            for page in batches(after):
                with transaction(self.engine) as conn:
                    stage_page(
                        conn,
                        run_id=run_id,
                        ordinal=next_ordinal,
                        epoch=epoch,
                        request_fingerprint=blob.sha256,
                        blob=blob,
                        page=page,
                        fetched_at=self.clock.now(),
                    )
                next_ordinal += 1
                pages += 1
                if faults.crash_after_pages is not None and pages >= faults.crash_after_pages:
                    raise RuntimeError("injected crash after durable page (test)")
                if page.terminal:
                    break
                if budget.nearly_exhausted(self.reserve_s):
                    self._pause(run_id, "fetching", "budget")
                    return False
            else:
                raise RunFailure("not_terminal", "snapshot stream ended without a terminal batch")
        with transaction(self.engine) as conn:
            self._set_state(conn, run_id, "staged", response_time=None)
        return True

    # -- terminal states ------------------------------------------------------------------

    def _pause(self, run_id: uuid.UUID, phase: str, code: str, summary: str | None = None) -> None:
        with transaction(self.engine) as conn:
            self._set_state(
                conn,
                run_id,
                "paused",
                paused_phase=phase,
                error_code=code,
                error_summary=summary[:500] if summary else None,
            )
            run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).one()
            self._upsert_coverage(conn, run.stream_id, run_id, "partial", None)

    def _fail(self, stream_id: uuid.UUID, run_id: uuid.UUID, exc: ProofIndexError) -> None:
        now = self.clock.now()
        with transaction(self.engine) as conn:
            self._set_state(
                conn,
                run_id,
                "failed",
                finished_at=now,
                error_code=exc.code,
                error_summary=str(exc)[:500],
            )
            conn.execute(
                update(t.provider_streams)
                .where(t.provider_streams.c.id == stream_id)
                .values(last_error_code=exc.code)
            )
            self._upsert_coverage(conn, stream_id, run_id, "partial", None)

    def _summary(self, stream: StreamConfig, run_id: uuid.UUID) -> RunSummary:
        with self.engine.connect() as conn:
            run = conn.execute(select(t.harvest_runs).where(t.harvest_runs.c.id == run_id)).one()
        complete = run.state == "succeeded"
        return RunSummary(
            stream_key=stream.key,
            run_id=str(run_id),
            state=run.state,
            complete=complete,
            exit_code=0 if run.state in ("succeeded", "paused") else 1,
            pages_fetched=run.pages_fetched,
            records_staged=run.records_staged,
            records_promoted=run.records_promoted,
            records_quarantined=run.records_quarantined,
            error_code=run.error_code,
            message=run.error_summary,
            window_from=run.window_from.isoformat() if run.window_from else None,
        )


class _SnapshotAdapter:
    """Minimal adapter identity for promotion of locally staged snapshots."""

    def __init__(self, name: str, parser_version: str) -> None:
        self.name = name
        self.parser_version = parser_version


def _cursor_key(cursor: dict[str, Any]) -> str:
    from proof_index.hashing import canonical_json

    return canonical_json(cursor)


__all__ = ["FaultInjection", "HarvestRunner", "ProviderError", "RunFailure", "RunSummary"]
