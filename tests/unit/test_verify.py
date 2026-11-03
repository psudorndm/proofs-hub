from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from proof_index.clock import FixedClock
from proof_index.config.sources import load_sources
from proof_index.verify import run_verification

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "arxiv"


def _handler(request: httpx.Request) -> httpx.Response:
    verb = request.url.params.get("verb")
    files = {
        "Identify": "synthetic-identify.xml",
        "ListMetadataFormats": "synthetic-list-metadata-formats.xml",
        "ListSets": "synthetic-list-sets.xml",
        "ListRecords": "synthetic-oai-listrecords.xml",
    }
    if verb in files:
        return httpx.Response(200, content=(FIX / files[verb]).read_bytes())
    body = (
        (FIX / "synthetic-atom-feed.xml")
        .read_bytes()
        .replace(b"<opensearch:totalResults>3", b"<opensearch:totalResults>2")
    )
    return httpx.Response(200, content=body)


def test_bounded_verification_writes_report_and_captures(tmp_path: Path) -> None:
    config = load_sources(ROOT / "config" / "sources.yaml")
    # Zero spacing so the test does not sleep.
    config = config.model_copy(
        update={
            "providers": {
                k: v.model_copy(update={"min_interval_seconds": 0.001})
                for k, v in config.providers.items()
            }
        }
    )
    report = run_verification(
        "arxiv",
        config,
        FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC)),
        user_agent="test",
        out_dir=tmp_path / "reports",
        capture_dir=tmp_path / "captured",
        transport=httpx.MockTransport(_handler),
    )
    assert report["all_steps_ok"], report
    steps = {s["step"]: s for s in report["steps"]}
    assert steps["identify"]["summary"]["earliestDatestamp"] == "2005-09-16"
    resolution = steps["list-sets"]["summary"]["category_set_resolution"]
    assert resolution["cs.CR"] == "cs:cs:CR"
    assert resolution["cs.LO"] == "cs"  # enclosing set; filter locally
    page = steps["page-arxiv_cs-core_incremental_v1"]["summary"]
    assert page["records_parsed"] == 3 and page["filtered"] == 1
    saved = json.loads(Path(report["report_path"]).read_text())
    assert "not an archive harvest" in saved["note"]
    captured = sorted(p.name for p in (tmp_path / "captured").iterdir())
    assert any(n.endswith(".provenance.json") for n in captured)
    assert all(n.startswith("captured-") for n in captured)
