"""Curated foundational collection (``curation/foundations.jsonl``, spec section 9).

Every line is validated before anything is staged; one invalid line rejects the
import. The spec's schema example (example.org evidence) is refused outright.
Slugs are stable: correcting a title keeps ``curated:<slug>`` and its URL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from proof_index.errors import DataValidationError
from proof_index.hashing import sha256_json
from proof_index.models.classification import Area, Contribution, Taxonomy
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author, NormalizedRecord, SourceLink
from proof_index.normalize.identifiers import (
    normalize_curated_id,
    normalize_dblp_key,
    normalize_doi,
    normalize_eprint_id,
    parse_arxiv_id,
)
from proof_index.normalize.links import check_url

PARSER_VERSION = "curated-jsonl-v1"
_PLACEHOLDER_HOSTS = ("example.org", "example.com", "example.net")


def _normalize_identifier(kind: str, value: str) -> str | None:
    if kind == "eprint":
        return normalize_eprint_id(value)
    if kind == "dblp":
        return normalize_dblp_key(value)
    if kind == "doi":
        return normalize_doi(value)
    parsed = parse_arxiv_id(value)
    return parsed.base if parsed else None


class CuratedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    curated_id: str
    title: str = Field(min_length=1)
    authors: list[Author]
    published: PartialDate
    doi: str | None = None
    source_identifiers: dict[Literal["eprint", "arxiv", "dblp", "doi"], list[str]] = Field(
        default_factory=dict
    )
    landing_url: str | None = None
    areas: list[Area] = Field(min_length=1)
    topics: list[str] = Field(default_factory=list)
    contributions: list[Contribution] = Field(default_factory=list)
    foundational: bool
    rationale: str = Field(min_length=10)
    evidence_urls: list[str] = Field(min_length=1)
    review_kind: Literal["agent-provisional", "human-reviewed"]

    @field_validator("curated_id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if normalize_curated_id(value) is None or value.startswith("curated:"):
            raise ValueError("curated_id must be a lower-case slug (without 'curated:')")
        return value

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_doi(value)
        if normalized is None:
            raise ValueError("invalid DOI")
        return normalized

    @field_validator("evidence_urls")
    @classmethod
    def _evidence(cls, values: list[str]) -> list[str]:
        out = []
        for url in values:
            checked = check_url(url)
            if checked.url is None:
                raise ValueError(f"unsafe evidence URL ({checked.issue})")
            out.append(checked.url)
        return out

    @model_validator(mode="after")
    def _not_placeholder(self) -> Self:
        urls = [*self.evidence_urls, *([self.landing_url] if self.landing_url else [])]
        if any(any(f"//{h}" in u or f".{h}" in u for h in _PLACEHOLDER_HOSTS) for u in urls):
            raise ValueError("placeholder example.* URLs are schema examples, not evidence")
        if self.title == "Verified bibliographic title":
            raise ValueError("the spec's schema example must not be imported")
        if self.landing_url is not None and check_url(self.landing_url).url is None:
            raise ValueError("unsafe landing_url")
        return self

    def identifiers(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for kind, values in self.source_identifiers.items():
            for value in values:
                normalized = _normalize_identifier(kind, value)
                if normalized is None:
                    raise ValueError(f"invalid {kind} identifier {value!r}")
                out.setdefault(kind, []).append(normalized)
        return out

    def to_record(self) -> NormalizedRecord:
        links = []
        if self.landing_url:
            links.append(SourceLink(kind="landing", url=self.landing_url, origin="curated"))
        return NormalizedRecord(
            source="curated",
            source_id=f"curated:{self.curated_id}",
            title=self.title,
            authors=self.authors,
            abstract=None,
            published=self.published,
            source_modified_at=None,
            harvest_datestamp=None,
            source_version=None,
            doi=self.doi,
            identifiers=self.identifiers(),
            source_categories=[],
            venue_key=None,
            venue_name=None,
            links=links,
            status="active",
            evidence={
                "file": f"curation/foundations.jsonl#{self.curated_id}",
                "evidence_urls": " ".join(self.evidence_urls),
                "review_kind": self.review_kind,
            },
        )

    def labels_hash(self) -> str:
        return sha256_json(
            {
                "areas": self.areas,
                "topics": sorted(self.topics),
                "contributions": self.contributions,
                "foundational": self.foundational,
                "review_kind": self.review_kind,
                "rationale": self.rationale,
            }
        )


def load_entries(path: Path, taxonomy: Taxonomy | None = None) -> list[CuratedEntry]:
    problems: list[str] = []
    entries: list[CuratedEntry] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            entry = CuratedEntry.model_validate(json.loads(line))
            entry.identifiers()
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            problems.append(f"line {number}: {str(exc)[:300]}")
            continue
        if entry.curated_id in seen:
            problems.append(f"line {number}: duplicate curated_id {entry.curated_id}")
        seen.add(entry.curated_id)
        if taxonomy is not None:
            problems += [
                f"line {number}: {p}"
                for p in taxonomy.validate_labels(
                    list(entry.areas), entry.topics, list(entry.contributions)
                )
            ]
        entries.append(entry)
    if problems:
        raise DataValidationError("invalid curated file:\n" + "\n".join(problems))
    return entries
