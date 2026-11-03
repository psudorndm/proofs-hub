"""Build a *fixture* catalog from the synthetic cases (spec section 18.1).

The synthetic records go through the real promotion, classification, identity,
event, and curation code. A release exported from this catalog must use
``dataset_mode="fixture"``; the live deployment guard rejects it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from proof_index.adapters.base import ParsedPage
from proof_index.classify.engine import Classifier
from proof_index.classify.rules import load_rules
from proof_index.classify.store import ClassificationHook
from proof_index.clock import FixedClock
from proof_index.config.sources import SourcesConfig
from proof_index.errors import ProviderError
from proof_index.hashing import sha256_hex
from proof_index.http.rate_limit import Budget
from proof_index.ingest.historical import import_curated
from proof_index.ingest.hooks import HookChain
from proof_index.ingest.runner import HarvestRunner
from proof_index.ingest.service import crypto_venue_keys
from proof_index.models.classification import Taxonomy
from proof_index.models.source import NormalizedRecord
from proof_index.resolve.canonical import CanonicalBuilder
from proof_index.resolve.curation import CurationService, load_curation_state
from proof_index.resolve.events import EventRecorder
from proof_index.resolve.identity import IdentityResolver
from proof_index.storage.base import BlobStore

FIXTURE_STREAM = "eprint:all:incremental:v1"
FIRST = ["F01", "F02", "F04", "F05", "F06", "F07", "F08", "F10", "F11", "F12", "F13", "F14", "F15"]
SECOND = ["F03", "F16"]


def _bulk_record(i: int) -> dict[str, Any]:
    """Generated SYNTHETIC filler records (for pagination tests only)."""
    return {
        "source": "eprint",
        "source_id": f"2025/{1000 + i}",
        "title": f"Synthetic zero-knowledge proof record number {i}",
        "authors": [{"display_name": f"Synthetic Author {i % 7}"}],
        "abstract": "Generated filler record for pagination tests; not a real paper.",
        "published": {"year": 2025, "month": 1 + i % 12, "day": 1 + i % 28, "precision": "day"},
        "source_modified_at": None,
        "harvest_datestamp": None,
        "source_version": None,
        "doi": None,
        "identifiers": {},
        "source_categories": [],
        "venue_key": None,
        "venue_name": None,
        "links": [],
        "status": "active",
        "evidence": {"synthetic": "generated bulk fixture"},
    }


def _no_http(provider: str, budget: Budget) -> Any:
    raise ProviderError("the fixture builder never uses the network")


def build_fixture_catalog(
    engine: Engine,
    blobs: BlobStore,
    config: SourcesConfig,
    clock: FixedClock,
    *,
    cases_path: Path,
    taxonomy: Taxonomy,
    rules_path: Path,
    venues_path: Path,
    work_dir: Path,
    bulk: int = 0,
) -> dict[str, Any]:
    cases = {c["case"]: c["record"] for c in json.loads(cases_path.read_text())["cases"]}
    rules = load_rules(rules_path, taxonomy)
    venues = crypto_venue_keys(venues_path) | {"synthcrypto"}
    classify = ClassificationHook(Classifier(rules), clock, venues)
    builder = CanonicalBuilder(clock, rules.version, load_curation_state)
    events = EventRecorder(clock)
    resolver = IdentityResolver(clock, rules.version, builder.rebuild, events.on_change)
    runner = HarvestRunner(
        engine, blobs, config, clock, _no_http, hooks=HookChain(classify, resolver)
    )

    # A later metadata update of F13 so the Updated list has a synthetic entry.
    f17 = dict(cases["F13"])
    f17["abstract"] = cases["F13"]["abstract"] + " (Synthetic correction.)"
    f17["source_modified_at"] = "2026-11-01T00:00:00Z"
    batches = [[cases[c] for c in FIRST], [cases[c] for c in SECOND] + [f17]]
    if bulk:
        batches.append([_bulk_record(i) for i in range(1, bulk + 1)])
    summaries = []
    body = cases_path.read_bytes()
    digest = sha256_hex(body)
    blob = blobs.put_if_absent(f"fixtures/{digest}", body, digest)
    for index, batch in enumerate(batches):
        records = [NormalizedRecord.model_validate(r) for r in batch]

        def pages(after: int, records: list[NormalizedRecord] = records) -> Iterator[ParsedPage]:
            yield ParsedPage(
                records=records,
                next_cursor=None,
                terminal=True,
                provider_response_at=None,
                reported_total=None,
            )

        summary = runner.run_snapshot(
            FIXTURE_STREAM,
            "sync",
            Budget(None),
            snapshot_key=f"fixture:{digest}:{index}",
            blob=blob,
            batches=pages,
            adapter_name="fixture",
            parser_version="fixture-v1",
        )
        summaries.append(summary.to_json())
        clock.advance(timedelta(minutes=10))

    f09 = cases["F09"]
    entry = {
        "curated_id": f09["source_id"].removeprefix("curated:"),
        "title": f09["title"],
        "authors": f09["authors"],
        "published": f09["published"],
        "doi": None,
        "source_identifiers": {},
        "landing_url": None,
        "areas": ["proof-systems"],
        "topics": ["interactive-foundations", "zero-knowledge"],
        "contributions": ["construction-theory"],
        "foundational": True,
        "rationale": "SYNTHETIC fixture standing in for a verified foundational record.",
        "evidence_urls": ["https://records.synthetic-fixture.test/zk-1985"],
        "review_kind": "agent-provisional",
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    jsonl = work_dir / "fixture-foundations.jsonl"
    jsonl.write_text(json.dumps(entry) + "\n")
    curated = import_curated(
        runner,
        blobs,
        CurationService(clock, builder, taxonomy),
        jsonl,
        taxonomy=taxonomy,
        budget=Budget(None),
    )
    return {"runs": summaries, "curated": curated}
