"""Shared integration harness: ingest synthetic records through the full hook chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine, func, select

from proof_index.classify.engine import Classifier
from proof_index.classify.rules import load_rules
from proof_index.classify.store import ClassificationHook
from proof_index.clock import FixedClock
from proof_index.db import tables as t
from proof_index.http.rate_limit import Budget
from proof_index.ingest.hooks import HookChain
from proof_index.models.classification import load_taxonomy
from proof_index.resolve.canonical import CanonicalBuilder
from proof_index.resolve.curation import load_curation_state
from proof_index.resolve.events import EventRecorder
from proof_index.resolve.identity import IdentityResolver

from .test_runner import BOOT, INCR, JsonAdapter, make_runner

ROOT = Path(__file__).resolve().parents[2]


CASES = {
    c["case"]: c["record"]
    for c in json.loads((ROOT / "tests/fixtures/synthetic/cases.json").read_text())["cases"]
}


class Harness:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        self.engine = engine
        self.clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
        rules = load_rules(
            ROOT / "config/relevance-rules.yaml", load_taxonomy(ROOT / "config/taxonomy.yaml")
        )
        classify = ClassificationHook(Classifier(rules), self.clock, frozenset({"synthcrypto"}))
        self.builder = CanonicalBuilder(self.clock, rules.version, load_curation_state)
        self.events = EventRecorder(self.clock)
        self.resolver = IdentityResolver(
            self.clock, rules.version, self.builder.rebuild, self.events.on_change
        )
        self.queue: list[list[dict[str, Any]]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(self.queue.pop(0)).encode())

        self.runner, _ = make_runner(
            engine,
            tmp_path,
            handle,
            hooks=HookChain(classify, self.resolver),  # type: ignore[arg-type]
            adapter_factory=lambda name, cfg: JsonAdapter(cfg),
            clock=self.clock,
        )

    def ingest(self, *records: dict[str, Any], bootstrap: bool = False) -> None:
        self.queue.append(list(records))
        stream, mode = (BOOT, "backfill") if bootstrap else (INCR, "sync")
        summary = self.runner.run_stream(stream, mode, Budget(None))
        assert summary.state == "succeeded", summary
        self.clock.advance(timedelta(hours=1))

    def work_of(self, source: str, source_id: str) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(
                select(t.source_records.c.work_id)
                .where(t.source_records.c.source == source)
                .where(t.source_records.c.source_id == source_id)
            ).scalar_one()

    def canonical(self, work_id: Any) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(t.works).where(t.works.c.id == work_id)).one()
        return {
            "revision": row.canonical_revision,
            "visibility": row.visibility,
            "status": row.record_status,
            **row.canonical,
        }

    def count(self, table: Any) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def mutate(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    data = json.loads(json.dumps(base))
    data.update(changes)
    return data
