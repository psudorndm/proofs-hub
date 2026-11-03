"""Canonical work projection stored in ``works.canonical`` (spec section 11.2).

It is a validated, rebuildable projection of current observations and curation,
never the sole evidence store. ``public_hash`` covers reader-visible content only,
so provenance-only changes do not bump ``canonical_revision``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from proof_index.hashing import sha256_json
from proof_index.models.classification import Area, Contribution, ReviewProvenance
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author, SourceLink, SourceName, SourceStatus

Visibility = Literal["included", "review", "excluded"]
RecordStatus = Literal["active", "withdrawn", "unavailable"]


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["source", "curation"]
    source_record_id: str | None = None
    observation_id: str | None = None
    field: str
    command_id: str | None = None


class CanonicalSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SourceName
    source_id: str
    source_record_id: str
    observation_id: str | None
    version: str | None
    status: SourceStatus
    links: list[SourceLink] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class CanonicalPublication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    provider_key: str
    venue_key: str | None
    venue_name: str | None
    year: int | None
    doi: str | None


class ProvenancedLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    link: SourceLink
    source_record_id: str | None
    provider: str


class InclusionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_record_id: str
    field: str
    snippet: str
    rule_id: str | None


class CanonicalWork(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    title: str
    title_provenance: Provenance
    authors: list[Author]
    authors_provenance: Provenance | None
    abstract: str | None
    abstract_provenance: Provenance | None
    published: PartialDate
    published_provenance: Provenance | None
    doi: str | None
    identifiers: list[str]
    sources: list[CanonicalSource]
    publications: list[CanonicalPublication]
    links: list[ProvenancedLink]
    visibility: Visibility
    areas: list[Area]
    topics: list[str]
    contributions: list[Contribution]
    inclusion_provenance: ReviewProvenance
    inclusion_explanation: str
    inclusion_evidence: list[InclusionEvidence]
    record_status: RecordStatus
    foundational: bool
    metadata_issues: list[str]

    def public_hash(self) -> str:
        data = self.model_dump(
            mode="json",
            exclude={
                "title_provenance",
                "authors_provenance",
                "abstract_provenance",
                "published_provenance",
                "inclusion_evidence",
            },
        )
        for source in data["sources"]:
            source.pop("observation_id", None)
            source.pop("source_record_id", None)
        for link in data["links"]:
            link.pop("source_record_id", None)
        return sha256_json(data)
