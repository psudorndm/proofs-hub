"""DBLP dump import, Crossref enrichment, and curated foundations (spec section 9, T10)."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from proof_index.db import tables as t
from proof_index.errors import DataValidationError
from proof_index.http.rate_limit import Budget
from proof_index.ingest.historical import (
    CROSSREF_STREAM,
    DBLP_STREAM,
    enrich_dois,
    import_curated,
    import_dblp_dump,
)
from proof_index.ingest.runner import FaultInjection
from proof_index.resolve.curation import CurationService

from .harness import CASES, ROOT, Harness, mutate

pytestmark = pytest.mark.db
DBLP = ROOT / "tests/fixtures/dblp"
CROSSREF = ROOT / "tests/fixtures/crossref"


def dblp_import(h: Harness, dump: Path, **kw: Any) -> Any:
    return import_dblp_dump(
        h.runner,
        h.runner.blobs,
        h.classifier,
        dump,
        dtd=DBLP / "synthetic-dblp.dtd",
        budget=Budget(None),
        venues_path=ROOT / "config/venues.yaml",
        **kw,
    )


def rows(h: Harness, table: Any, *where: Any) -> list[Any]:
    with h.engine.connect() as conn:
        q = select(table)
        for w in where:
            q = q.where(w)
        return list(conn.execute(q).all())


# --- DBLP -----------------------------------------------------------------------------------


def test_dblp_dump_import_filters_candidates_and_keeps_unknown_totals(h: Harness) -> None:
    summary = dblp_import(h, DBLP / "synthetic-dblp.xml", batch_size=3)
    assert summary.state == "succeeded", summary
    records = {
        r.source_id: r for r in rows(h, t.source_records, t.source_records.c.source == "dblp")
    }
    # Venue candidate, title-signal candidate, and the withdrawn journal article are kept;
    # the person page, proceedings volume, and unrelated bridge article are filtered.
    assert set(records) == {
        "conf/crypto/ExampleE20",
        "journals/misc/Tamarin22",
        "journals/joc/Cafe18",
    }
    assert len(rows(h, t.quarantine_items)) == 1  # the empty-title record
    stream = rows(h, t.provider_streams, t.provider_streams.c.stream_key == DBLP_STREAM)[0]
    cov = rows(h, t.coverage_slices, t.coverage_slices.c.stream_id == stream.id)[0]
    assert cov.expected_count is None  # DBLP supplies no total; never filled in
    assert (cov.valid_count, cov.quarantined_count) == (3, 1)
    assert cov.notes["filtered_by_local_rule"] == 3  # www, proceedings, bridge article


def test_dblp_editors_not_authors_and_cross_ids_merge(h: Harness) -> None:
    h.ingest(CASES["F01"])
    dblp_import(h, DBLP / "synthetic-dblp.xml")
    assert h.work_of("dblp", "conf/crypto/ExampleE20") == h.work_of("eprint", "2020/7")
    with h.engine.connect() as conn:
        obs = conn.execute(
            select(t.source_observations.c.normalized)
            .join(
                t.source_records,
                t.source_records.c.current_observation_id == t.source_observations.c.id,
            )
            .where(t.source_records.c.source_id == "conf/crypto/ExampleE20")
        ).scalar_one()
    assert [a["display_name"] for a in obs["authors"]] == ["Alice Example", "Bob Müller"]
    assert obs["published"]["precision"] == "year"
    canon = h.canonical(h.work_of("eprint", "2020/7"))
    assert canon["published"]["precision"] == "year"  # earliest lower bound: year 2020 ≤ 2020-01-03
    assert any(p["venue_name"] == "CRYPTO" for p in canon["publications"])


def test_dblp_dump_restart_resumes_from_durable_ordinal(h: Harness) -> None:
    with pytest.raises(RuntimeError, match="injected crash"):
        import_dblp_dump(
            h.runner,
            h.runner.blobs,
            h.classifier,
            DBLP / "synthetic-dblp.xml",
            dtd=DBLP / "synthetic-dblp.dtd",
            budget=Budget(None),
            batch_size=2,
        ) if False else h.runner.run_snapshot(  # crash after the first durable batch
            DBLP_STREAM,
            "backfill",
            Budget(None),
            snapshot_key="dblp-dump:test",
            blob=_blob(h),
            batches=_batches(h, 2),
            adapter_name="dblp",
            parser_version="t",
            faults=FaultInjection(crash_after_pages=1),
        )
    first_pages = rows(h, t.harvest_pages)
    assert len(first_pages) == 1 and first_pages[0].next_cursor == {"after_ordinal": 1}
    summary = h.runner.run_snapshot(
        DBLP_STREAM,
        "backfill",
        Budget(None),
        snapshot_key="dblp-dump:test",
        blob=_blob(h),
        batches=_batches(h, 2),
        adapter_name="dblp",
        parser_version="t",
    )
    assert summary.state == "succeeded"
    assert len(rows(h, t.harvest_runs)) == 1
    assert len(rows(h, t.source_records, t.source_records.c.source == "dblp")) == 3


def test_changed_dump_starts_a_new_snapshot(h: Harness, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        h.runner.run_snapshot(
            DBLP_STREAM,
            "backfill",
            Budget(None),
            snapshot_key="dblp-dump:old",
            blob=_blob(h),
            batches=_batches(h, 2),
            adapter_name="dblp",
            parser_version="t",
            faults=FaultInjection(crash_after_pages=1),
        )
    changed = tmp_path / "dblp-changed.xml"
    shutil.copy(DBLP / "synthetic-dblp.xml", changed)
    changed.write_text(
        changed.read_text(encoding="latin-1").replace("Load tests", "Stress tests"),
        encoding="latin-1",
    )
    summary = dblp_import(h, changed, batch_size=2)
    assert summary.state == "succeeded"
    runs = {r.snapshot_key: r for r in rows(h, t.harvest_runs)}
    assert runs["dblp-dump:old"].state == "failed"
    assert runs["dblp-dump:old"].error_code == "superseded_snapshot"
    new_run = next(r for k, r in runs.items() if k != "dblp-dump:old")
    first = rows(h, t.harvest_pages, t.harvest_pages.c.run_id == new_run.id)
    assert min(p.ordinal for p in first) == 0  # started from the beginning, not a continuation


def _blob(h: Harness) -> Any:
    from proof_index.storage.base import sha256_file

    digest, _ = sha256_file(DBLP / "synthetic-dblp.xml")
    return h.runner.blobs.put_file_if_absent(
        f"dumps/dblp/{digest}", DBLP / "synthetic-dblp.xml", digest
    )


def _batches(h: Harness, size: int) -> Any:
    from proof_index.adapters.dblp import iter_batches, load_venues, open_dump
    from proof_index.ingest.historical import title_relevance

    venues = load_venues(ROOT / "config/venues.yaml")
    return lambda after: iter_batches(
        lambda: open_dump(DBLP / "synthetic-dblp.xml"),
        after_ordinal=after,
        batch_size=size,
        venues=venues,
        title_is_relevant=title_relevance(h.classifier),
        dtd_path=DBLP / "synthetic-dblp.dtd",
    )


# --- Crossref -------------------------------------------------------------------------------


def crossref_handler(log: list[str]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        doi = request.url.path.rsplit("/works/", 1)[1]
        log.append(doi)
        if doi == "10.9999%2Fsynthetic.0001" or doi == "10.9999/synthetic.0001":
            return httpx.Response(200, content=(CROSSREF / "synthetic-work.json").read_bytes())
        if "proceedings" in doi:
            return httpx.Response(
                200, content=(CROSSREF / "synthetic-proceedings.json").read_bytes()
            )
        return httpx.Response(404, content=b'{"status":"error"}')

    return handle


def test_crossref_enrichment_attaches_and_quarantines_404_and_containers(h: Harness) -> None:
    h.ingest(mutate(CASES["F01"], doi="10.9999/synthetic.0001"))
    h.ingest(mutate(CASES["F04"], doi="10.9999/missing.doi"))
    h.ingest(mutate(CASES["F05"], doi="10.9999/proceedings.2021"))
    log: list[str] = []
    h.http_handler = crossref_handler(log)
    result = enrich_dois(
        h.runner, h.engine, max_records=10, budget=Budget(None), mailto=None, clock=h.clock
    )
    assert result["planned"] == 3 and result["run"]["state"] == "succeeded", result
    work = h.work_of("eprint", "2020/7")
    assert h.work_of("crossref", "10.9999/synthetic.0001") == work
    errors = sorted(q.error_code for q in rows(h, t.quarantine_items))
    assert errors == ["container_doi", "doi_not_found"]
    canon = h.canonical(work)
    # The en dash is normalized to "-".
    assert any(
        p["venue_name"] == "Advances in Cryptology - CRYPTO 2020" for p in canon["publications"]
    )
    assert canon["published"] == {"year": 2020, "month": 1, "day": 3, "precision": "day"}
    with h.engine.connect() as conn:
        obs = conn.execute(
            select(t.source_observations.c.normalized).where(
                t.source_observations.c.normalized["source"].astext == "crossref"
            )
        ).scalar_one()
    # Earliest manifestation (online 2020-08-09), never created/deposited/indexed.
    assert obs["published"] == {"year": 2020, "month": 8, "day": 9, "precision": "day"}
    assert [a["display_name"] for a in obs["authors"]] == ["Alice Example", "Bob Example"]
    # A second run plans nothing new for the enriched DOI.
    again = enrich_dois(
        h.runner, h.engine, max_records=10, budget=Budget(None), mailto=None, clock=h.clock
    )
    assert again["planned"] == 2  # the 404 and container DOIs remain unenriched


def test_crossref_conflicting_metadata_becomes_review_not_overwrite(h: Harness) -> None:
    h.ingest(mutate(CASES["F04"], doi="10.9999/synthetic.0001"))  # unrelated paper claims the DOI
    h.http_handler = crossref_handler([])
    enrich_dois(h.runner, h.engine, max_records=10, budget=Budget(None), mailto=None, clock=h.clock)
    assert h.work_of("crossref", "10.9999/synthetic.0001") is None
    reviews = rows(h, t.identity_reviews)
    assert [r.reason for r in reviews] == ["crossref_metadata_conflicts_with_work"]
    assert h.canonical(h.work_of("eprint", "2021/42"))["title"] == CASES["F04"]["title"]


def test_crossref_run_resumes_with_its_stored_doi_list(h: Harness) -> None:
    h.ingest(mutate(CASES["F01"], doi="10.9999/synthetic.0001"))
    h.ingest(mutate(CASES["F04"], doi="10.9999/missing.doi"))
    h.http_handler = lambda request: httpx.Response(503)
    first = enrich_dois(
        h.runner, h.engine, max_records=10, budget=Budget(None), mailto=None, clock=h.clock
    )
    assert first["run"]["state"] == "paused"
    h.http_handler = None
    h.ingest(mutate(CASES["F05"], doi="10.9999/later.doi"))  # not part of the paused run
    log: list[str] = []
    h.http_handler = crossref_handler(log)
    second = enrich_dois(
        h.runner, h.engine, max_records=10, budget=Budget(None), mailto=None, clock=h.clock
    )
    assert second["resumed"] and second["run"]["state"] == "succeeded"
    assert len(log) == 2  # only the two DOIs fixed when the run was planned


# --- curated -------------------------------------------------------------------------------


def entry(**changes: Any) -> dict[str, Any]:
    data = {
        "curated_id": "synthetic-ip-1985",
        "title": "A synthetic foundational interactive proof record",
        "authors": [{"display_name": "Ida Historic"}, {"display_name": "Jon Historic"}],
        "published": {"year": 1985, "month": None, "day": None, "precision": "year"},
        "doi": None,
        "source_identifiers": {},
        "landing_url": "https://records.synthetic-fixture.test/ip-1985",
        "areas": ["proof-systems"],
        "topics": ["interactive-foundations"],
        "contributions": ["construction-theory"],
        "foundational": True,
        "rationale": "SYNTHETIC test entry standing in for a verified foundational paper.",
        "evidence_urls": ["https://records.synthetic-fixture.test/ip-1985"],
        "review_kind": "agent-provisional",
    }
    data.update(changes)
    return data


def write_jsonl(path: Path, *entries: dict[str, Any]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def curated(h: Harness, path: Path) -> dict[str, Any]:
    service = CurationService(h.clock, h.builder, h.taxonomy)
    return import_curated(
        h.runner, h.runner.blobs, service, path, taxonomy=h.taxonomy, budget=Budget(None)
    )


def test_curated_import_is_included_foundational_and_idempotent(h: Harness, tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "foundations.jsonl", entry())
    result = curated(h, path)
    assert result["curation_commands_applied"] == 1
    work = h.work_of("curated", "curated:synthetic-ip-1985")
    canon = h.canonical(work)
    assert canon["visibility"] == "included" and canon["foundational"] is True
    assert canon["inclusion_provenance"] == "agent-provisional"
    assert canon["areas"] == ["proof-systems"] and canon["topics"] == ["interactive-foundations"]
    assert canon["published"] == {"year": 1985, "month": None, "day": None, "precision": "year"}
    assert canon["abstract"] is None
    assert curated(h, path)["curation_commands_applied"] == 0
    # A corrected title keeps the stable slug and work ID.
    h.clock.advance(timedelta(hours=1))
    write_jsonl(path, entry(title="A synthetic foundational interactive-proof record (corrected)"))
    curated(h, path)
    assert h.work_of("curated", "curated:synthetic-ip-1985") == work
    assert h.canonical(work)["title"].endswith("(corrected)")


def test_curated_rejects_schema_example_and_invalid_lines(h: Harness, tmp_path: Path) -> None:
    bad = write_jsonl(
        tmp_path / "bad.jsonl",
        entry(),
        entry(
            curated_id="stable-local-slug",
            title="Verified bibliographic title",
            evidence_urls=["https://example.org/authoritative-record"],
            landing_url="https://example.org/authoritative-record",
        ),
        entry(curated_id="bad-topic", topics=["not-a-topic"]),
    )
    with pytest.raises(DataValidationError) as info:
        curated(h, bad)
    assert "line 2" in str(info.value) and "line 3" in str(info.value)
    assert rows(h, t.source_records) == []  # nothing staged


def test_curated_entry_with_identifiers_joins_existing_work(h: Harness, tmp_path: Path) -> None:
    h.ingest(CASES["F01"])
    path = write_jsonl(
        tmp_path / "f.jsonl",
        entry(
            curated_id="synthetic-zk-2020",
            title=CASES["F01"]["title"],
            authors=CASES["F01"]["authors"],
            source_identifiers={"eprint": ["2020/0007"]},
            topics=["zero-knowledge"],
        ),
    )
    curated(h, path)
    work = h.work_of("eprint", "2020/7")
    assert h.work_of("curated", "curated:synthetic-zk-2020") == work
    assert h.canonical(work)["foundational"] is True
    # Text precedence keeps the ePrint title; the curated record adds evidence.
    assert h.canonical(work)["title_provenance"]["origin"] == "source"


def test_crossref_stream_key_constant() -> None:
    assert CROSSREF_STREAM == "crossref:doi:enrichment:v1"
