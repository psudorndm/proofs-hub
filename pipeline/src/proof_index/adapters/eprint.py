"""IACR ePrint Archive via OAI-PMH ``oai_dc``.

**Status: assumption-based mapping.** No live ePrint response has been observed
(the planning environment received HTTP 403 and this build environment's egress
policy blocks the host). Every field mapping below is a documented assumption
recorded in ``docs/source-contracts.md`` and must be verified with
``proof-index sources verify --source eprint --bounded`` before live claims.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lxml import etree

from proof_index.adapters.base import (
    Checkpoint,
    Cursor,
    HarvestRequest,
    ParseContext,
    ParsedPage,
    StreamPlan,
)
from proof_index.adapters.oai import OaiRecord, list_records_request, parse_list_records
from proof_index.adapters.xmlsafe import text_of
from proof_index.config.sources import SourcesConfig
from proof_index.http.client import RequestSpec
from proof_index.models.dates import PartialDate
from proof_index.models.source import (
    Author,
    NormalizedRecord,
    ParseFailure,
    SourceLink,
    SourceName,
    Tombstone,
)
from proof_index.normalize.dates import DateParseError, parse_partial_date
from proof_index.normalize.identifiers import normalize_doi, normalize_eprint_id
from proof_index.normalize.links import check_url

OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"
DC_NS = "http://purl.org/dc/elements/1.1/"
_DC = {"dc": DC_NS}

PARSER_VERSION = "eprint-oai-dc-v1"


def _texts(meta: etree._Element, field: str) -> list[str]:
    values = []
    for el in meta.findall(f"dc:{field}", _DC):
        text = text_of(el)
        if text is not None:
            values.append(text.strip())
    return values


def map_record(item: OaiRecord, context: ParseContext) -> NormalizedRecord | Tombstone | None:
    source_id = normalize_eprint_id(item.header.identifier)
    meta = item.metadata
    if source_id is None and meta is not None:
        for ident in _texts(meta, "identifier"):
            source_id = normalize_eprint_id(ident)
            if source_id:
                break
    if source_id is None:
        raise ValueError(f"no ePrint identifier in header {item.header.identifier!r}")

    if item.header.deleted:
        return Tombstone(
            source="eprint",
            source_id=source_id,
            harvest_datestamp=item.header.datestamp,
            evidence={"status": "record/header/@status"},
        )
    if meta is None:
        raise ValueError("non-deleted record without metadata")
    qname = etree.QName(meta)
    if (qname.namespace, qname.localname) != (OAI_DC_NS, "dc"):
        raise ValueError(f"unexpected metadata element {qname.localname}")

    titles = _texts(meta, "title")
    creators = _texts(meta, "creator")
    descriptions = _texts(meta, "description")
    dates: list[PartialDate] = []
    for raw in _texts(meta, "date"):
        try:
            dates.append(parse_partial_date(raw))
        except DateParseError:
            continue  # unparseable optional date; the raw value stays in the blob
    known = [d for d in dates if d.date_interval() is not None]
    known.sort(key=lambda d: d.date_interval() or ())
    published = known[0] if known else PartialDate.unknown()
    modified: datetime | None = None
    if len(known) >= 2:
        latest = known[-1]
        interval = latest.date_interval()
        if interval is not None and latest != published:
            modified = datetime(interval[0].year, interval[0].month, interval[0].day, tzinfo=UTC)

    links: list[SourceLink] = []
    doi: str | None = None
    for ident in _texts(meta, "identifier") + _texts(meta, "relation"):
        maybe_doi = normalize_doi(ident)
        if maybe_doi:
            doi = doi or maybe_doi
            continue
        checked = check_url(ident)
        if checked.url is None:
            continue
        kind = "pdf" if checked.url.lower().endswith(".pdf") else "landing"
        if normalize_eprint_id(checked.url) is None and kind == "landing":
            kind = "other"
        links.append(SourceLink(kind=kind, url=checked.url, origin="source_metadata"))

    evidence = {
        "source_id": "record/header/identifier",
        "title": "record/metadata/dc/title",
        "authors": "record/metadata/dc/creator",
        "abstract": "record/metadata/dc/description[1]",
        "published": "record/metadata/dc/date (earliest; assumption)",
        "harvest_datestamp": "record/header/datestamp",
    }
    if modified is not None:
        evidence["source_modified_at"] = "record/metadata/dc/date (latest; assumption)"
    return NormalizedRecord(
        source="eprint",
        source_id=source_id,
        title=titles[0] if titles else "",
        authors=[Author(display_name=c) for c in creators],
        abstract=descriptions[0] if descriptions else None,
        published=published,
        source_modified_at=modified,
        harvest_datestamp=item.header.datestamp,
        source_version=None,
        doi=doi,
        identifiers={},
        source_categories=_texts(meta, "subject"),
        venue_key=None,
        venue_name=None,
        links=links,
        status="active",
        evidence=evidence,
    )


def record_failure(item: OaiRecord, exc: Exception) -> ParseFailure:
    return ParseFailure(
        source="eprint",
        source_id=normalize_eprint_id(item.header.identifier),
        error_code=type(exc).__name__,
        message=str(exc)[:500],
        item_ordinal=item.ordinal,
        raw_excerpt=item.raw_excerpt,
    )


class EprintOaiAdapter:
    name = "eprint"
    provider: SourceName = "eprint"
    parser_version = PARSER_VERSION

    def __init__(self, config: SourcesConfig) -> None:
        self.config = config

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan:
        stream = self.config.stream(request.stream_key)
        provider = self.config.provider("eprint")
        return StreamPlan(
            provider="eprint",
            adapter=self.name,
            stream_key=stream.key,
            base_url=provider.endpoints["oai"],
            params=dict(stream.params),
            window_from=request.window_from,
            window_until=request.window_until,
            max_bytes=provider.max_response_bytes,
        )

    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec:
        return list_records_request(plan, cursor)

    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage:
        return parse_list_records(body, context, map_record, record_failure=record_failure)
