"""Release export and validation on PostgreSQL (spec sections 13, 16, 18)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select, update

from proof_index.clock import FixedClock
from proof_index.config.site import load_site
from proof_index.config.sources import load_sources
from proof_index.db import tables as t
from proof_index.db.repository import new_id, transaction
from proof_index.errors import DataValidationError, InputError
from proof_index.ingest.reconcile import PendingPromotionError
from proof_index.models.classification import load_taxonomy
from proof_index.publish.export import export_release
from proof_index.publish.fixture import build_fixture_catalog
from proof_index.publish.validate import validate_release
from proof_index.storage.local import LocalBlobStore

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = load_taxonomy(ROOT / "config/taxonomy.yaml")
SOURCES = load_sources(ROOT / "config/sources.yaml")


def build(engine: Engine, tmp_path: Path) -> FixedClock:
    clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
    build_fixture_catalog(
        engine,
        LocalBlobStore(tmp_path / "blobs"),
        SOURCES,
        clock,
        cases_path=ROOT / "tests/fixtures/synthetic/cases.json",
        taxonomy=TAXONOMY,
        rules_path=ROOT / "config/relevance-rules.yaml",
        venues_path=ROOT / "config/venues.yaml",
        work_dir=tmp_path / "work",
    )
    return clock


def export(
    engine: Engine, out: Path, clock: FixedClock, base: str = "/", mode: str = "fixture"
) -> Any:
    return export_release(
        engine,
        out,
        clock=clock,
        site=load_site(ROOT / "config/site.yaml"),
        sources=SOURCES,
        taxonomy=TAXONOMY,
        rules_version="rules-v1",
        public_site_url="https://index.example.test",
        base_path=base,
        dataset_mode=mode,
        revision="0" * 40,
    )


def load(root: Path, rel: str) -> Any:
    return json.loads((root / rel).read_text())


def titles(root: Path, rel: str) -> list[str]:
    return [i["title"] for i in load(root, rel)["items"]]


@pytest.fixture()
def release(engine: Engine, tmp_path: Path) -> tuple[Path, FixedClock]:
    clock = build(engine, tmp_path)
    out = tmp_path / "releases" / "rel-1"
    export(engine, out, clock)
    return out, clock


def test_fixture_release_validates_with_consistent_counts(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    report = validate_release(root)
    assert report["ok"] and report["papers"] == 9
    manifest = load(root, "manifest.json")
    assert manifest["dataset_mode"] == "fixture"
    assert manifest["included_work_count"] == 9
    assert manifest["source_record_counts"] == {"arxiv": 2, "curated": 1, "eprint": 8}
    assert manifest["coverage_status"] == "partial"  # declared live streams have not run
    assert manifest["available_years"][-1] == "unknown"


def test_fixture_release_is_rejected_by_live_deploy_guard(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    with pytest.raises(DataValidationError, match="fixture dataset"):
        validate_release(root, require_live=True)


def test_list_semantics(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    new = titles(root, "lists/new/1.json")
    assert new == ["Zero-knowledge proofs with tight security reductions for signature schemes"]
    # F14: an old paper first harvested "today" is archived, never New.
    assert "Zero-knowledge arguments from one-way functions" in titles(
        root, "lists/archive/1999/1.json"
    )
    assert "Zero-knowledge arguments from one-way functions" not in new
    assert titles(root, "lists/archive/unknown/1.json") == [
        "Succinct arguments for batch statements"
    ]
    assert titles(root, "lists/foundations/1.json") == [
        "A synthetic foundational interactive proof record"
    ]
    updated = load(root, "lists/updated/1.json")
    assert [i["latest_update"]["kind"] for i in updated["items"]] == ["metadata_updated"]
    assert updated["as_of_date"] == "2026-11-03" and updated["window_days"] == 30
    # F01/F02/F03 are one canonical work in every list.
    archive_2020 = titles(root, "lists/archive/2020/1.json")
    assert archive_2020.count("Zero-knowledge proofs for private arithmetic computations") == 1
    # F13 carries two areas and appears once per list.
    assert len(load(root, "topics/zero-knowledge/1.json")["items"]) == len(
        {i["id"] for i in load(root, "topics/zero-knowledge/1.json")["items"]}
    )
    empty = load(root, "topics/verified-implementations/1.json")
    assert empty["total"] == 0 and empty["items"] == [] and empty["page_count"] == 1


def test_ranks_are_unique_and_unknown_dates_sort_last(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    facets = load(root, "search-facets.json")
    papers = facets["papers"]
    for key in ("rank_newest", "rank_oldest", "rank_updated"):
        assert sorted(p[key] for p in papers.values()) == list(range(1, len(papers) + 1))
    unknown = next(i for i, p in papers.items() if p["publication_period"] == "unknown")
    assert papers[unknown]["rank_newest"] == len(papers)
    assert papers[unknown]["rank_oldest"] == len(papers)
    tokens = {t_["token"] for t_ in facets["publication_tokens"]}
    assert {"y:1985", "unknown", "d:2026-10-30"} <= tokens


def test_withdrawn_source_with_active_counterpart_is_shown(
    release: tuple[Path, FixedClock],
) -> None:
    root, _ = release
    paper = next(
        json.loads(p.read_text())
        for p in (root / "papers").glob("*.json")
        if json.loads(p.read_text())["title"].startswith("Zero-knowledge proofs with tight")
    )
    assert paper["status"] == "active"
    assert {s["provider"]: s["status"] for s in paper["sources"]} == {
        "arxiv": "withdrawn",
        "eprint": "active",
    }
    assert set(paper["areas"]) == {"proof-systems", "security-proofs"}


def test_untrusted_title_and_link_are_safe(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    blob = "".join(p.read_text() for p in root.rglob("*") if p.is_file())
    assert "javascript:" not in blob
    paper = next(
        json.loads(p.read_text())
        for p in (root / "papers").glob("*.json")
        if "<script>" in json.loads(p.read_text())["title"]
    )
    assert paper["title"].startswith("<script>")  # preserved as text; renderers must escape
    feed = (root / "feeds" / "new.atom").read_text() + (root / "feeds" / "updated.atom").read_text()
    assert "<script>" not in feed


def test_public_export_excludes_internal_secrets_and_error_traces(
    engine: Engine, tmp_path: Path
) -> None:
    clock = build(engine, tmp_path)
    with transaction(engine) as conn:
        stream = conn.execute(select(t.provider_streams.c.id)).scalars().first()
        conn.execute(
            insert(t.harvest_runs).values(
                id=new_id(),
                stream_id=stream,
                mode="sync",
                state="failed",
                started_at=clock.now(),
                config_hash="x",
                error_code="transport_failure",
                error_summary="Traceback (most recent call last): postgresql+psycopg://u:secret@db/x",
            )
        )
        conn.execute(update(t.provider_streams).values(last_error_code="transport_failure"))
    out = tmp_path / "releases" / "rel-scan"
    export(engine, out, clock)
    validate_release(out)
    blob = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert "secret" not in blob and "Traceback" not in blob and "error_summary" not in blob
    status = load(out, "status.json")
    codes = {s["last_error_code"] for s in status["sources"]}
    assert "transport_failure" in codes  # sanitized code only


def test_validator_detects_tampering_and_leaks(release: tuple[Path, FixedClock]) -> None:
    root, _ = release
    paper = next((root / "papers").glob("*.json"))
    data = json.loads(paper.read_text())
    data["title"] = "Tampered"
    paper.write_text(json.dumps(data))
    with pytest.raises(DataValidationError, match="checksum mismatch"):
        validate_release(root)
    data["headers"] = {"Authorization": "x"}
    paper.write_text(json.dumps(data))
    with pytest.raises(DataValidationError, match="forbidden key"):
        validate_release(root)


def test_new_and_updated_feeds_have_stable_unique_ids(engine: Engine, tmp_path: Path) -> None:
    clock = build(engine, tmp_path)
    ids = []
    for name in ("rel-a", "rel-b"):
        out = tmp_path / "releases" / name
        export(engine, out, clock)
        feeds = (out / "feeds/new.atom").read_text() + (out / "feeds/updated.atom").read_text()
        ids.append(
            sorted(
                line.strip()
                for line in feeds.splitlines()
                if "<id>tag:" in line and ":paper:" in line or ":update:" in line
            )
        )
    assert ids[0] == ids[1] and len(ids[0]) == len(set(ids[0])) == 2


def test_export_never_overwrites_an_existing_release(
    release: tuple[Path, FixedClock], engine: Engine
) -> None:
    root, clock = release
    with pytest.raises(InputError, match="already exists"):
        export(engine, root, clock)


def test_partial_promotion_blocks_export(engine: Engine, tmp_path: Path) -> None:
    clock = build(engine, tmp_path)
    with transaction(engine) as conn:
        stream = conn.execute(select(t.provider_streams.c.id)).scalars().first()
        conn.execute(
            insert(t.harvest_runs).values(
                id=new_id(),
                stream_id=stream,
                mode="sync",
                state="promoting",
                started_at=clock.now(),
                config_hash="x",
                promotion_started_at=clock.now(),
            )
        )
    with pytest.raises(PendingPromotionError):
        export(engine, tmp_path / "releases" / "blocked", clock)
    assert not (tmp_path / "releases" / "blocked").exists()


def test_non_root_base_path_in_public_paths(engine: Engine, tmp_path: Path) -> None:
    clock = build(engine, tmp_path)
    out = tmp_path / "releases" / "rel-sub"
    export(engine, out, clock, base="/proof-papers/")
    validate_release(out)
    page = load(out, "lists/new/1.json")["items"][0]
    assert page["canonical_path"].startswith("/proof-papers/papers/")
    assert page["json_path"].startswith("/proof-papers/assets/releases/rel-sub/papers/")
    feed = (out / "feeds/new.atom").read_text()
    assert 'href="https://index.example.test/proof-papers/papers/' in feed
    assert load(out, "manifest.json")["public_base_path"] == "/proof-papers/"
