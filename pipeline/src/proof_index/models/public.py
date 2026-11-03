"""Public release models (spec section 13). The JSON Schema is generated from these.

Public payloads never carry database credentials, request headers, reviewer
contact details, or internal error traces. Every payload carries ``release_id``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from proof_index.models.classification import Area, Contribution
from proof_index.models.dates import PartialDate

PUBLIC_SCHEMA_VERSION = 1
PAGE_SIZE = 25

Provider = Literal["eprint", "arxiv", "dblp", "crossref", "curated"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PublicAuthor(_Model):
    display_name: str
    orcid: str | None


class PublicSource(_Model):
    provider: Provider
    source_id: str
    landing_url: str | None
    pdf_url: str | None
    version: str | None
    status: Literal["active", "withdrawn", "deleted"]


class PublicPublication(_Model):
    venue_key: str | None
    venue_name: str | None
    year: int | None
    doi: str | None


class PublicCodeLink(_Model):
    url: str
    origin: str
    verified_official: bool


class PublicUpdate(_Model):
    kind: Literal["source_revision", "metadata_updated", "withdrawn", "restored"]
    date: str
    date_basis: Literal["source", "observed"]
    source_record_id: str | None


class PublicEvidence(_Model):
    field: str
    snippet: str
    rule_id: str | None


class PublicInclusion(_Model):
    review_status: Literal["automatic", "agent-provisional", "human-reviewed"]
    explanation: str
    evidence: list[PublicEvidence]


class PublicEvent(_Model):
    kind: str
    date: str | None
    date_basis: Literal["source", "observed", "none"]
    source: str | None


class PublicPaper(_Model):
    schema_version: Literal[1] = 1
    release_id: str
    id: str
    title: str
    authors: list[PublicAuthor]
    abstract: str | None
    published: PartialDate
    areas: list[Area]
    topics: list[str]
    contributions: list[Contribution]
    foundational: bool
    status: Literal["active", "withdrawn", "unavailable"]
    metadata_issues: list[str]
    sources: list[PublicSource]
    publications: list[PublicPublication]
    code_links: list[PublicCodeLink]
    latest_update: PublicUpdate | None
    inclusion: PublicInclusion
    related_work_ids: list[str]
    canonical_path: str
    bibtex_path: str
    # Additive (schema v1): full event history for the detail page.
    history: list[PublicEvent] = Field(default_factory=list)


class PaperCard(_Model):
    """Small list projection: no full abstract, evidence, or history."""

    id: str
    title: str
    authors: list[str]
    published: PartialDate
    areas: list[Area]
    topics: list[str]
    contributions: list[Contribution]
    foundational: bool
    status: Literal["active", "withdrawn", "unavailable"]
    sources: list[PublicSource]
    venue: str | None
    doi: str | None
    has_code: bool
    has_abstract: bool
    latest_update: PublicUpdate | None
    canonical_path: str
    bibtex_path: str
    json_path: str


class ListPage(_Model):
    release_id: str
    list_name: str = Field(alias="list")
    total: int
    page: int
    page_count: int
    page_size: Literal[25] = 25
    items: list[PaperCard]
    as_of_date: str | None = None
    window_days: int | None = None


class SourceSummary(_Model):
    provider: str
    last_attempt_at: str | None
    last_success_at: str | None
    completed_watermark: str | None
    last_error_code: str | None
    coverage_state: str
    streams: list[str]


class Manifest(_Model):
    schema_version: Literal[1] = 1
    release_id: str
    dataset_mode: Literal["live", "fixture"]
    generated_at: str
    catalog_snapshot_at: str
    code_revision: str
    rules_version: str
    source_config_hash: str
    included_work_count: int
    source_record_counts: dict[str, int]
    sources: list[SourceSummary]
    available_years: list[str]
    topic_counts: dict[str, int]
    coverage_status: Literal["complete", "partial", "not_started"]
    page_size: Literal[25] = 25
    public_base_path: str
    as_of_date: str
    site_title: str
    site_description: str
    public_site_url: str
