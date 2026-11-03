"""Bounded live source verification (spec section 4).

Writes a JSON report and, optionally, captured fixtures with provenance. It never
writes to the catalog database and never advances a watermark. A bounded
verification is not an archive harvest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from lxml import etree

from proof_index.adapters.base import (
    Checkpoint,
    HarvestRequest,
    PageStructureError,
    ParseContext,
    ProtocolError,
)
from proof_index.adapters.oai import OAI_NS, parse_envelope, raise_for_errors, verb_request
from proof_index.adapters.registry import adapter_for
from proof_index.clock import Clock
from proof_index.config.sources import SourcesConfig
from proof_index.errors import ProofIndexError
from proof_index.hashing import sha256_hex
from proof_index.http.client import FetchedResponse, ProviderHttpClient, RequestSpec
from proof_index.http.rate_limit import Budget, LimiterRegistry

_NS = {"oai": OAI_NS}
VERIFY_STREAMS = {
    "eprint": ["eprint:all:incremental:v1"],
    "arxiv": ["arxiv:cs-core:incremental:v1", "arxiv:adjacent-terms:supplemental:v1"],
}
ARXIV_WANTED_CATEGORIES = ["cs.CR", "cs.CC", "cs.DS", "cs.LO", "cs.PL"]


@dataclass
class Step:
    name: str
    request: RequestSpec
    ok: bool = False
    status: int | None = None
    error: str | None = None
    summary: dict[str, Any] | None = None
    raw_sha256: str | None = None


def _capture(dest: Path, step: Step, response: FetchedResponse, clock: Clock) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    digest = sha256_hex(response.body)
    base = dest / f"captured-{step.request.provider}-{step.name}-{digest[:12]}"
    base.with_suffix(".xml").write_bytes(response.body)
    base.with_suffix(".provenance.json").write_text(
        json.dumps(
            {
                "provider": step.request.provider,
                "endpoint": step.request.url,
                "params": [list(p) for p in step.request.params],
                "accessed_at": response.fetched_at.isoformat(),
                "http_status": response.status,
                "content_type": response.content_type,
                "raw_sha256": digest,
                "captured_by": "proof-index sources verify --bounded",
                "recorded_at": clock.now().isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _identify_summary(body: bytes) -> dict[str, Any]:
    root, response_date = parse_envelope(body)
    raise_for_errors(root)
    ident = root.find("oai:Identify", _NS)
    if ident is None:
        raise PageStructureError("Identify response missing <Identify>")
    return {
        "responseDate": response_date.isoformat() if response_date else None,
        "repositoryName": ident.findtext("oai:repositoryName", namespaces=_NS),
        "earliestDatestamp": ident.findtext("oai:earliestDatestamp", namespaces=_NS),
        "granularity": ident.findtext("oai:granularity", namespaces=_NS),
        "deletedRecord": ident.findtext("oai:deletedRecord", namespaces=_NS),
    }


def _formats_summary(body: bytes) -> dict[str, Any]:
    root, _ = parse_envelope(body)
    raise_for_errors(root)
    return {
        "metadataPrefixes": sorted(
            (f.findtext("oai:metadataPrefix", namespaces=_NS) or "")
            for f in root.iter(f"{{{OAI_NS}}}metadataFormat")
        )
    }


def _sets_summary(body: bytes) -> dict[str, Any]:
    root, _ = parse_envelope(body)
    raise_for_errors(root)
    specs = sorted(
        (s.findtext("oai:setSpec", namespaces=_NS) or "") for s in root.iter(f"{{{OAI_NS}}}set")
    )
    resolution: dict[str, str | None] = {}
    for category in ARXIV_WANTED_CATEGORIES:
        archive = category.split(".")[0]
        colon_form = category.replace(".", ":")  # e.g. cs.CR -> set spec "cs:cs:CR"
        exact = [s for s in specs if s == category or s.endswith(f":{colon_form}")]
        resolution[category] = exact[0] if exact else (archive if archive in specs else None)
    token = root.find(f".//{{{OAI_NS}}}resumptionToken")
    return {
        "set_count_first_page": len(specs),
        "set_list_complete": token is None or not (token.text or "").strip(),
        "category_set_resolution": resolution,
        "sample_sets": specs[:50],
    }


def run_verification(
    source: str,
    config: SourcesConfig,
    clock: Clock,
    *,
    user_agent: str,
    out_dir: Path,
    capture_dir: Path | None = None,
    transport: Any = None,
    max_minutes: float = 10.0,
) -> dict[str, Any]:
    if source not in VERIFY_STREAMS:
        raise ProofIndexError(f"bounded verification is not implemented for {source!r}")
    limiters = LimiterRegistry(config.rate_intervals())
    budget = Budget(max_minutes * 60)
    steps: list[tuple[Step, Any]] = []
    provider = config.provider(source)
    oai_plan = adapter_for(config.stream(VERIFY_STREAMS[source][0]).adapter, config).plan(
        HarvestRequest(stream_key=VERIFY_STREAMS[source][0], mode="verify"), Checkpoint()
    )
    steps.append((Step("identify", verb_request(oai_plan, "Identify")), _identify_summary))
    steps.append(
        (
            Step("list-metadata-formats", verb_request(oai_plan, "ListMetadataFormats")),
            _formats_summary,
        )
    )
    if source == "arxiv":
        steps.append((Step("list-sets", verb_request(oai_plan, "ListSets")), _sets_summary))

    # One bounded ListRecords page per verified stream, over the last two days.
    window_from = clock.now() - timedelta(days=2)
    for stream_key in VERIFY_STREAMS[source]:
        stream = config.stream(stream_key)
        adapter = adapter_for(stream.adapter, config)
        plan = adapter.plan(
            HarvestRequest(stream_key=stream_key, mode="verify", window_from=window_from),
            Checkpoint(),
        )

        def summarize(body: bytes, adapter=adapter, plan=plan) -> dict[str, Any]:  # type: ignore[no-untyped-def]
            page = adapter.parse_page(
                body,
                ParseContext(
                    provider=plan.provider,
                    stream_key=plan.stream_key,
                    parser_version=adapter.parser_version,
                    fetched_at=clock.now(),
                    plan_params=plan.params,
                    cursor=None,
                ),
            )
            sample = [
                r.model_dump(
                    mode="json", include={"source_id", "title", "published", "source_version"}
                )
                for r in page.records[:5]
            ]
            return {
                "records_parsed": len(page.records),
                "quarantined": len(page.quarantined_items),
                "filtered": page.filtered_count,
                "terminal": page.terminal,
                "reported_total": page.reported_total,
                "no_records_match": page.no_records_match,
                "sample": sample,
            }

        steps.append(
            (
                Step(f"page-{stream_key.replace(':', '_')}", adapter.request_page(plan, None)),
                summarize,
            )
        )

    results = []
    with ProviderHttpClient(
        user_agent=user_agent,
        limiter=limiters.get(source),
        budget=budget,
        transport=transport,
        clock=clock,
    ) as client:
        for step, summarize in steps:
            try:
                response = client.fetch(step.request)
                step.status = response.status
                step.raw_sha256 = sha256_hex(response.body)
                if capture_dir is not None:
                    _capture(capture_dir, step, response, clock)
                step.summary = summarize(response.body)
                step.ok = True
            except (ProofIndexError, etree.XMLSyntaxError, ProtocolError) as exc:
                step.error = f"{type(exc).__name__}: {exc}"[:500]
                status = getattr(exc, "status", None)
                step.status = status if isinstance(status, int) else step.status
            results.append(
                {
                    "step": step.name,
                    "endpoint": step.request.url,
                    "params": [list(p) for p in step.request.params],
                    "ok": step.ok,
                    "http_status": step.status,
                    "raw_sha256": step.raw_sha256,
                    "error": step.error,
                    "summary": step.summary,
                }
            )
    report = {
        "kind": "bounded-source-verification",
        "source": source,
        "verified_at": clock.now().isoformat(),
        "live_verified_previously": provider.live_verified,
        "all_steps_ok": all(r["ok"] for r in results),
        "note": (
            "Bounded verification only: no records promoted, no watermarks advanced. "
            "This is not an archive harvest."
        ),
        "steps": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.now().strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"verify-{source}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    report["report_path"] = str(path)
    return report
