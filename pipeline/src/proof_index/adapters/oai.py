"""OAI-PMH 2.0 protocol layer shared by the ePrint and arXiv OAI adapters.

Reads namespace URIs, never hard-coded prefixes. Distinguishes ``noRecordsMatch``
(a successful empty window), ``badResumptionToken`` (expired cursor → replay),
and every other protocol error (fail the run).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lxml import etree

from proof_index.adapters.base import (
    Cursor,
    ExpiredCursor,
    PageStructureError,
    ParseContext,
    ParsedPage,
    ProtocolError,
    StreamPlan,
)
from proof_index.adapters.xmlsafe import parse_xml
from proof_index.http.client import RequestSpec
from proof_index.models.source import NormalizedRecord, ParseFailure, Tombstone
from proof_index.normalize.dates import DateParseError, parse_timestamp

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_NS = {"oai": OAI_NS}

KNOWN_ERRORS = {
    "badArgument",
    "badResumptionToken",
    "badVerb",
    "cannotDisseminateFormat",
    "idDoesNotExist",
    "noRecordsMatch",
    "noMetadataFormats",
    "noSetHierarchy",
}


@dataclass(frozen=True)
class OaiHeader:
    identifier: str
    datestamp: datetime | None
    deleted: bool
    set_specs: tuple[str, ...]


@dataclass(frozen=True)
class OaiRecord:
    ordinal: int
    header: OaiHeader
    metadata: etree._Element | None  # the single child of <metadata>
    raw_excerpt: str


def format_oai_datetime(value: datetime, granularity: str) -> str:
    if granularity == "YYYY-MM-DD":
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_records_request(plan: StreamPlan, cursor: Cursor | None) -> RequestSpec:
    if cursor is not None:
        token = cursor.get("resumptionToken")
        if not isinstance(token, str) or not token:
            raise ValueError("OAI cursor requires a non-empty resumptionToken")
        params: tuple[tuple[str, str], ...] = (
            ("verb", "ListRecords"),
            ("resumptionToken", token),
        )
    else:
        granularity = str(plan.params.get("granularity", "YYYY-MM-DD"))
        items = [("verb", "ListRecords"), ("metadataPrefix", str(plan.params["metadata_prefix"]))]
        if plan.params.get("set"):
            items.append(("set", str(plan.params["set"])))
        if plan.window_from is not None:
            items.append(("from", format_oai_datetime(plan.window_from, granularity)))
        if plan.window_until is not None:
            items.append(("until", format_oai_datetime(plan.window_until, granularity)))
        params = tuple(items)
    return RequestSpec(
        provider=plan.provider,
        url=plan.base_url,
        params=params,
        accept="application/xml, text/xml",
        max_bytes=plan.max_bytes,
    )


def verb_request(plan: StreamPlan, verb: str, **extra: str) -> RequestSpec:
    return RequestSpec(
        provider=plan.provider,
        url=plan.base_url,
        params=(("verb", verb), *sorted(extra.items())),
        accept="application/xml, text/xml",
        max_bytes=plan.max_bytes,
    )


def parse_envelope(body: bytes) -> tuple[etree._Element, datetime | None]:
    root = parse_xml(body, expect_root=(OAI_NS, "OAI-PMH"))
    response_date_el = root.find("oai:responseDate", _NS)
    if response_date_el is None or not (response_date_el.text or "").strip():
        raise PageStructureError("OAI-PMH response has no responseDate")
    try:
        response_date = parse_timestamp(response_date_el.text)
    except DateParseError as exc:
        raise PageStructureError(f"invalid responseDate ({exc})") from None
    return root, response_date


def raise_for_errors(root: etree._Element) -> bool:
    """Raise on protocol errors. Returns True for a lone ``noRecordsMatch``."""
    errors = root.findall("oai:error", _NS)
    if not errors:
        return False
    codes = [(e.get("code") or "", (e.text or "").strip()) for e in errors]
    if len(codes) == 1 and codes[0][0] == "noRecordsMatch":
        return True
    for code, message in codes:
        if code == "badResumptionToken":
            raise ExpiredCursor(code, message or "resumption token rejected")
    code, message = codes[0]
    if code not in KNOWN_ERRORS:
        raise ProtocolError(code or "unknown", f"unknown OAI error: {message}")
    raise ProtocolError(code, message or code)


def iter_records(container: etree._Element) -> Iterator[OaiRecord]:
    for ordinal, record in enumerate(container.findall("oai:record", _NS)):
        header_el = record.find("oai:header", _NS)
        if header_el is None:
            raise PageStructureError(f"record {ordinal} has no header")
        ident = (header_el.findtext("oai:identifier", namespaces=_NS) or "").strip()
        stamp_text = header_el.findtext("oai:datestamp", namespaces=_NS)
        try:
            stamp = parse_timestamp(stamp_text)
        except DateParseError:
            stamp = None
        header = OaiHeader(
            identifier=ident,
            datestamp=stamp,
            deleted=header_el.get("status") == "deleted",
            set_specs=tuple((s.text or "").strip() for s in header_el.findall("oai:setSpec", _NS)),
        )
        metadata_el = record.find("oai:metadata", _NS)
        payload = None
        if metadata_el is not None:
            children = [c for c in metadata_el if isinstance(c.tag, str)]
            payload = children[0] if len(children) == 1 else None
            if len(children) != 1:
                raise PageStructureError(f"record {ordinal} metadata must have one child")
        excerpt = etree.tostring(record, encoding="unicode")[:2000]
        yield OaiRecord(ordinal, header, payload, excerpt)


def parse_resumption(container: etree._Element) -> tuple[Cursor | None, int | None]:
    """Return (next cursor or None when terminal, completeListSize)."""
    token_el = container.find("oai:resumptionToken", _NS)
    if token_el is None:
        return None, None
    total_attr = token_el.get("completeListSize")
    total: int | None = None
    if total_attr is not None:
        try:
            total = int(total_attr)
        except ValueError:
            raise PageStructureError("completeListSize is not an integer") from None
    token = (token_el.text or "").strip()
    if not token:
        return None, total  # an empty token marks the last page
    cursor: dict[str, Any] = {"resumptionToken": token}
    if token_el.get("cursor") is not None:
        cursor["cursor"] = token_el.get("cursor")
    if token_el.get("expirationDate") is not None:
        cursor["expirationDate"] = token_el.get("expirationDate")
    if total is not None:
        cursor["completeListSize"] = total
    return cursor, total


RecordMapper = Callable[[OaiRecord, ParseContext], NormalizedRecord | Tombstone | None]


def parse_list_records(
    body: bytes,
    context: ParseContext,
    mapper: RecordMapper,
    *,
    record_failure: Callable[[OaiRecord, Exception], ParseFailure],
) -> ParsedPage:
    """Parse one ListRecords page. ``mapper`` returns ``None`` to filter a record out."""
    root, response_date = parse_envelope(body)
    if raise_for_errors(root):
        return ParsedPage(
            records=[],
            next_cursor=None,
            terminal=True,
            provider_response_at=response_date,
            reported_total=0,
            no_records_match=True,
        )
    container = root.find("oai:ListRecords", _NS)
    if container is None:
        raise PageStructureError("OAI-PMH response has neither ListRecords nor error")
    records: list[NormalizedRecord | Tombstone] = []
    failures: list[ParseFailure] = []
    filtered = 0
    seen_ids: set[str] = set()
    for item in iter_records(container):
        try:
            mapped = mapper(item, context)
        except (ValueError, DateParseError) as exc:  # pydantic ValidationError is a ValueError
            failures.append(record_failure(item, exc))
            continue
        if mapped is None:
            filtered += 1
            continue
        key = f"{mapped.source}:{mapped.source_id}"
        if key in seen_ids:
            # The same record twice in one page: keep the first, quarantine the repeat.
            failures.append(record_failure(item, ValueError(f"duplicate record {key} in page")))
            continue
        seen_ids.add(key)
        records.append(mapped)
    cursor, total = parse_resumption(container)
    return ParsedPage(
        records=records,
        next_cursor=cursor,
        terminal=cursor is None,
        provider_response_at=response_date,
        reported_total=total,
        quarantined_items=failures,
        filtered_count=filtered,
    )
