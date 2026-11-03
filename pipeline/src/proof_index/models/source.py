"""Normalized source records (spec sections 5.2–5.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from proof_index.hashing import sha256_json
from proof_index.models.dates import PartialDate
from proof_index.normalize.authors import normalize_orcid
from proof_index.normalize.identifiers import is_valid_source_id, normalize_doi
from proof_index.normalize.links import check_url
from proof_index.normalize.text import clean_optional, clean_text

SourceName = Literal["eprint", "arxiv", "dblp", "crossref", "curated"]
SOURCES: tuple[SourceName, ...] = ("eprint", "arxiv", "dblp", "crossref", "curated")
SourceStatus = Literal["active", "withdrawn", "deleted"]
LinkKind = Literal["landing", "pdf", "code", "publisher", "other"]
LinkOrigin = Literal["source_metadata", "curated"]


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value


class Author(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None

    @field_validator("display_name")
    @classmethod
    def _display(cls, value: str) -> str:
        text = clean_text(value)
        if not text:
            raise ValueError("author display_name must not be empty")
        return text

    @field_validator("given", "family")
    @classmethod
    def _parts(cls, value: str | None) -> str | None:
        return clean_optional(value)

    @field_validator("orcid")
    @classmethod
    def _orcid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_orcid(value)
        if normalized is None:
            raise ValueError("invalid ORCID")
        return normalized


class SourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: LinkKind
    url: str
    origin: LinkOrigin
    verified_official: bool = False

    @field_validator("url")
    @classmethod
    def _safe(cls, value: str) -> str:
        checked = check_url(value)
        if checked.url is None:
            raise ValueError(f"unsafe or invalid URL ({checked.issue})")
        return checked.url


class NormalizedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source: SourceName
    source_id: str
    title: str
    authors: list[Author]
    abstract: str | None
    published: PartialDate
    source_modified_at: datetime | None
    harvest_datestamp: datetime | None
    source_version: str | None
    doi: str | None
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    source_categories: list[str] = Field(default_factory=list)
    venue_key: str | None = None
    venue_name: str | None = None
    links: list[SourceLink] = Field(default_factory=list)
    status: SourceStatus = "active"
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        text = clean_text(value)
        if not text:
            raise ValueError("title must not be empty")
        return text

    @field_validator("abstract", "venue_name", "venue_key", "source_version")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return clean_optional(value)

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_doi(value)
        if normalized is None:
            raise ValueError("invalid DOI")
        return normalized

    @field_validator("source_modified_at", "harvest_datestamp")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @field_validator("identifiers")
    @classmethod
    def _identifiers(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for key in sorted(value):
            items = sorted({clean_text(v) for v in value[key] if clean_text(v)})
            if items:
                out[clean_text(key)] = items
        return out

    @field_validator("links")
    @classmethod
    def _links(cls, value: list[SourceLink]) -> list[SourceLink]:
        seen: set[tuple[str, str]] = set()
        out: list[SourceLink] = []
        for link in value:
            key = (link.kind, link.url)
            if key not in seen:
                seen.add(key)
                out.append(link)
        return out

    @field_validator("source_categories")
    @classmethod
    def _categories(cls, value: list[str]) -> list[str]:
        return sorted({clean_text(v) for v in value if clean_text(v)})

    @model_validator(mode="after")
    def _source_identity(self) -> Self:
        if not is_valid_source_id(self.source, self.source_id):
            raise ValueError(f"source_id {self.source_id!r} is not a normalized {self.source} id")
        return self

    def content_sha256(self) -> str:
        return content_hash(self)


class Tombstone(BaseModel):
    """A provider deletion marker. Carries no invented bibliographic data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tombstone"] = "tombstone"
    source: SourceName
    source_id: str
    harvest_datestamp: datetime | None
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("harvest_datestamp")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @model_validator(mode="after")
    def _source_identity(self) -> Self:
        if not is_valid_source_id(self.source, self.source_id):
            raise ValueError(f"source_id {self.source_id!r} is not a normalized {self.source} id")
        return self

    def content_sha256(self) -> str:
        return sha256_json(
            {"kind": "tombstone", "source": self.source, "source_id": self.source_id}
        )


class ParseFailure(BaseModel):
    """One item in a page that could not be normalized. Preserved in quarantine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SourceName
    source_id: str | None
    error_code: str
    message: str
    item_ordinal: int
    raw_excerpt: str | None = None


# Fields excluded from the semantic content hash (spec 5.4): evidence-path
# formatting and the provider's harvest bookkeeping datestamp. Fetch/run times,
# storage pointers, and parser version live outside the model entirely.
_HASH_EXCLUDED = {"evidence", "harvest_datestamp"}


def hash_projection(record: NormalizedRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude=_HASH_EXCLUDED)
    data["links"] = sorted(data["links"], key=lambda link: (link["kind"], link["url"]))
    # Sets of identifiers/categories are already sorted and deduplicated by validators.
    return data


def content_hash(record: NormalizedRecord) -> str:
    return sha256_json(hash_projection(record))
