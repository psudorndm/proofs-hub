"""DBLP XML dump: streaming parser yielding bounded batches (spec sections 7 and 9).

The dump is never loaded as a whole. ``iter_batches`` re-streams it and skips
record ordinals that are already durable, so a resume never seeks into
compressed data. Ordinals count every top-level record element in document
order (byte-independent). A different dump file is a different snapshot.

Entities: DBLP's XML references ``dblp.dtd`` for character entities. A
constrained resolver serves only a local copy of that DTD; any other external
reference is refused and the network is never used.

**Status: assumption-based mapping** (no live dump processed in this environment).
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import yaml
from lxml import etree

from proof_index.adapters.base import PageStructureError, ParsedPage
from proof_index.adapters.xmlsafe import text_of
from proof_index.errors import ConfigError
from proof_index.models.dates import PartialDate
from proof_index.models.source import (
    Author,
    NormalizedRecord,
    ParseFailure,
    SourceLink,
    Tombstone,
)
from proof_index.normalize.dates import DateParseError, from_parts, parse_timestamp
from proof_index.normalize.identifiers import (
    normalize_dblp_key,
    normalize_doi,
    normalize_eprint_id,
    parse_arxiv_id,
)
from proof_index.normalize.links import check_url

PARSER_VERSION = "dblp-xml-v1"
RECORD_TAGS = (
    "article",
    "inproceedings",
    "proceedings",
    "book",
    "incollection",
    "phdthesis",
    "mastersthesis",
    "www",
    "data",
)
PAPER_TAGS = frozenset({"article", "inproceedings", "incollection"})
_DISAMBIGUATION = re.compile(r"\s+\d{4}$")


@dataclass(frozen=True)
class Venue:
    key: str
    name: str
    cryptographic: bool
    candidate: bool


def load_venues(path: Path = Path("config/venues.yaml")) -> dict[str, Venue]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"venue registry not found: {path}") from None
    venues = {}
    for item in data.get("venues", []):
        venue = Venue(
            str(item["key"]),
            str(item["name"]),
            bool(item.get("cryptographic")),
            bool(item.get("candidate", True)),
        )
        if venue.key in venues:
            raise ConfigError(f"duplicate venue key {venue.key}")
        venues[venue.key] = venue
    return venues


def venue_key_of(dblp_key: str) -> str:
    parts = dblp_key.split("/")
    return "/".join(parts[:2]) if len(parts) >= 3 else dblp_key


class _DtdResolver(etree.Resolver):  # type: ignore[misc]
    def __init__(self, dtd_path: Path | None) -> None:
        super().__init__()
        self.dtd_path = dtd_path

    def resolve(self, system_url: str | None, public_id: str | None, context: Any) -> Any:
        name = (system_url or "").rsplit("/", 1)[-1]
        if name == "dblp.dtd" and self.dtd_path is not None:
            return self.resolve_filename(str(self.dtd_path), context)
        # Refuse anything else by resolving to an empty document.
        return self.resolve_string("", context)


def open_dump(path: Path) -> IO[bytes]:
    if path.suffix == ".gz":
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return path.open("rb")


def _clean_name(name: str) -> str:
    return _DISAMBIGUATION.sub("", name.strip())


def map_element(elem: etree._Element) -> NormalizedRecord:
    raw_key = elem.get("key") or ""
    key = normalize_dblp_key(raw_key)
    if key is None:
        raise ValueError(f"invalid DBLP key {raw_key!r}")
    title = text_of(elem.find("title")) or ""
    title = title.strip()
    if title.endswith(".") and not title.endswith(".."):
        title = title[:-1]  # DBLP titles conventionally end with a period
    authors = [
        Author(display_name=_clean_name(name))
        for name in (text_of(a) for a in elem.findall("author"))
        if name and name.strip()
    ]
    year_text = (elem.findtext("year") or "").strip()
    try:
        published = from_parts(year_text) if year_text else PartialDate.unknown()
    except DateParseError:
        published = PartialDate.unknown()
    venue_name = (elem.findtext("journal") or elem.findtext("booktitle") or "").strip() or None
    doi: str | None = None
    identifiers: dict[str, list[str]] = {}
    links: list[SourceLink] = []
    for ee in elem.findall("ee"):
        url = (ee.text or "").strip()
        if (d := normalize_doi(url)) is not None:
            doi = doi or d
            continue
        if (e := normalize_eprint_id(url)) is not None:
            identifiers.setdefault("eprint", []).append(e)
        elif (a := parse_arxiv_id(url)) is not None:
            identifiers.setdefault("arxiv", []).append(a.base)
        checked = check_url(url)
        if checked.url is not None:
            links.append(
                SourceLink(
                    kind="publisher" if ee.get("type") != "oa" else "landing",
                    url=checked.url,
                    origin="source_metadata",
                )
            )
    mdate = elem.get("mdate")
    try:
        modified = parse_timestamp(mdate) if mdate else None
    except DateParseError:
        modified = None
    status = "withdrawn" if elem.get("publtype") == "withdrawn" else "active"
    return NormalizedRecord(
        source="dblp",
        source_id=key,
        title=title,
        authors=authors,
        abstract=None,
        published=published,
        source_modified_at=modified,
        harvest_datestamp=None,
        source_version=None,
        doi=doi,
        identifiers=identifiers,
        source_categories=[elem.tag],
        venue_key=venue_key_of(key),
        venue_name=venue_name,
        links=links,
        status=status,
        evidence={
            "source_id": "@key",
            "title": "title (inline markup flattened; trailing period removed)",
            "authors": "author (ordered; editors excluded; numeric disambiguators removed)",
            "published": "year",
            "source_modified_at": "@mdate",
            "venue_name": "journal|booktitle",
            "doi": "ee (doi.org)",
        },
    )


Relevance = Callable[[str], bool]


def iter_batches(
    source: Callable[[], IO[bytes]],
    *,
    after_ordinal: int,
    batch_size: int,
    venues: dict[str, Venue],
    title_is_relevant: Relevance,
    dtd_path: Path | None = None,
    huge_tree: bool = False,
) -> Iterator[ParsedPage]:
    """Yield ParsedPage batches for record ordinals > ``after_ordinal``."""
    handle = source()
    try:
        context = etree.iterparse(
            handle,
            events=("end",),
            tag=RECORD_TAGS,
            load_dtd=dtd_path is not None,
            resolve_entities=dtd_path is not None,
            no_network=True,
            huge_tree=huge_tree,
            dtd_validation=False,
        )
        context.resolvers.add(_DtdResolver(dtd_path))
        records: list[NormalizedRecord | Tombstone] = []
        failures: list[ParseFailure] = []
        filtered = 0
        last = after_ordinal
        ordinal = -1
        try:
            for _event, elem in context:
                if elem.getparent() is None or elem.getparent().getparent() is not None:
                    continue  # only direct children of <dblp>
                ordinal += 1
                if ordinal > after_ordinal:
                    last = ordinal
                    if elem.tag not in PAPER_TAGS:
                        filtered += 1
                    else:
                        try:
                            record = map_element(elem)
                        except ValueError as exc:
                            failures.append(
                                ParseFailure(
                                    source="dblp",
                                    source_id=normalize_dblp_key(elem.get("key") or ""),
                                    error_code=type(exc).__name__,
                                    message=str(exc)[:500],
                                    item_ordinal=len(records) + len(failures) + filtered,
                                    raw_excerpt=etree.tostring(elem, encoding="unicode")[:2000],
                                )
                            )
                        else:
                            venue = venues.get(record.venue_key or "")
                            if (venue is not None and venue.candidate) or title_is_relevant(
                                record.title
                            ):
                                records.append(record)
                            else:
                                filtered += 1
                elem.clear()
                parent = elem.getparent()
                while parent is not None and elem.getprevious() is not None:
                    del parent[0]
                if len(records) + len(failures) + filtered >= batch_size:
                    yield ParsedPage(
                        records=records,
                        next_cursor={"after_ordinal": last},
                        terminal=False,
                        provider_response_at=None,
                        reported_total=None,
                        quarantined_items=failures,
                        filtered_count=filtered,
                    )
                    records, failures, filtered = [], [], 0
        except etree.XMLSyntaxError as exc:
            raise PageStructureError(
                f"DBLP dump is malformed or truncated ({exc.msg[:120]})"
            ) from None
        yield ParsedPage(
            records=records,
            next_cursor=None,
            terminal=True,
            provider_response_at=None,
            reported_total=None,
            quarantined_items=failures,
            filtered_count=filtered,
        )
    finally:
        handle.close()
