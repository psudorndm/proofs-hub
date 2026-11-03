"""arXiv Atom search API: supplemental title/abstract discovery.

One stream holds several partitioned queries (one per configured category). The
cursor is ``{"query_index": i, "start": n}``. When a query reports more results
than the API's documented reachable ceiling, the run fails explicitly instead of
silently truncating; a date-partitioned stream version is then required.

**Status: assumption-based mapping** (no live response observed from this environment).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lxml import etree

from proof_index.adapters.arxiv_oai import explicit_eprint_ids
from proof_index.adapters.base import (
    Checkpoint,
    Cursor,
    HarvestRequest,
    PageStructureError,
    ParseContext,
    ParsedPage,
    ProtocolError,
    StreamPlan,
)
from proof_index.adapters.xmlsafe import parse_xml, text_of
from proof_index.config.sources import SourcesConfig
from proof_index.http.client import RequestSpec
from proof_index.models.source import (
    Author,
    NormalizedRecord,
    ParseFailure,
    SourceLink,
    SourceName,
    Tombstone,
)
from proof_index.normalize.dates import DateParseError, parse_partial_date, parse_timestamp
from proof_index.normalize.identifiers import normalize_doi, parse_arxiv_id
from proof_index.normalize.links import check_url

ATOM_NS = "http://www.w3.org/2005/Atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV_ATOM_NS = "http://arxiv.org/schemas/atom"
_NS = {"atom": ATOM_NS, "os": OPENSEARCH_NS, "arxiv": ARXIV_ATOM_NS}
PARSER_VERSION = "arxiv-atom-v1"


def _quote(term: str) -> str:
    term = term.replace('"', "")
    return f'"{term}"' if " " in term or "-" in term else term


def build_queries(
    params: dict[str, Any], window: tuple[datetime | None, datetime | None]
) -> list[str]:
    categories = [str(c) for c in params.get("categories", [])]
    fields = [str(f) for f in params.get("fields", ["ti", "abs"])]
    terms = [str(t) for t in params.get("terms", [])]
    if not categories or not terms:
        raise ValueError("arxiv_atom stream requires categories and terms")
    term_clause = " OR ".join(f"{f}:{_quote(t)}" for t in terms for f in fields)
    date_clause = ""
    start, end = window
    if start is not None or end is not None:
        lo = start.strftime("%Y%m%d%H%M") if start else "000001010000"
        hi = end.strftime("%Y%m%d%H%M") if end else "999912312359"
        date_clause = f" AND lastUpdatedDate:[{lo} TO {hi}]"
    return [f"cat:{c} AND ({term_clause}){date_clause}" for c in categories]


def _entry_record(entry: etree._Element, ordinal: int) -> NormalizedRecord:
    raw_id = (entry.findtext("atom:id", namespaces=_NS) or "").strip()
    parsed = parse_arxiv_id(raw_id)
    if parsed is None:
        raise ValueError(f"unparseable arXiv entry id {raw_id!r}")
    published = parse_partial_date(entry.findtext("atom:published", namespaces=_NS))
    updated = parse_timestamp(entry.findtext("atom:updated", namespaces=_NS))
    authors = [
        Author(display_name=name.strip())
        for name in (text_of(a.find("atom:name", _NS)) for a in entry.findall("atom:author", _NS))
        if name and name.strip()
    ]
    links: list[SourceLink] = []
    for link in entry.findall("atom:link", _NS):
        checked = check_url(link.get("href"))
        if checked.url is None:
            continue
        if link.get("title") == "pdf":
            links.append(SourceLink(kind="pdf", url=checked.url, origin="source_metadata"))
        elif link.get("rel") == "alternate":
            links.append(SourceLink(kind="landing", url=checked.url, origin="source_metadata"))
        elif link.get("title") == "doi":
            links.append(SourceLink(kind="publisher", url=checked.url, origin="source_metadata"))
    doi_text = text_of(entry.find("arxiv:doi", _NS))
    doi = normalize_doi(doi_text.split()[0]) if doi_text else None
    comment = text_of(entry.find("arxiv:comment", _NS))
    summary = text_of(entry.find("atom:summary", _NS))
    identifiers: dict[str, list[str]] = {}
    refs = explicit_eprint_ids(comment, summary)
    if refs:
        identifiers["eprint"] = refs
    categories = [c.get("term") or "" for c in entry.findall("atom:category", _NS)]
    evidence = {
        "source_id": "feed/entry/id",
        "source_version": "feed/entry/id (vN suffix)",
        "title": "feed/entry/title",
        "authors": "feed/entry/author/name",
        "abstract": "feed/entry/summary",
        "published": "feed/entry/published",
        "source_modified_at": "feed/entry/updated",
        "source_categories": "feed/entry/category/@term",
    }
    return NormalizedRecord(
        source="arxiv",
        source_id=parsed.base,
        title=text_of(entry.find("atom:title", _NS)) or "",
        authors=authors,
        abstract=summary,
        published=published,
        source_modified_at=updated,
        harvest_datestamp=None,
        source_version=parsed.version,
        doi=doi,
        identifiers=identifiers,
        source_categories=[c for c in categories if c],
        venue_key=None,
        venue_name=None,
        links=links,
        status="active",
        evidence=evidence,
    )


def parse_feed(body: bytes, context: ParseContext, queries_count: int) -> ParsedPage:
    root = parse_xml(body, expect_root=(ATOM_NS, "feed"))
    entries = root.findall("atom:entry", _NS)
    for entry in entries:
        entry_id = entry.findtext("atom:id", namespaces=_NS) or ""
        if "arxiv.org/api/errors" in entry_id:
            message = (text_of(entry.find("atom:summary", _NS)) or "unknown error").strip()
            raise ProtocolError("arxiv_api_error", message[:300])
    total_text = root.findtext("os:totalResults", namespaces=_NS)
    start_text = root.findtext("os:startIndex", namespaces=_NS)
    if total_text is None or start_text is None:
        raise PageStructureError("Atom feed lacks opensearch totalResults/startIndex")
    try:
        total, start = int(total_text), int(start_text)
    except ValueError:
        raise PageStructureError("non-integer opensearch counts") from None
    cursor = context.cursor or {"query_index": 0, "start": 0}
    if start != int(cursor.get("start", 0)):
        raise PageStructureError(f"startIndex {start} does not match requested {cursor['start']}")
    limit = int(context.plan_params.get("max_results_per_query", 30000))
    if total > limit:
        raise ProtocolError(
            "result_limit_exceeded",
            f"query {cursor.get('query_index', 0)} reports {total} results (> {limit}); "
            "a date-partitioned stream version is required",
        )
    if not entries and start < total:
        raise PageStructureError(f"empty page at start={start} although total={total}")

    records: list[NormalizedRecord | Tombstone] = []
    failures: list[ParseFailure] = []
    for ordinal, entry in enumerate(entries):
        try:
            records.append(_entry_record(entry, ordinal))
        except (ValueError, DateParseError) as exc:
            raw_id = entry.findtext("atom:id", namespaces=_NS) or ""
            parsed = parse_arxiv_id(raw_id)
            failures.append(
                ParseFailure(
                    source="arxiv",
                    source_id=parsed.base if parsed else None,
                    error_code=type(exc).__name__,
                    message=str(exc)[:500],
                    item_ordinal=ordinal,
                    raw_excerpt=etree.tostring(entry, encoding="unicode")[:2000],
                )
            )
    next_start = start + len(entries)
    query_index = int(cursor.get("query_index", 0))
    next_cursor: Cursor | None
    if next_start < total:
        next_cursor = {"query_index": query_index, "start": next_start}
    elif query_index + 1 < queries_count:
        next_cursor = {"query_index": query_index + 1, "start": 0}
    else:
        next_cursor = None
    updated = parse_timestamp(root.findtext("atom:updated", namespaces=_NS))
    return ParsedPage(
        records=records,
        next_cursor=next_cursor,
        terminal=next_cursor is None,
        provider_response_at=updated,
        reported_total=total,
        quarantined_items=failures,
    )


class ArxivAtomAdapter:
    name = "arxiv_atom"
    provider: SourceName = "arxiv"
    parser_version = PARSER_VERSION

    def __init__(self, config: SourcesConfig) -> None:
        self.config = config

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan:
        stream = self.config.stream(request.stream_key)
        provider = self.config.provider("arxiv")
        params = dict(stream.params)
        params["queries"] = build_queries(params, (request.window_from, request.window_until))
        return StreamPlan(
            provider="arxiv",
            adapter=self.name,
            stream_key=stream.key,
            base_url=provider.endpoints["atom"],
            params=params,
            window_from=request.window_from,
            window_until=request.window_until,
            max_bytes=provider.max_response_bytes,
        )

    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec:
        cursor = cursor or {"query_index": 0, "start": 0}
        queries: list[str] = plan.params["queries"]
        query = queries[int(cursor["query_index"])]
        return RequestSpec(
            provider="arxiv",
            url=plan.base_url,
            params=(
                ("search_query", query),
                ("start", str(int(cursor["start"]))),
                ("max_results", str(int(plan.params.get("page_size", 200)))),
                ("sortBy", "submittedDate"),
                ("sortOrder", "ascending"),
            ),
            accept="application/atom+xml",
            max_bytes=plan.max_bytes,
        )

    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage:
        queries = context.plan_params.get("queries") or []
        return parse_feed(body, context, len(queries))
