"""Crossref REST API DOI enrichment (spec section 9).

One request per planned DOI (the list is fixed per run and stored on the run).
A 404 is an item-level ``doi_not_found`` quarantine, not a failed run. Container
DOIs (proceedings volumes, journals, issues) are quarantined as ``container_doi``
so they can never attach to or merge constituent papers.

Date precedence: the earliest supported publication manifestation among
``published-print``, ``published-online``, ``published``, and ``issued``. Indexing,
deposit, and creation dates are never publication dates; ``deposited`` is used only
as the modification ordering stamp. The JATS abstract is not used in v1.

**Status: assumption-based mapping** (no live response observed from this environment).
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from typing import Any
from urllib.parse import quote

from proof_index.adapters.base import (
    Checkpoint,
    Cursor,
    HarvestRequest,
    PageStructureError,
    ParseContext,
    ParsedPage,
    StreamPlan,
)
from proof_index.config.sources import SourcesConfig
from proof_index.http.client import RequestSpec
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author, NormalizedRecord, ParseFailure, SourceName
from proof_index.normalize.authors import normalize_orcid
from proof_index.normalize.dates import DateParseError, from_parts, parse_timestamp
from proof_index.normalize.identifiers import normalize_doi, parse_arxiv_id

PARSER_VERSION = "crossref-works-v1"
DATE_FIELDS = ("published-print", "published-online", "published", "issued")
CONTAINER_TYPES = frozenset(
    {
        "proceedings",
        "proceedings-series",
        "journal",
        "journal-volume",
        "journal-issue",
        "book-series",
        "book-set",
        "report-series",
    }
)
_TAG = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def _date(parts_holder: Any) -> PartialDate | None:
    try:
        parts = parts_holder["date-parts"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not parts or parts[0] is None:
        return None
    try:
        return from_parts(*parts[:3])
    except (DateParseError, TypeError):
        return None


def map_work(message: dict[str, Any], requested_doi: str) -> NormalizedRecord:
    doi = normalize_doi(str(message.get("DOI", "")))
    if doi is None:
        raise ValueError("Crossref work has no valid DOI")
    if doi != requested_doi:
        raise ValueError(f"Crossref returned DOI {doi} for request {requested_doi}")
    work_type = str(message.get("type", ""))
    if work_type in CONTAINER_TYPES:
        raise ContainerDoi(f"DOI {doi} is a {work_type}, not a paper")
    titles = message.get("title") or []
    title = _plain(str(titles[0])) if titles else ""
    authors = []
    for person in message.get("author") or []:
        given, family = person.get("given"), person.get("family")
        name = " ".join(p for p in (given, family) if p) or person.get("name") or ""
        if not name.strip():
            continue
        authors.append(
            Author(
                display_name=name,
                given=given,
                family=family,
                orcid=normalize_orcid(person.get("ORCID")),
            )
        )
    candidates: list[tuple[date, int, str, PartialDate]] = []
    for name in DATE_FIELDS:
        value = _date(message.get(name))
        if value is not None and (interval := value.date_interval()) is not None:
            rank = {"day": 0, "month": 1, "year": 2}[value.precision]
            candidates.append((interval[0], rank, name, value))
    candidates.sort()
    published = candidates[0][3] if candidates else PartialDate.unknown()
    deposited = (message.get("deposited") or {}).get("date-time")
    try:
        modified = parse_timestamp(deposited) if deposited else None
    except DateParseError:
        modified = None
    identifiers: dict[str, list[str]] = {}
    for relation, items in (message.get("relation") or {}).items():
        if relation not in ("has-preprint", "is-preprint-of", "is-version-of", "has-version"):
            continue
        for item in items or []:
            kind, ident = item.get("id-type"), str(item.get("id", ""))
            if kind == "doi" and (d := normalize_doi(ident)):
                identifiers.setdefault("doi", []).append(d)
            elif kind == "arxiv" and (a := parse_arxiv_id(ident)):
                identifiers.setdefault("arxiv", []).append(a.base)
    container = message.get("container-title") or []
    evidence = {
        "title": "message.title[0] (markup stripped)",
        "authors": "message.author (editors excluded)",
        "doi": "message.DOI",
        "venue_name": "message.container-title[0]",
        "source_modified_at": "message.deposited.date-time (ordering only)",
        "abstract": "not used (JATS abstract ignored in v1)",
    }
    if candidates:
        evidence["published"] = f"message.{candidates[0][2]} (earliest manifestation)"
    return NormalizedRecord(
        source="crossref",
        source_id=doi,
        title=title,
        authors=authors,
        abstract=None,
        published=published,
        source_modified_at=modified,
        harvest_datestamp=None,
        source_version=None,
        doi=doi,
        identifiers=identifiers,
        source_categories=[work_type] if work_type else [],
        venue_key=None,
        venue_name=_plain(str(container[0])) if container else None,
        links=[],
        status="active",
        evidence=evidence,
    )


class ContainerDoi(ValueError):
    pass


class CrossrefAdapter:
    name = "crossref"
    provider: SourceName = "crossref"
    parser_version = PARSER_VERSION

    def __init__(self, config: SourcesConfig) -> None:
        self.config = config

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan:
        provider = self.config.provider("crossref")
        dois = [d for d in (normalize_doi(x) for x in request.params.get("dois", [])) if d]
        if not dois:
            raise ValueError("Crossref enrichment run has no DOIs")
        params: dict[str, Any] = {"dois": dois}
        if request.params.get("mailto"):
            params["mailto"] = request.params["mailto"]
        return StreamPlan(
            provider="crossref",
            adapter=self.name,
            stream_key=request.stream_key,
            base_url=provider.endpoints["works"],
            params=params,
            max_bytes=provider.max_response_bytes,
        )

    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec:
        position = int((cursor or {}).get("position", 0))
        doi = plan.params["dois"][position]
        params = (("mailto", plan.params["mailto"]),) if plan.params.get("mailto") else ()
        return RequestSpec(
            provider="crossref",
            url=f"{plan.base_url}/{quote(doi, safe='')}",
            params=params,
            accept="application/json",
            max_bytes=plan.max_bytes,
            ok_statuses=frozenset({404}),
        )

    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage:
        dois: list[str] = context.plan_params["dois"]
        position = int((context.cursor or {}).get("position", 0))
        doi = dois[position]
        next_cursor = {"position": position + 1} if position + 1 < len(dois) else None
        common = {
            "next_cursor": next_cursor,
            "terminal": next_cursor is None,
            "provider_response_at": None,
            "reported_total": None,
        }
        if context.http_status == 404:
            return ParsedPage(
                records=[],
                quarantined_items=[
                    ParseFailure(
                        source="crossref",
                        source_id=doi,
                        error_code="doi_not_found",
                        message=f"Crossref has no work for {doi}",
                        item_ordinal=0,
                    )
                ],
                **common,
            )
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PageStructureError("Crossref response is not JSON") from None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "ok"
            or payload.get("message-type") != "work"
            or not isinstance(payload.get("message"), dict)
        ):
            raise PageStructureError("Crossref response is not an ok work message")
        try:
            record = map_work(payload["message"], doi)
        except ContainerDoi as exc:
            return ParsedPage(
                records=[],
                quarantined_items=[
                    ParseFailure(
                        source="crossref",
                        source_id=doi,
                        error_code="container_doi",
                        message=str(exc),
                        item_ordinal=0,
                    )
                ],
                **common,
            )
        except ValueError as exc:
            return ParsedPage(
                records=[],
                quarantined_items=[
                    ParseFailure(
                        source="crossref",
                        source_id=doi,
                        error_code=type(exc).__name__,
                        message=str(exc)[:500],
                        item_ordinal=0,
                    )
                ],
                **common,
            )
        return ParsedPage(records=[record], **common)
