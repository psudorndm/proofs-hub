"""Export one immutable, validated release directory (spec section 13).

Files are written into a temporary sibling directory and renamed into place at
the end, so an export never overwrites or half-writes a previous release.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from proof_index.clock import Clock
from proof_index.config.site import SiteConfig
from proof_index.config.sources import SourcesConfig
from proof_index.db import tables as t
from proof_index.db.repository import new_id, transaction
from proof_index.errors import InputError
from proof_index.hashing import sha256_hex
from proof_index.models.classification import Taxonomy
from proof_index.models.dates import PRECISION_RANK, PartialDate
from proof_index.models.public import (
    PAGE_SIZE,
    ListPage,
    Manifest,
    PaperCard,
    PublicAuthor,
    PublicCodeLink,
    PublicEvent,
    PublicEvidence,
    PublicInclusion,
    PublicPaper,
    PublicPublication,
    PublicSource,
    PublicUpdate,
    SourceSummary,
)
from proof_index.normalize.identifiers import eprint_display_id
from proof_index.normalize.text import title_key
from proof_index.publish.bibtex import bibtex_entry
from proof_index.publish.feeds import build_feed, update_entry_key
from proof_index.publish.snapshot import CatalogSnapshot, WorkRow, take_snapshot

WINDOW_DAYS = 30
UPDATE_KINDS = ("source_revision", "metadata_updated", "withdrawn", "restored")
_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{2,80}$")
REPO_ROOT = Path(__file__).resolve().parents[4]


def derived_landing(provider: str, source_id: str) -> str | None:
    """Landing URL that follows from the identifier alone (decision D008)."""
    if provider == "eprint":
        return f"https://eprint.iacr.org/{eprint_display_id(source_id)}"
    if provider == "arxiv":
        return f"https://arxiv.org/abs/{source_id}"
    if provider == "dblp":
        return f"https://dblp.org/rec/{source_id}"
    if provider == "crossref":
        return f"https://doi.org/{source_id}"
    return None


def code_revision() -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown-local"
    value = out.stdout.strip()
    return (
        value if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else "unknown-local"
    )


def _latest_update(events: list[Any]) -> tuple[PublicUpdate | None, str | None]:
    best = None
    for event in events:
        if event.kind in UPDATE_KINDS and event.feed_eligible and event.event_date is not None:
            if best is None or (event.event_date, event.event_key) > (
                best.event_date,
                best.event_key,
            ):
                best = event
    if best is None:
        return None, None
    return (
        PublicUpdate(
            kind=best.kind,
            date=best.event_date.isoformat(),
            date_basis=best.date_basis,
            source_record_id=str(best.source_record_id) if best.source_record_id else None,
        ),
        best.event_key,
    )


def build_paper(
    work: WorkRow, events: list[Any], related: list[str], release_id: str, base: str
) -> tuple[PublicPaper, str | None]:
    c = work.canonical
    work_id = str(work.id)
    sources = []
    for s in c["sources"]:
        links = s.get("links", [])
        landing = next((link["url"] for link in links if link["kind"] == "landing"), None)
        pdf = next((link["url"] for link in links if link["kind"] == "pdf"), None)
        sources.append(
            PublicSource(
                provider=s["provider"],
                source_id=s["source_id"],
                landing_url=landing or derived_landing(s["provider"], s["source_id"]),
                pdf_url=pdf,
                version=s.get("version"),
                status=s["status"],
            )
        )
    code_links = [
        PublicCodeLink(
            url=p["link"]["url"],
            origin=p["link"]["origin"],
            verified_official=bool(p["link"].get("verified_official")),
        )
        for p in c.get("links", [])
        if p["link"]["kind"] == "code"
    ]
    update, update_key = _latest_update(events)
    history = [
        PublicEvent(
            kind=e.kind,
            date=e.event_date.isoformat() if e.event_date else None,
            date_basis=e.date_basis,
            source=getattr(e, "provider", None),
        )
        for e in events
    ]
    paper = PublicPaper(
        release_id=release_id,
        id=work_id,
        title=c["title"],
        authors=[
            PublicAuthor(display_name=a["display_name"], orcid=a.get("orcid")) for a in c["authors"]
        ],
        abstract=c.get("abstract"),
        published=PartialDate.model_validate(c["published"]),
        areas=c["areas"],
        topics=c["topics"],
        contributions=c["contributions"],
        foundational=work.foundational,
        status=c["record_status"],
        metadata_issues=c.get("metadata_issues", []),
        sources=sources,
        publications=[
            PublicPublication(
                venue_key=p.get("venue_key"),
                venue_name=p.get("venue_name"),
                year=p.get("year"),
                doi=p.get("doi"),
            )
            for p in c.get("publications", [])
        ]
        or (
            [PublicPublication(venue_key=None, venue_name=None, year=None, doi=c["doi"])]
            if c.get("doi")
            else []
        ),
        code_links=code_links,
        latest_update=update,
        inclusion=PublicInclusion(
            review_status=c["inclusion_provenance"],
            explanation=c["inclusion_explanation"],
            evidence=[
                PublicEvidence(field=e["field"], snippet=e["snippet"], rule_id=e.get("rule_id"))
                for e in c.get("inclusion_evidence", [])
            ],
        ),
        related_work_ids=sorted(related),
        canonical_path=f"{base}papers/{work_id}/",
        bibtex_path=f"{base}citations/{work_id}.bib",
        history=history,
    )
    return paper, update_key


def card(paper: PublicPaper, release_id: str, base: str) -> PaperCard:
    venue = next((p.venue_name for p in paper.publications if p.venue_name), None)
    doi = next((p.doi for p in paper.publications if p.doi), None)
    return PaperCard(
        id=paper.id,
        title=paper.title,
        authors=[a.display_name for a in paper.authors],
        published=paper.published,
        areas=paper.areas,
        topics=paper.topics,
        contributions=paper.contributions,
        foundational=paper.foundational,
        status=paper.status,
        sources=paper.sources,
        venue=venue,
        doi=doi,
        has_code=bool(paper.code_links),
        has_abstract=paper.abstract is not None,
        latest_update=paper.latest_update,
        canonical_path=paper.canonical_path,
        bibtex_path=paper.bibtex_path,
        json_path=f"{base}assets/releases/{release_id}/papers/{paper.id}.json",
    )


# --- deterministic ordering (spec section 12) ----------------------------------------------


def _date_key(p: PublicPaper, descending: bool) -> tuple[Any, ...]:
    interval = p.published.date_interval()
    tie = (PRECISION_RANK[p.published.precision], title_key(p.title), p.id)
    if interval is None:
        return (1, 0, *tie)
    ordinal = interval[0].toordinal()
    return (0, -ordinal if descending else ordinal, *tie)


def newest_key(p: PublicPaper) -> tuple[Any, ...]:
    return _date_key(p, True)


def oldest_key(p: PublicPaper) -> tuple[Any, ...]:
    return _date_key(p, False)


def updated_key(p: PublicPaper) -> tuple[Any, ...]:
    if p.latest_update is None:
        return (1, 0, *newest_key(p))
    return (0, -date.fromisoformat(p.latest_update.date).toordinal(), *newest_key(p))


def ranks(papers: list[PublicPaper]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {p.id: {} for p in papers}
    for name, key in (
        ("rank_newest", newest_key),
        ("rank_oldest", oldest_key),
        ("rank_updated", updated_key),
    ):
        for rank, paper in enumerate(sorted(papers, key=key), start=1):
            out[paper.id][name] = rank
    return out


def in_window(day: date | None, today: date, days: int = WINDOW_DAYS) -> bool:
    return day is not None and 0 <= (today - day).days < days


# --- writing ----------------------------------------------------------------------------------


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _write_list(
    root: Path,
    rel: str,
    name: str,
    papers: list[PublicPaper],
    release_id: str,
    base: str,
    **extra: Any,
) -> int:
    total = len(papers)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    for page in range(1, pages + 1):
        chunk = papers[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        _dump(
            root / rel / f"{page}.json",
            ListPage(
                release_id=release_id,
                list_name=name,
                total=total,
                page=page,
                page_count=pages,
                items=[card(p, release_id, base) for p in chunk],
                **extra,
            ).model_dump(mode="json", by_alias=True),
        )
    return pages


@dataclass
class ExportResult:
    release_id: str
    path: Path
    manifest: dict[str, Any]


def _stream_summaries(
    snapshot: CatalogSnapshot, config: SourcesConfig
) -> tuple[list[SourceSummary], list[dict[str, Any]], str]:
    by_key = {s.stream_key: s for s in snapshot.streams}
    declared = [s for s in config.streams if s.enabled]
    coverage_rows = []
    states = []
    for stream in declared:
        row = by_key.get(stream.key)
        state = row.coverage_state if row is not None and row.coverage_state else "not_started"
        if row is not None and row.coverage_state == "running":
            state = "running"
        states.append(state)
        notes = (row.notes or {}) if row is not None else {}
        coverage_rows.append(
            {
                "stream_key": stream.key,
                "provider": stream.provider,
                "kind": stream.kind,
                "state": state,
                "expected_count": row.expected_count if row is not None else None,
                "seen_count": int(row.seen_count or 0) if row is not None else 0,
                "valid_count": int(row.valid_count or 0) if row is not None else 0,
                "quarantined_count": int(row.quarantined_count or 0) if row is not None else 0,
                "filtered_by_local_rule": int(notes.get("filtered_by_local_rule", 0)),
                "last_attempt_at": row.last_attempt_at.isoformat()
                if row is not None and row.last_attempt_at
                else None,
                "last_success_at": row.last_success_at.isoformat()
                if row is not None and row.last_success_at
                else None,
                "completed_watermark": row.completed_watermark.isoformat()
                if row is not None and row.completed_watermark
                else None,
                "last_error_code": row.last_error_code if row is not None else None,
            }
        )
    summaries = []
    for provider in sorted({s.provider for s in declared}):
        rows = [r for r in coverage_rows if r["provider"] == provider]
        provider_states = {r["state"] for r in rows}
        state = (
            "complete"
            if provider_states == {"complete"}
            else "not_started"
            if provider_states == {"not_started"}
            else "partial"
        )
        summaries.append(
            SourceSummary(
                provider=provider,
                last_attempt_at=max(
                    (r["last_attempt_at"] for r in rows if r["last_attempt_at"]), default=None
                ),
                last_success_at=max(
                    (r["last_success_at"] for r in rows if r["last_success_at"]), default=None
                ),
                completed_watermark=max(
                    (r["completed_watermark"] for r in rows if r["completed_watermark"]),
                    default=None,
                ),
                last_error_code=next(
                    (r["last_error_code"] for r in rows if r["last_error_code"]), None
                ),
                coverage_state=state,
                streams=[r["stream_key"] for r in rows],
            )
        )
    overall = (
        "complete"
        if set(states) == {"complete"}
        else "not_started"
        if set(states) <= {"not_started"}
        else "partial"
    )
    return summaries, coverage_rows, overall


def export_release(
    engine: Engine,
    out: Path,
    *,
    clock: Clock,
    site: SiteConfig,
    sources: SourcesConfig,
    taxonomy: Taxonomy,
    rules_version: str,
    public_site_url: str,
    base_path: str,
    dataset_mode: str = "live",
    revision: str | None = None,
) -> ExportResult:
    release_id = out.name
    if not _RELEASE_ID.match(release_id):
        raise InputError(f"release id {release_id!r} must match {_RELEASE_ID.pattern}")
    if out.exists():
        raise InputError(f"{out} already exists; releases are immutable, choose a new release id")
    snapshot = take_snapshot(engine)
    tmp = out.parent / f".tmp-{release_id}-{uuid.uuid4().hex[:6]}"
    tmp.mkdir(parents=True)
    try:
        manifest = _write_release(
            tmp,
            snapshot,
            release_id=release_id,
            clock=clock,
            site=site,
            sources=sources,
            taxonomy=taxonomy,
            rules_version=rules_version,
            site_url=public_site_url.rstrip("/"),
            base=base_path,
            dataset_mode=dataset_mode,
            revision=revision or code_revision(),
        )
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    with transaction(engine) as conn:
        conn.execute(
            t.releases.insert().values(
                id=new_id(),
                release_key=release_id,
                state="exported",
                catalog_snapshot_at=snapshot.snapshot_at,
                manifest=manifest,
                blob_prefix=str(out),
                created_at=clock.now(),
            )
        )
    return ExportResult(release_id, out, manifest)


def _write_release(
    root: Path,
    snapshot: CatalogSnapshot,
    *,
    release_id: str,
    clock: Clock,
    site: SiteConfig,
    sources: SourcesConfig,
    taxonomy: Taxonomy,
    rules_version: str,
    site_url: str,
    base: str,
    dataset_mode: str,
    revision: str,
) -> dict[str, Any]:
    today = clock.today()
    included_ids = {str(w.id) for w in snapshot.works}
    related: dict[str, set[str]] = {i: set() for i in included_ids}
    for rel in snapshot.relations:
        a = snapshot.aliases.get(str(rel.from_work), str(rel.from_work))
        b = snapshot.aliases.get(str(rel.to_work), str(rel.to_work))
        if a in included_ids and b in included_ids and a != b:
            related[a].add(b)
            related[b].add(a)
    papers: list[PublicPaper] = []
    update_keys: dict[str, str] = {}
    for work in snapshot.works:
        paper, key = build_paper(
            work, snapshot.events.get(work.id, []), sorted(related[str(work.id)]), release_id, base
        )
        papers.append(paper)
        if key:
            update_keys[paper.id] = key
    papers.sort(key=newest_key)

    for paper in papers:
        data = paper.model_dump(mode="json")
        _dump(root / "papers" / f"{paper.id}.json", data)
        (root / "citations").mkdir(parents=True, exist_ok=True)
        (root / "citations" / f"{paper.id}.bib").write_text(bibtex_entry(data), encoding="utf-8")

    active = [p for p in papers if p.status == "active"]
    new = [p for p in active if in_window(p.published.exact(), today)]
    updated = sorted(
        [
            p
            for p in active
            if p.latest_update and in_window(date.fromisoformat(p.latest_update.date), today)
        ],
        key=updated_key,
    )
    window = {"as_of_date": today.isoformat(), "window_days": WINDOW_DAYS}
    _write_list(root, "lists/new", "new", new, release_id, base, **window)
    _write_list(root, "lists/updated", "updated", updated, release_id, base, **window)
    years: dict[str, list[PublicPaper]] = {}
    for paper in active:
        key = str(paper.published.year) if paper.published.year is not None else "unknown"
        years.setdefault(key, []).append(paper)
    available_years = sorted((y for y in years if y != "unknown"), key=int, reverse=True)
    if "unknown" in years:
        available_years.append("unknown")
    for year in available_years:
        _write_list(root, f"lists/archive/{year}", f"archive:{year}", years[year], release_id, base)
    foundations = [p for p in active if p.foundational]
    _write_list(root, "lists/foundations", "foundations", foundations, release_id, base)
    topic_counts: dict[str, int] = {}
    for topic in sorted(taxonomy.topic_ids):
        members = [p for p in active if topic in p.topics]
        topic_counts[topic] = len(members)
        _write_list(root, f"topics/{topic}", f"topic:{topic}", members, release_id, base)

    # Feeds: the same windows as the lists, newest first, at most 100 entries.
    updated_at = clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    (root / "feeds").mkdir(parents=True, exist_ok=True)
    (root / "feeds" / "new.atom").write_bytes(
        build_feed(
            kind="paper",
            title=f"{site.title}: new papers",
            site_url=site_url,
            base_path=base,
            feed_path="feeds/new.atom",
            updated=updated_at,
            entries=[
                {
                    "id_key": p.id,
                    "title": p.title,
                    "path": p.canonical_path,
                    "date": p.published.iso(),
                    "categories": list(p.areas),
                    "summary": "; ".join(a.display_name for a in p.authors)
                    or "Authors unavailable",
                }
                for p in new
            ],
        )
    )
    (root / "feeds" / "updated.atom").write_bytes(
        build_feed(
            kind="update",
            title=f"{site.title}: updated papers",
            site_url=site_url,
            base_path=base,
            feed_path="feeds/updated.atom",
            updated=updated_at,
            entries=[_update_entry(p, update_keys[p.id]) for p in updated],
        )
    )

    summaries, coverage_rows, coverage_status = _stream_summaries(snapshot, sources)
    _dump(
        root / "coverage.json",
        {
            "release_id": release_id,
            "as_of_date": today.isoformat(),
            "coverage_status": coverage_status,
            "streams": coverage_rows,
            "relevance": {
                "works_by_visibility": snapshot.visibility_counts,
                "source_records_total": snapshot.total_records,
                "source_records_classified": snapshot.classified_records,
                "review_provenance_note": (
                    "Automatic rules-based decisions unless a work says otherwise."
                ),
            },
            "notes": "Counts come from this catalog snapshot. Expected totals appear only when a "
            "provider reports them; unknown denominators are never filled in.",
        },
    )
    _dump(
        root / "status.json",
        {
            "release_id": release_id,
            "dataset_mode": dataset_mode,
            "generated_at": clock.now().isoformat(),
            "catalog_snapshot_at": snapshot.snapshot_at.isoformat(),
            "as_of_date": today.isoformat(),
            "sources": [s.model_dump(mode="json") for s in summaries],
            "partial": coverage_status != "complete",
        },
    )
    _write_facets(root, papers, release_id, ranks(papers))
    _dump(
        root / "taxonomy.json",
        {
            "release_id": release_id,
            "version": taxonomy.version,
            "areas": [{"id": a.id, "display_name": a.display_name} for a in taxonomy.areas],
            "topics": [
                {"id": tp.id, "display_name": tp.display_name, "areas": list(tp.areas)}
                for tp in taxonomy.topics
            ],
            "contributions": [
                {"id": c.id, "display_name": c.display_name} for c in taxonomy.contributions
            ],
        },
    )
    _dump(
        root / "aliases.json",
        {"release_id": release_id, "aliases": dict(sorted(snapshot.aliases.items()))},
    )
    schema_dir = root / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "public-paper.schema.json").write_text(
        json.dumps(PublicPaper.model_json_schema(), indent=2, sort_keys=True), encoding="utf-8"
    )

    manifest = Manifest(
        release_id=release_id,
        dataset_mode=dataset_mode,
        generated_at=clock.now().isoformat(),
        catalog_snapshot_at=snapshot.snapshot_at.isoformat(),
        code_revision=revision,
        rules_version=rules_version,
        source_config_hash=sources.config_hash(),
        included_work_count=len(papers),
        source_record_counts=dict(sorted(snapshot.source_record_counts.items())),
        sources=summaries,
        available_years=available_years,
        topic_counts=topic_counts,
        coverage_status=coverage_status,
        public_base_path=base,
        as_of_date=today.isoformat(),
        site_title=site.title,
        site_description=site.description,
        public_site_url=site_url,
    ).model_dump(mode="json")
    _dump(root / "manifest.json", manifest)
    _write_checksums(root, release_id)
    return manifest


def _update_entry(p: PublicPaper, event_key: str) -> dict[str, Any]:
    update = p.latest_update
    assert update is not None
    return {
        "id_key": update_entry_key(event_key),
        "title": p.title,
        "path": p.canonical_path,
        "date": update.date,
        "categories": list(p.areas),
        "summary": f"{update.kind.replace('_', ' ')} ({update.date_basis} date)",
    }


def _write_facets(
    root: Path, papers: list[PublicPaper], release_id: str, rank_map: dict[str, dict[str, int]]
) -> None:
    tokens: dict[str, dict[str, Any]] = {}
    update_tokens: set[str] = set()
    per_paper: dict[str, Any] = {}
    values: dict[str, set[str]] = {
        k: set() for k in ("area", "topic", "source", "venue", "kind", "status")
    }
    for p in papers:
        token = p.published.token()
        interval = p.published.date_interval()
        tokens[token] = {
            "token": token,
            "precision": p.published.precision,
            "start": interval[0].isoformat() if interval else None,
            "end": interval[1].isoformat() if interval else None,
        }
        update_token = f"d:{p.latest_update.date}" if p.latest_update else "unknown"
        if p.latest_update:
            update_tokens.add(update_token)
        venues = sorted({pub.venue_key for pub in p.publications if pub.venue_key})
        identifiers = sorted(
            {
                f"{s.provider}:{s.source_id}"
                if s.provider not in ("crossref", "curated")
                else (f"doi:{s.source_id}" if s.provider == "crossref" else s.source_id)
                for s in p.sources
            }
            | {f"doi:{pub.doi}" for pub in p.publications if pub.doi}
        )
        values["area"].update(p.areas)
        values["topic"].update(p.topics)
        values["source"].update(s.provider for s in p.sources)
        values["venue"].update(venues)
        values["kind"].update(p.contributions)
        values["status"].add(p.status)
        per_paper[p.id] = {
            **rank_map[p.id],
            "publication_period": token,
            "update_period": update_token,
            "has_update": "yes" if p.latest_update else "no",
            "identifiers": identifiers,
            "venues": venues,
            "code": "yes" if p.code_links else "no",
            "foundational": "yes" if p.foundational else "no",
        }
    _dump(
        root / "search-facets.json",
        {
            "release_id": release_id,
            "publication_tokens": [tokens[k] for k in sorted(tokens)],
            "update_tokens": [
                {"token": k, "start": k[2:], "end": k[2:], "precision": "day"}
                for k in sorted(update_tokens)
            ],
            "values": {k: sorted(v) for k, v in values.items()},
        },
    )
    # Per-paper attributes for the search indexer (not downloaded by browsers).
    _dump(root / "search-index-data.json", {"release_id": release_id, "papers": per_paper})


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            yield path


def _write_checksums(root: Path, release_id: str) -> None:
    files = {str(p.relative_to(root)): sha256_hex(p.read_bytes()) for p in _iter_files(root)}
    _dump(
        root / "checksums.json", {"release_id": release_id, "algorithm": "sha256", "files": files}
    )
