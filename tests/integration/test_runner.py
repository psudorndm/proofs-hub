"""Harvest runner recovery behavior on real PostgreSQL (spec sections 8 and 18.2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, Engine, func, select

from proof_index.adapters.base import (
    Checkpoint,
    Cursor,
    HarvestRequest,
    ParseContext,
    ParsedPage,
    StreamPlan,
)
from proof_index.adapters.registry import adapter_for
from proof_index.clock import FixedClock
from proof_index.config.sources import SourcesConfig, load_sources
from proof_index.db import tables as t
from proof_index.db.locks import advisory_lock, provider_lock_name
from proof_index.errors import ConfigError
from proof_index.http.client import ProviderHttpClient, RequestSpec
from proof_index.http.rate_limit import Budget, RateLimiter
from proof_index.ingest.hooks import CurrentChange
from proof_index.ingest.reconcile import PendingPromotionError, assert_export_allowed
from proof_index.ingest.runner import FaultInjection, HarvestRunner
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author, NormalizedRecord
from proof_index.storage.local import LocalBlobStore

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "eprint"
BOOT = "eprint:all:bootstrap:v1"
INCR = "eprint:all:incremental:v1"


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class RecordingHooks:
    def __init__(self) -> None:
        self.changes: list[CurrentChange] = []

    def current_changed(self, conn: Connection, change: CurrentChange) -> None:
        self.changes.append(change)


def make_runner(
    engine: Engine,
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: SourcesConfig | None = None,
    hooks: RecordingHooks | None = None,
    adapter_factory: Any = adapter_for,
    batch_size: int = 250,
    clock: FixedClock | None = None,
) -> tuple[HarvestRunner, FakeTime]:
    ft = FakeTime()
    cfg = config or load_sources(ROOT / "config" / "sources.yaml")
    clk = clock or FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))

    def http_factory(provider: str, budget: Budget) -> ProviderHttpClient:
        return ProviderHttpClient(
            user_agent="test",
            limiter=RateLimiter(0.0, sleep=ft.sleep, monotonic=ft.monotonic),
            budget=budget,
            transport=httpx.MockTransport(handler),
            sleep=ft.sleep,
            monotonic=ft.monotonic,
            clock=clk,
        )

    runner = HarvestRunner(
        engine,
        LocalBlobStore(tmp_path / "blobs"),
        cfg,
        clk,
        http_factory,
        hooks=hooks,
        adapter_factory=adapter_factory,
        reserve_s=0.0,
        batch_size=batch_size,
    )
    return runner, ft


def oai_handler(
    log: list[str], pages: dict[str | None, str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("resumptionToken")
        log.append(token or "start")
        name = pages[token]
        return httpx.Response(200, content=(FIX / name).read_bytes())

    return handle


NORMAL = {
    None: "synthetic-listrecords-page1.xml",
    "synthetic-token-1": "synthetic-listrecords-page2.xml",
}


def count(conn: Connection, table: Any, *where: Any) -> int:
    q = select(func.count()).select_from(table)
    for w in where:
        q = q.where(w)
    return int(conn.execute(q).scalar_one())


def stream_row(conn: Connection, key: str) -> Any:
    return conn.execute(
        select(t.provider_streams).where(t.provider_streams.c.stream_key == key)
    ).one()


def budget() -> Budget:
    return Budget(None)


# --- happy path ------------------------------------------------------------------------


def test_full_bootstrap_run_promotes_and_advances_watermark(engine: Engine, tmp_path: Path) -> None:
    log: list[str] = []
    hooks = RecordingHooks()
    runner, _ = make_runner(engine, tmp_path, oai_handler(log, NORMAL), hooks=hooks)
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "succeeded" and summary.complete and summary.exit_code == 0
    assert log == ["start", "synthetic-token-1"]
    with engine.connect() as conn:
        assert count(conn, t.source_records) == 3
        assert count(conn, t.source_observations) == 3
        assert count(conn, t.quarantine_items) == 1
        boot = stream_row(conn, BOOT)
        assert boot.completed_watermark == datetime(2026, 11, 3, 6, 17, 5, tzinfo=UTC)
        assert boot.last_success_at is not None
        # The paired incremental boundary was captured before the bootstrap began.
        incr = stream_row(conn, INCR)
        assert incr.completed_watermark == datetime(2026, 11, 3, 12, tzinfo=UTC)
        cov = conn.execute(
            select(t.coverage_slices).where(t.coverage_slices.c.stream_id == boot.id)
        ).one()
        assert cov.state == "complete"
        assert (cov.expected_count, cov.seen_count, cov.valid_count, cov.quarantined_count) == (
            4,
            4,
            3,
            1,
        )
    assert {c.change_kind for c in hooks.changes} == {"initial", "tombstone"}
    assert len(hooks.changes) == 3


def test_oai_no_records_match_is_successful_empty_window(engine: Engine, tmp_path: Path) -> None:
    runner, _ = make_runner(
        engine, tmp_path, oai_handler([], {None: "synthetic-no-records-match.xml"})
    )
    summary = runner.run_stream(INCR, "sync", budget())
    assert summary.state == "succeeded"
    with engine.connect() as conn:
        assert count(conn, t.source_records) == 0
        assert stream_row(conn, INCR).completed_watermark is not None


# --- failures -----------------------------------------------------------------------------


def test_oai_protocol_error_does_not_advance_watermark(engine: Engine, tmp_path: Path) -> None:
    runner, _ = make_runner(engine, tmp_path, oai_handler([], {None: "synthetic-bad-argument.xml"}))
    summary = runner.run_stream(INCR, "sync", budget())
    assert summary.state == "failed" and summary.exit_code == 3
    assert summary.error_code == "protocol_error"
    with engine.connect() as conn:
        row = stream_row(conn, INCR)
        assert row.completed_watermark is None and row.last_success_at is None
        assert row.last_error_code == "protocol_error"


def test_html_page_with_200_fails_run(engine: Engine, tmp_path: Path) -> None:
    runner, _ = make_runner(engine, tmp_path, oai_handler([], {None: "synthetic-html-login.html"}))
    summary = runner.run_stream(INCR, "sync", budget())
    assert summary.state == "failed" and summary.exit_code == 4


def test_repeated_resumption_token_fails_completeness_check(engine: Engine, tmp_path: Path) -> None:
    pages = {
        None: "synthetic-listrecords-page1.xml",
        "synthetic-token-1": "synthetic-listrecords-page1.xml",
    }
    runner, _ = make_runner(engine, tmp_path, oai_handler([], pages))
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "failed" and summary.error_code == "repeated_cursor"
    with engine.connect() as conn:
        assert stream_row(conn, BOOT).completed_watermark is None


def test_count_mismatch_fails(engine: Engine, tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("resumptionToken")
        body = (FIX / NORMAL[token]).read_bytes()
        return httpx.Response(
            200, content=body.replace(b'completeListSize="4"', b'completeListSize="9"')
        )

    runner, _ = make_runner(engine, tmp_path, handle)
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "failed" and summary.error_code == "count_mismatch"


# --- resumption ------------------------------------------------------------------------------


def test_expired_token_replays_window_without_duplicate_works(
    engine: Engine, tmp_path: Path
) -> None:
    log: list[str] = []
    expired = {"done": False}

    def handle(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("resumptionToken")
        log.append(token or "start")
        if token and not expired["done"]:
            expired["done"] = True
            return httpx.Response(
                200, content=(FIX / "synthetic-bad-resumption-token.xml").read_bytes()
            )
        return httpx.Response(200, content=(FIX / NORMAL[token]).read_bytes())

    runner, _ = make_runner(engine, tmp_path, handle)
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "succeeded"
    assert log == ["start", "synthetic-token-1", "start", "synthetic-token-1"]
    with engine.connect() as conn:
        assert count(conn, t.source_records) == 3
        assert count(conn, t.source_observations) == 3
        run = conn.execute(select(t.harvest_runs)).one()
        assert run.cursor_epoch == 1
        assert count(conn, t.staged_records) == 3  # replayed rows were not duplicated


def test_partial_page_write_resumes_from_last_durable_cursor(
    engine: Engine, tmp_path: Path
) -> None:
    log: list[str] = []
    runner, _ = make_runner(engine, tmp_path, oai_handler(log, NORMAL))
    with pytest.raises(RuntimeError, match="injected crash"):
        runner.run_stream(BOOT, "backfill", budget(), faults=FaultInjection(crash_after_pages=1))
    with engine.connect() as conn:
        run = conn.execute(select(t.harvest_runs)).one()
        assert run.state == "fetching" and run.pages_fetched == 1
        assert stream_row(conn, BOOT).completed_watermark is None
    log.clear()
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "succeeded"
    assert log == ["synthetic-token-1"]  # resumed from the durable cursor, not the start
    with engine.connect() as conn:
        assert count(conn, t.harvest_runs) == 1


def test_partial_promotion_blocks_export_until_repaired(engine: Engine, tmp_path: Path) -> None:
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL), batch_size=1)
    with pytest.raises(RuntimeError, match="injected promotion failure"):
        runner.run_stream(
            BOOT, "backfill", budget(), faults=FaultInjection(crash_after_promotion_batches=1)
        )
    with engine.connect() as conn:
        assert count(conn, t.staged_records, t.staged_records.c.state == "promoted") == 1
        with pytest.raises(PendingPromotionError):
            assert_export_allowed(conn)
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "succeeded"
    with engine.connect() as conn:
        assert_export_allowed(conn)
        assert count(conn, t.source_observations) == 3


def test_budget_pause_is_resumable_and_incomplete(engine: Engine, tmp_path: Path) -> None:
    runner, ft = make_runner(engine, tmp_path, oai_handler([], NORMAL))
    tiny = Budget(10, monotonic=ft.monotonic)
    ft.t += 10  # exhausted before the first request
    summary = runner.run_stream(BOOT, "backfill", tiny)
    assert summary.state == "paused" and not summary.complete and summary.exit_code == 0
    with engine.connect() as conn:
        assert stream_row(conn, BOOT).completed_watermark is None
    summary = runner.run_stream(BOOT, "backfill", budget())
    assert summary.state == "succeeded"


def test_transport_failure_pauses_with_provider_exit_code(engine: Engine, tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    runner, _ = make_runner(engine, tmp_path, handle)
    summary = runner.run_stream(INCR, "sync", budget())
    assert summary.state == "paused" and summary.exit_code == 3


def test_writer_or_provider_lock_held_returns_exit_5(
    engine: Engine, tmp_path: Path, db_url: str
) -> None:
    from proof_index.db.repository import make_engine

    other = make_engine(db_url)
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL))
    try:
        with advisory_lock(other, provider_lock_name("eprint")):
            summary = runner.run_stream(BOOT, "backfill", budget())
        assert summary.exit_code == 5 and summary.state == "not_started"
    finally:
        other.dispose()


def test_changed_stream_config_refuses_reuse(engine: Engine, tmp_path: Path) -> None:
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL))
    runner.run_stream(BOOT, "backfill", budget())
    cfg = load_sources(ROOT / "config" / "sources.yaml")
    streams = [
        s.model_copy(update={"params": {**s.params, "set": "x"}}) if s.key == BOOT else s
        for s in cfg.streams
    ]
    changed = cfg.model_copy(update={"streams": streams})
    runner2, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL), config=changed)
    with pytest.raises(ConfigError, match="configuration changed"):
        runner2.run_stream(BOOT, "backfill", budget())


def test_incremental_window_uses_watermark_minus_overlap(engine: Engine, tmp_path: Path) -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("from"))
        return httpx.Response(200, content=(FIX / "synthetic-no-records-match.xml").read_bytes())

    runner, _ = make_runner(engine, tmp_path, handle)
    runner.run_stream(INCR, "sync", budget())  # first sync: no artificial cutoff
    runner.run_stream(INCR, "sync", budget())
    assert seen[0] is None
    assert seen[1] == "2026-11-01"  # responseDate 2026-11-03 minus 2-day overlap


def test_rerun_of_identical_fixture_does_not_create_new_observations(
    engine: Engine, tmp_path: Path
) -> None:
    hooks = RecordingHooks()
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL), hooks=hooks)
    runner.run_stream(BOOT, "backfill", budget())
    first = len(hooks.changes)
    # A second, separate run of the same responses (e.g. an incremental overlap).
    runner.run_stream(INCR, "sync", budget())
    with engine.connect() as conn:
        assert count(conn, t.source_observations) == 3
    assert len(hooks.changes) == first


# --- current selection with a JSON-fed fake adapter ---------------------------------------------


class JsonAdapter:
    """Test adapter: each response body is a JSON list of NormalizedRecord dicts."""

    name = "eprint"
    provider = "eprint"
    parser_version = "json-test-v1"

    def __init__(self, config: SourcesConfig) -> None:
        self.config = config

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan:
        return StreamPlan(
            provider="eprint",
            adapter=self.name,
            stream_key=request.stream_key,
            base_url="https://example.org/json",
        )

    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec:
        return RequestSpec(provider="eprint", url=plan.base_url)

    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage:
        records = [NormalizedRecord.model_validate(r) for r in json.loads(body)]
        return ParsedPage(
            records=records,
            next_cursor=None,
            terminal=True,
            provider_response_at=None,
            reported_total=None,
        )


def rec(title: str, modified: datetime | None = None, version: str | None = None) -> dict[str, Any]:
    return NormalizedRecord(
        source="eprint",
        source_id="2024/1",
        title=title,
        authors=[Author(display_name="A")],
        abstract=None,
        published=PartialDate.of_year(2024),
        source_modified_at=modified,
        harvest_datestamp=None,
        source_version=version,
        doi=None,
    ).model_dump(mode="json")


def serve(bodies: list[list[dict[str, Any]]]) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(bodies)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(queue.pop(0)).encode())

    return handle


def current_title(conn: Connection) -> str:
    row = conn.execute(
        select(t.source_observations.c.normalized).select_from(
            t.source_records.join(
                t.source_observations,
                t.source_observations.c.id == t.source_records.c.current_observation_id,
            )
        )
    ).one()
    return str(row.normalized["title"])


def test_older_backfill_cannot_replace_newer_current_observation(
    engine: Engine, tmp_path: Path
) -> None:
    newer = datetime(2025, 6, 1, tzinfo=UTC)
    older = datetime(2025, 1, 1, tzinfo=UTC)
    clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
    runner, _ = make_runner(
        engine,
        tmp_path,
        serve([[rec("Newer title", newer)], [rec("Older title", older)]]),
        adapter_factory=lambda name, cfg: JsonAdapter(cfg),
        clock=clock,
    )
    runner.run_stream(INCR, "sync", budget())
    clock.advance(timedelta(hours=1))
    runner.run_stream(BOOT, "backfill", budget())
    with engine.connect() as conn:
        assert current_title(conn) == "Newer title"
        kinds = conn.execute(select(t.source_observations.c.change_kind)).scalars().all()
        assert sorted(kinds) == ["initial", "older"]


def test_equal_ordering_with_conflicting_content_is_flagged(engine: Engine, tmp_path: Path) -> None:
    stamp = datetime(2025, 6, 1, tzinfo=UTC)
    clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
    runner, _ = make_runner(
        engine,
        tmp_path,
        serve([[rec("First", stamp, "v2")], [rec("Second", stamp, "v2")]]),
        adapter_factory=lambda name, cfg: JsonAdapter(cfg),
        clock=clock,
    )
    runner.run_stream(INCR, "sync", budget())
    clock.advance(timedelta(hours=1))
    runner.run_stream(INCR, "sync", budget())
    with engine.connect() as conn:
        assert current_title(conn) == "First"
        kinds = sorted(conn.execute(select(t.source_observations.c.change_kind)).scalars().all())
        assert kinds == ["conflict", "initial"]


def test_undated_A_B_A_reversion_is_retained_once(engine: Engine, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
    bodies = [[rec("Title A")], [rec("Title B")], [rec("Title A")], [rec("Title A")]]
    hooks = RecordingHooks()
    runner, _ = make_runner(
        engine,
        tmp_path,
        serve(bodies),
        adapter_factory=lambda name, cfg: JsonAdapter(cfg),
        clock=clock,
        hooks=hooks,
    )
    for _ in bodies:
        runner.run_stream(INCR, "sync", budget())
        clock.advance(timedelta(hours=1))
    with engine.connect() as conn:
        kinds = (
            conn.execute(
                select(t.source_observations.c.change_kind).order_by(
                    t.source_observations.c.observed_at
                )
            )
            .scalars()
            .all()
        )
        assert kinds == ["initial", "changed", "reverted"]
        assert current_title(conn) == "Title A"
    assert [c.change_kind for c in hooks.changes] == ["initial", "changed", "reverted"]


def test_newer_version_replaces_current(engine: Engine, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    runner, _ = make_runner(
        engine,
        tmp_path,
        serve([[rec("V1", t1, "v1")], [rec("V2", t1 + timedelta(days=30), "v2")]]),
        adapter_factory=lambda name, cfg: JsonAdapter(cfg),
        clock=clock,
    )
    runner.run_stream(INCR, "sync", budget())
    clock.advance(timedelta(hours=1))
    runner.run_stream(INCR, "sync", budget())
    with engine.connect() as conn:
        assert current_title(conn) == "V2"
