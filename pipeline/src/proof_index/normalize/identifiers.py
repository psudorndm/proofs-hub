"""Source identifier normalization (spec section 5.3).

Each parser returns ``None`` when the input is not an unambiguous identifier of
that kind. Nothing here performs network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- IACR ePrint -----------------------------------------------------------

_EPRINT = re.compile(
    r"^(?:oai:eprint\.iacr\.org:|https?://(?:www\.)?eprint\.iacr\.org/|eprint:|iacr:)?"
    r"(?P<year>\d{4})/(?P<num>\d{1,6})(?:\.pdf)?/?$",
    re.IGNORECASE,
)


def normalize_eprint_id(value: str) -> str | None:
    """``2020/0007`` / ``https://eprint.iacr.org/2020/007`` -> ``2020/7``."""
    match = _EPRINT.match(value.strip())
    if not match:
        return None
    year = int(match["year"])
    num = int(match["num"])
    if year < 1996 or num <= 0:  # the ePrint archive starts in 1996
        return None
    return f"{year}/{num}"


def eprint_display_id(source_id: str) -> str:
    """ePrint's display form with at least three digits (``2021/42`` -> ``2021/042``)."""
    year, _, number = source_id.partition("/")
    return f"{year}/{int(number):03d}"


# --- arXiv -------------------------------------------------------------------


@dataclass(frozen=True)
class ArxivId:
    base: str  # e.g. "2101.00001" or "math.GT/0309136"
    version: str | None  # e.g. "v2"


_ARXIV_PREFIX = re.compile(
    r"^(?:oai:arxiv\.org:|arxiv:|https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/)",
    re.IGNORECASE,
)
_ARXIV_MODERN = re.compile(r"^(?P<base>\d{4}\.\d{4,5})(?P<ver>v\d+)?(?:\.pdf)?$")
# Old style: archive[.SUBJ]/YYMMNNN, e.g. hep-th/9901001, math.GT/0309136
_ARXIV_OLD = re.compile(
    r"^(?P<base>[a-z][a-z\-]*(?:\.[A-Za-z]{2})?/\d{7})(?P<ver>v\d+)?(?:\.pdf)?$"
)


def parse_arxiv_id(value: str) -> ArxivId | None:
    text = _ARXIV_PREFIX.sub("", value.strip())
    text = text.rstrip("/")
    match = _ARXIV_MODERN.match(text)
    if match:
        yymm = int(match["base"][2:4])
        if not 1 <= yymm <= 12:
            return None
        digits = match["base"].split(".")[1]
        # Five-digit sequence numbers start in 2015 (1501.*); four-digit before.
        year = int(match["base"][:2])
        if year < 7:  # the YYMM.NNNN scheme starts with 0704
            return None
        if (year >= 15 and len(digits) != 5) or (year < 15 and len(digits) != 4):
            return None
        return ArxivId(match["base"], match["ver"])
    match = _ARXIV_OLD.match(text)
    if match:
        archive, _, number = match["base"].partition("/")
        if "." in archive:
            main, subj = archive.split(".", 1)
            archive = f"{main.lower()}.{subj.upper()}"
        else:
            archive = archive.lower()
        return ArxivId(f"{archive}/{number}", match["ver"])
    return None


# --- DOI -------------------------------------------------------------------

_DOI_PREFIX = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi\.org/|doi:\s*|urn:doi:)", re.IGNORECASE
)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_doi(value: str | None) -> str | None:
    """Strip URL/prefix and whitespace, then lower-case for comparison.

    DOIs are case-insensitive; the original string stays in the raw payload.
    """
    if value is None:
        return None
    text = _DOI_PREFIX.sub("", value.strip()).strip()
    if not _DOI.match(text):
        return None
    return text.lower()


# --- DBLP and curated ---------------------------------------------------------

_DBLP_KEY = re.compile(
    r"^(?:conf|journals|books|series|reference|phd|tr|persons|ms|www|homepages)"
    r"(?:/[A-Za-z0-9_\-.+']+)+$"
)


def normalize_dblp_key(value: str) -> str | None:
    text = value.strip()
    for prefix in ("https://dblp.org/rec/", "https://dblp.uni-trier.de/rec/", "dblp:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    for suffix in (".html", ".xml", ".bib"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text if _DBLP_KEY.match(text) else None


_CURATED = re.compile(r"^curated:[a-z0-9](?:[a-z0-9-]{0,118}[a-z0-9])?$")


def normalize_curated_id(slug_or_id: str) -> str | None:
    text = slug_or_id.strip()
    if not text.startswith("curated:"):
        text = f"curated:{text}"
    return text if _CURATED.match(text) else None


# --- Validation by source ------------------------------------------------------


def is_valid_source_id(source: str, source_id: str) -> bool:
    if source == "eprint":
        return normalize_eprint_id(source_id) == source_id
    if source == "arxiv":
        parsed = parse_arxiv_id(source_id)
        return parsed is not None and parsed.version is None and parsed.base == source_id
    if source == "dblp":
        return normalize_dblp_key(source_id) == source_id
    if source == "crossref":
        return normalize_doi(source_id) == source_id
    if source == "curated":
        return normalize_curated_id(source_id) == source_id
    return False


def qualified_identifier(source: str, source_id: str) -> str:
    """``eprint:2020/7``, ``arxiv:2101.00001``, ``dblp:conf/x/Y20``, ``doi:10.1/x``."""
    if source == "crossref":
        return f"doi:{source_id}"
    if source == "curated":
        return source_id
    return f"{source}:{source_id}"
