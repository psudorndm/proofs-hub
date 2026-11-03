"""Historical inputs: DBLP dump snapshot, curated foundations, Crossref DOI enrichment."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, and_, exists, select

from proof_index.adapters.base import ParsedPage
from proof_index.adapters.curated import PARSER_VERSION as CURATED_PARSER
from proof_index.adapters.curated import load_entries
from proof_index.adapters.dblp import PARSER_VERSION as DBLP_PARSER
from proof_index.adapters.dblp import iter_batches, load_venues, open_dump
from proof_index.classify.engine import Classifier
from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.locks import CATALOG_WRITER, advisory_lock
from proof_index.db.repository import transaction
from proof_index.errors import InputError
from proof_index.hashing import sha256_hex
from proof_index.http.rate_limit import Budget
from proof_index.ingest.runner import HarvestRunner, RunSummary
from proof_index.models.classification import Taxonomy
from proof_index.resolve.candidates import resolve_live
from proof_index.resolve.curation import CurationService, parse_command
from proof_index.storage.base import BlobStore, sha256_file

DBLP_STREAM = "dblp:dump:snapshot:v1"
CURATED_STREAM = "curated:foundations:snapshot:v1"
CROSSREF_STREAM = "crossref:doi:enrichment:v1"
AREA_GROUPS = {"ps_direct", "ps_component", "security_method", "formal_method", "formal_tool"}


def title_relevance(classifier: Classifier) -> Any:
    """True when a title alone carries an explicit relevant topic/tool signal."""

    def probe(title: str) -> bool:
        hits, _ = classifier._hits({"title": title})  # noqa: SLF001 - same package policy
        return any(h.group in AREA_GROUPS for h in hits)

    return probe


def import_dblp_dump(
    runner: HarvestRunner,
    blobs: BlobStore,
    classifier: Classifier,
    dump: Path,
    *,
    dtd: Path | None,
    budget: Budget,
    venues_path: Path = Path("config/venues.yaml"),
    batch_size: int = 2000,
    huge_tree: bool = False,
) -> RunSummary:
    if not dump.is_file():
        raise InputError(f"DBLP dump not found: {dump}")
    digest, _size = sha256_file(dump)
    blob = blobs.put_file_if_absent(f"dumps/dblp/{digest}", dump, digest)
    venues = load_venues(venues_path)
    probe = title_relevance(classifier)

    def batches(after: int) -> Iterator[ParsedPage]:
        return iter_batches(
            lambda: open_dump(dump),
            after_ordinal=after,
            batch_size=batch_size,
            venues=venues,
            title_is_relevant=probe,
            dtd_path=dtd,
            huge_tree=huge_tree,
        )

    return runner.run_snapshot(
        DBLP_STREAM,
        "backfill",
        budget,
        snapshot_key=f"dblp-dump:{digest}",
        blob=blob,
        batches=batches,
        adapter_name="dblp",
        parser_version=DBLP_PARSER,
    )


def import_curated(
    runner: HarvestRunner,
    blobs: BlobStore,
    curation: CurationService,
    path: Path,
    *,
    taxonomy: Taxonomy,
    budget: Budget,
) -> dict[str, Any]:
    entries = load_entries(path, taxonomy)  # validates every line before staging anything
    body = path.read_bytes()
    digest = sha256_hex(body)
    blob = blobs.put_if_absent(f"curated/{digest}", body, digest)

    def batches(after: int) -> Iterator[ParsedPage]:
        records = [e.to_record() for e in entries]
        yield ParsedPage(
            records=records,
            next_cursor=None,
            terminal=True,
            provider_response_at=None,
            reported_total=len(records),
        )

    summary = runner.run_snapshot(
        CURATED_STREAM,
        "backfill",
        budget,
        snapshot_key=f"curated:{digest}",
        blob=blob,
        batches=batches,
        adapter_name="curated",
        parser_version=CURATED_PARSER,
    )
    applied = 0
    if summary.state == "succeeded":
        engine = runner.engine
        with advisory_lock(engine, CATALOG_WRITER), transaction(engine) as conn:
            for entry in entries:
                work = conn.execute(
                    select(t.source_records.c.work_id).where(
                        and_(
                            t.source_records.c.source == "curated",
                            t.source_records.c.source_id == f"curated:{entry.curated_id}",
                        )
                    )
                ).scalar_one()
                command = parse_command(
                    {
                        "command_id": (
                            f"import-curated-{entry.curated_id}-{entry.labels_hash()[:12]}"
                        ),
                        "action": "import_curated",
                        "reason": entry.rationale,
                        "reviewer_label": "curation/foundations.jsonl",
                        "review_kind": entry.review_kind,
                        "work_id": str(resolve_live(conn, work)),
                        "curated_id": entry.curated_id,
                        "areas": list(entry.areas),
                        "topics": entry.topics,
                        "contributions": list(entry.contributions),
                        "foundational": entry.foundational,
                    },
                    taxonomy,
                )
                if curation.apply(conn, command)["status"] == "applied":
                    applied += 1
    return {"run": summary.to_json(), "entries": len(entries), "curation_commands_applied": applied}


def plan_doi_enrichment(engine: Engine, max_records: int) -> list[str]:
    """DOIs of visible works that have no Crossref source record yet (sorted, bounded)."""
    crossref = t.source_records.alias("crossref")
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(t.source_records.c.doi)
                .select_from(
                    t.source_records.join(t.works, t.works.c.id == t.source_records.c.work_id)
                )
                .where(t.source_records.c.doi.is_not(None))
                .where(t.source_records.c.source != "crossref")
                .where(t.works.c.visibility.in_(("included", "review")))
                .where(t.works.c.merged_into.is_(None))
                .where(
                    ~exists().where(
                        and_(
                            crossref.c.source == "crossref",
                            crossref.c.source_id == t.source_records.c.doi,
                        )
                    )
                )
                .group_by(t.source_records.c.doi)
                .order_by(t.source_records.c.doi)
                .limit(max_records)
            )
            .scalars()
            .all()
        )
    return [str(r) for r in rows]


def enrich_dois(
    runner: HarvestRunner,
    engine: Engine,
    *,
    max_records: int,
    budget: Budget,
    mailto: str | None,
    clock: Clock,
) -> dict[str, Any]:
    from proof_index.ingest.service import has_resumable_run

    if has_resumable_run(engine, CROSSREF_STREAM):
        summary = runner.run_stream(CROSSREF_STREAM, "backfill", budget)  # resume stored list
        return {"planned": None, "resumed": True, "run": summary.to_json()}
    dois = plan_doi_enrichment(engine, max_records)
    if not dois:
        return {"planned": 0, "resumed": False, "run": None, "complete": True}
    params: dict[str, Any] = {"dois": dois, "planned_at": clock.now().isoformat()}
    if mailto:
        params["mailto"] = mailto
    summary = runner.run_stream(CROSSREF_STREAM, "backfill", budget, request_params=params)
    return {"planned": len(dois), "resumed": False, "run": summary.to_json()}
