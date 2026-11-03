"""arXiv via OAI-PMH with the ``arXiv`` metadata format.

**Status: assumption-based mapping** (no live response observed from this
environment). Field paths follow arXiv's published OAI documentation as recorded
in ``docs/source-contracts.md``. Records outside ``category_filter`` are skipped
locally and counted as filtered (spec section 9: enclosing set + local filter).
"""

from __future__ import annotations

import re
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
from proof_index.models.source import Author, NormalizedRecord, ParseFailure, SourceName, Tombstone
from proof_index.normalize.dates import parse_partial_date
from proof_index.normalize.identifiers import normalize_doi, normalize_eprint_id, parse_arxiv_id

ARXIV_NS = "http://arxiv.org/OAI/arXiv/"
_A = {"a": ARXIV_NS}
PARSER_VERSION = "arxiv-oai-arXiv-v1"

# Explicit ePrint references in comments/abstract: a full ePrint URL, or
# "ePrint (Archive|Report) YYYY/NNNN". Bare "YYYY/NNNN" alone is not enough.
_EPRINT_REF = re.compile(
    r"(?:eprint\.iacr\.org/(\d{4}/\d{1,6})"
    r"|\b(?:IACR\s+)?(?:Cryptology\s+)?ePrint(?:\s+Archive)?(?:\s*,)?(?:\s+(?:Report|Paper))?"
    r"(?:\s+No\.?)?\s+(\d{4}/\d{1,6})\b)",
    re.IGNORECASE,
)


def explicit_eprint_ids(*texts: str | None) -> list[str]:
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _EPRINT_REF.finditer(text):
            normalized = normalize_eprint_id(match.group(1) or match.group(2))
            if normalized:
                found.add(normalized)
    return sorted(found)


def _child_text(meta: etree._Element, name: str) -> str | None:
    value = text_of(meta.find(f"a:{name}", _A))
    return value.strip() if value else None


def _author(el: etree._Element) -> Author | None:
    keyname = _child_text(el, "keyname")
    forenames = _child_text(el, "forenames")
    suffix = _child_text(el, "suffix")
    parts = [p for p in (forenames, keyname, suffix) if p]
    if not parts:
        return None
    return Author(display_name=" ".join(parts), given=forenames, family=keyname)


def map_record(
    item: OaiRecord, context: ParseContext, category_filter: frozenset[str]
) -> NormalizedRecord | Tombstone | None:
    parsed = parse_arxiv_id(item.header.identifier)
    if item.header.deleted:
        if parsed is None:
            raise ValueError(f"unparseable arXiv identifier {item.header.identifier!r}")
        return Tombstone(
            source="arxiv",
            source_id=parsed.base,
            harvest_datestamp=item.header.datestamp,
            evidence={"status": "record/header/@status"},
        )
    meta = item.metadata
    if meta is None:
        raise ValueError("non-deleted record without metadata")
    qname = etree.QName(meta)
    if (qname.namespace, qname.localname) != (ARXIV_NS, "arXiv"):
        raise ValueError(f"unexpected metadata element {{{qname.namespace}}}{qname.localname}")
    meta_id = _child_text(meta, "id")
    from_meta = parse_arxiv_id(meta_id) if meta_id else None
    ident = from_meta or parsed
    if ident is None:
        raise ValueError(f"no arXiv identifier (header {item.header.identifier!r})")

    categories = (_child_text(meta, "categories") or "").split()
    if category_filter and not category_filter.intersection(categories):
        return None

    created = parse_partial_date(_child_text(meta, "created"))
    updated_date = parse_partial_date(_child_text(meta, "updated"))
    modified: datetime | None = None
    exact = updated_date.exact()
    if exact is not None:
        modified = datetime(exact.year, exact.month, exact.day, tzinfo=UTC)

    authors: list[Author] = []
    authors_el = meta.find("a:authors", _A)
    if authors_el is not None:
        for author_el in authors_el.findall("a:author", _A):
            author = _author(author_el)
            if author is not None:
                authors.append(author)

    doi: str | None = None
    extra_dois: list[str] = []
    for token in (_child_text(meta, "doi") or "").split():
        normalized = normalize_doi(token)
        if normalized and doi is None:
            doi = normalized
        elif normalized:
            extra_dois.append(normalized)

    comments = _child_text(meta, "comments")
    abstract = _child_text(meta, "abstract")
    identifiers: dict[str, list[str]] = {}
    eprint_refs = explicit_eprint_ids(comments, abstract)
    if eprint_refs:
        identifiers["eprint"] = eprint_refs
    if extra_dois:
        identifiers["doi"] = extra_dois

    evidence = {
        "source_id": "record/metadata/arXiv/id",
        "title": "record/metadata/arXiv/title",
        "authors": "record/metadata/arXiv/authors/author",
        "abstract": "record/metadata/arXiv/abstract",
        "published": "record/metadata/arXiv/created",
        "harvest_datestamp": "record/header/datestamp",
        "source_categories": "record/metadata/arXiv/categories",
    }
    if modified is not None:
        evidence["source_modified_at"] = "record/metadata/arXiv/updated"
    if eprint_refs:
        evidence["identifiers.eprint"] = "record/metadata/arXiv/comments|abstract (explicit ref)"
    license_url = _child_text(meta, "license")
    if license_url:
        evidence["license"] = "record/metadata/arXiv/license"
    return NormalizedRecord(
        source="arxiv",
        source_id=ident.base,
        title=_child_text(meta, "title") or "",
        authors=authors,
        abstract=abstract,
        published=created if created.precision != "unknown" else PartialDate.unknown(),
        source_modified_at=modified,
        harvest_datestamp=item.header.datestamp,
        source_version=None,  # the arXiv format does not expose version numbers
        doi=doi,
        identifiers=identifiers,
        source_categories=categories,
        venue_key=None,
        venue_name=None,
        links=[],
        status="active",
        evidence=evidence,
    )


def record_failure(item: OaiRecord, exc: Exception) -> ParseFailure:
    parsed = parse_arxiv_id(item.header.identifier)
    return ParseFailure(
        source="arxiv",
        source_id=parsed.base if parsed else None,
        error_code=type(exc).__name__,
        message=str(exc)[:500],
        item_ordinal=item.ordinal,
        raw_excerpt=item.raw_excerpt,
    )


class ArxivOaiAdapter:
    name = "arxiv_oai"
    provider: SourceName = "arxiv"
    parser_version = PARSER_VERSION

    def __init__(self, config: SourcesConfig) -> None:
        self.config = config

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan:
        stream = self.config.stream(request.stream_key)
        provider = self.config.provider("arxiv")
        return StreamPlan(
            provider="arxiv",
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
        category_filter = frozenset(context.plan_params.get("category_filter") or [])
        return parse_list_records(
            body,
            context,
            lambda item, ctx: map_record(item, ctx, category_filter),
            record_failure=record_failure,
        )
