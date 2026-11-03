"""SQLAlchemy Core table definitions (spec section 6).

Columns beyond the spec's minimum are additive and documented in
``docs/decisions.md`` (D006). Bounded values are CHECK constraints mirrored by
Pydantic literals. There is no ON DELETE CASCADE anywhere.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)

TS = TIMESTAMP(timezone=True)
EMPTY_OBJECT = text("'{}'::jsonb")
EMPTY_ARRAY = text("'[]'::jsonb")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


SOURCES = ("eprint", "arxiv", "dblp", "crossref", "curated")
RUN_MODES = ("sync", "backfill", "verify", "reconcile")
RUN_STATES = ("planned", "fetching", "staged", "promoting", "paused", "succeeded", "failed")
PAGE_STATES = ("durable", "failed")
STAGE_STATES = ("pending", "promoted", "quarantined")
ITEM_KINDS = ("record", "tombstone")
CHANGE_KINDS = ("initial", "changed", "reverted", "older", "conflict", "tombstone")
VISIBILITY = ("included", "review", "excluded")
RECORD_STATUS = ("active", "withdrawn", "unavailable")
EVENT_KINDS = (
    "publication",
    "source_added",
    "source_revision",
    "metadata_updated",
    "withdrawn",
    "restored",
    "curation",
)
DATE_BASIS = ("source", "observed", "none")
DECISIONS = ("include", "review", "exclude")
REVIEW_PROVENANCE = ("automatic", "agent-provisional", "human-reviewed")
RELATIONS = ("extended_by", "related_to")
REVIEW_STATES = ("pending", "accepted", "rejected")
COVERAGE_STATES = ("not_started", "running", "complete", "partial", "blocked")
RELEASE_STATES = ("exported", "validated", "built", "deployed", "failed")

provider_streams = Table(
    "provider_streams",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("stream_key", Text, nullable=False, unique=True),
    Column("config_hash", Text, nullable=False),
    Column("completed_watermark", TS, nullable=True),
    Column("last_attempt_at", TS, nullable=True),
    Column("last_success_at", TS, nullable=True),
    Column("last_error_code", Text, nullable=True),
    # Additive: stream-level checkpoint data (e.g. captured incremental boundary).
    Column("checkpoint", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("created_at", TS, nullable=False),
    CheckConstraint(_in("provider", SOURCES), name="provider"),
)

harvest_runs = Table(
    "harvest_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("stream_id", UUID(as_uuid=True), ForeignKey("provider_streams.id"), nullable=False),
    Column("mode", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("window_from", TS, nullable=True),
    Column("window_until", TS, nullable=True),
    Column("started_at", TS, nullable=False),
    Column("finished_at", TS, nullable=True),
    Column("response_time", TS, nullable=True),
    Column("pages_fetched", Integer, nullable=False, server_default=text("0")),
    Column("records_staged", Integer, nullable=False, server_default=text("0")),
    Column("records_promoted", Integer, nullable=False, server_default=text("0")),
    Column("error_code", Text, nullable=True),
    Column("error_summary", Text, nullable=True),
    # Additive columns.
    Column("config_hash", Text, nullable=False),
    Column("paused_phase", Text, nullable=True),
    Column("snapshot_key", Text, nullable=True),
    Column("promotion_started_at", TS, nullable=True),
    Column("reconciled_at", TS, nullable=True),
    Column("records_quarantined", Integer, nullable=False, server_default=text("0")),
    Column("reported_total", BigInteger, nullable=True),
    CheckConstraint(_in("mode", RUN_MODES), name="mode"),
    CheckConstraint(_in("state", RUN_STATES), name="state"),
    CheckConstraint(
        "paused_phase IS NULL OR paused_phase IN ('fetching', 'promoting')", name="paused_phase"
    ),
    CheckConstraint("(state = 'paused') = (paused_phase IS NOT NULL)", name="paused_has_phase"),
    Index("ix_harvest_runs_stream_started", "stream_id", text("started_at DESC")),
)

harvest_pages = Table(
    "harvest_pages",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("harvest_runs.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("request_fingerprint", Text, nullable=False),
    Column("blob_key", Text, nullable=False),
    Column("raw_sha256", Text, nullable=False),
    Column("next_cursor", JSONB(none_as_null=True), nullable=True),
    Column("is_terminal", Boolean, nullable=False),
    Column("parsed_count", Integer, nullable=False),
    Column("state", Text, nullable=False),
    # Additive columns.
    Column("quarantined_count", Integer, nullable=False, server_default=text("0")),
    Column("reported_total", BigInteger, nullable=True),
    Column("fetched_at", TS, nullable=False),
    UniqueConstraint("run_id", "ordinal"),
    CheckConstraint(_in("state", PAGE_STATES), name="state"),
    CheckConstraint("ordinal >= 0", name="ordinal"),
    CheckConstraint("is_terminal = (next_cursor IS NULL)", name="terminal_cursor"),
)

works = Table(
    "works",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("visibility", Text, nullable=False),
    Column("record_status", Text, nullable=False),
    Column("canonical", JSONB, nullable=False),
    Column("canonical_revision", BigInteger, nullable=False, server_default=text("0")),
    Column("is_foundational", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
    Column("merged_into", UUID(as_uuid=True), ForeignKey("works.id"), nullable=True),
    CheckConstraint(_in("visibility", VISIBILITY), name="visibility"),
    CheckConstraint(_in("record_status", RECORD_STATUS), name="record_status"),
    CheckConstraint("merged_into IS NULL OR merged_into <> id", name="not_self_merged"),
    CheckConstraint("canonical_revision >= 0", name="revision"),
    Index("ix_works_visibility_merged_into", "visibility", "merged_into"),
    Index("ix_works_merged_into", "merged_into"),
)

source_records = Table(
    "source_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("current_observation_id", UUID(as_uuid=True), nullable=True),
    Column("work_id", UUID(as_uuid=True), ForeignKey("works.id"), nullable=True),
    Column("first_seen_at", TS, nullable=False),
    Column("last_seen_at", TS, nullable=False),
    UniqueConstraint("source", "source_id"),
    CheckConstraint(_in("source", SOURCES), name="source"),
    # The current observation must belong to this record (composite FK, added after
    # source_observations exists).
    ForeignKeyConstraint(
        ["id", "current_observation_id"],
        ["source_observations.source_record_id", "source_observations.id"],
        name="fk_source_records_current_observation",
        use_alter=True,
    ),
    Index("ix_source_records_work_id", "work_id"),
    Index("ix_source_records_current_observation_id", "current_observation_id"),
)

source_observations = Table(
    "source_observations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=False),
    Column("observation_key", Text, nullable=False, unique=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("harvest_runs.id"), nullable=False),
    Column("page_id", UUID(as_uuid=True), ForeignKey("harvest_pages.id"), nullable=False),
    Column("normalized", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("source_modified_at", TS, nullable=True),
    Column("harvest_datestamp", TS, nullable=True),
    Column("source_version", Text, nullable=True),
    Column("observed_at", TS, nullable=False),
    Column("change_kind", Text, nullable=False),
    Column("is_current_eligible", Boolean, nullable=False),
    # Additive columns.
    Column("item_kind", Text, nullable=False, server_default=text("'record'")),
    Column("parser_version", Text, nullable=False),
    Column("previous_observation_id", UUID(as_uuid=True), nullable=True),
    UniqueConstraint("source_record_id", "id"),
    CheckConstraint(_in("change_kind", CHANGE_KINDS), name="change_kind"),
    CheckConstraint(_in("item_kind", ITEM_KINDS), name="item_kind"),
    Index("ix_source_observations_record_observed", "source_record_id", text("observed_at DESC")),
    Index("ix_source_observations_run_id", "run_id"),
    Index("ix_source_observations_page_id", "page_id"),
)

staged_records = Table(
    "staged_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("harvest_runs.id"), nullable=False),
    Column("page_id", UUID(as_uuid=True), ForeignKey("harvest_pages.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("item_kind", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column(
        "promoted_observation_id",
        UUID(as_uuid=True),
        ForeignKey("source_observations.id"),
        nullable=True,
    ),
    Column("error_code", Text, nullable=True),
    # Additive: position inside the page, for stable promotion order.
    Column("item_ordinal", Integer, nullable=False),
    UniqueConstraint("run_id", "source", "source_id", "content_sha256"),
    CheckConstraint(_in("item_kind", ITEM_KINDS), name="item_kind"),
    CheckConstraint(_in("state", STAGE_STATES), name="state"),
    CheckConstraint(
        "(state = 'promoted') = (promoted_observation_id IS NOT NULL)", name="promoted_has_obs"
    ),
    Index("ix_staged_records_run_state_id", "run_id", "state", "id"),
    Index("ix_staged_records_page_id", "page_id"),
    Index("ix_staged_records_promoted_observation_id", "promoted_observation_id"),
)

# Additive table: item-level parse failures that have no valid source identity.
quarantine_items = Table(
    "quarantine_items",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("harvest_runs.id"), nullable=False),
    Column("page_id", UUID(as_uuid=True), ForeignKey("harvest_pages.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("source_id", Text, nullable=True),
    Column("item_ordinal", Integer, nullable=False),
    Column("error_code", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("raw_excerpt", Text, nullable=True),
    Column("created_at", TS, nullable=False),
    UniqueConstraint("page_id", "item_ordinal"),
    Index("ix_quarantine_items_run_id", "run_id"),
)

curation_commands = Table(
    "curation_commands",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("command_hash", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("reviewer_label", Text, nullable=False),
    Column("review_kind", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("applied_at", TS, nullable=False),
    Column("before_state", JSONB, nullable=False),
    Column("after_state", JSONB, nullable=False),
    Column(
        "reverts_command_id",
        UUID(as_uuid=True),
        ForeignKey("curation_commands.id"),
        nullable=True,
    ),
    # Additive: the YAML command_id; reuse with different content is an error.
    Column("command_id", Text, nullable=False, unique=True),
    CheckConstraint(
        _in(
            "kind",
            (
                "include",
                "exclude",
                "set_labels",
                "set_fields",
                "mark_foundational",
                "merge",
                "split",
                "relate",
                "revert",
                "import_curated",
            ),
        ),
        name="kind",
    ),
    CheckConstraint(_in("review_kind", REVIEW_PROVENANCE[1:]), name="review_kind"),
    Index("ix_curation_commands_reverts_command_id", "reverts_command_id"),
)

work_events = Table(
    "work_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("work_id", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column(
        "observation_id", UUID(as_uuid=True), ForeignKey("source_observations.id"), nullable=True
    ),
    Column("kind", Text, nullable=False),
    Column("event_date", Date, nullable=True),
    Column("date_basis", Text, nullable=False),
    Column("feed_eligible", Boolean, nullable=False),
    Column("event_key", Text, nullable=False, unique=True),
    Column("created_at", TS, nullable=False),
    # Additive columns.
    Column(
        "curation_command_id",
        UUID(as_uuid=True),
        ForeignKey("curation_commands.id"),
        nullable=True,
    ),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=True),
    Column("details", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    CheckConstraint(_in("kind", EVENT_KINDS), name="kind"),
    CheckConstraint(_in("date_basis", DATE_BASIS), name="date_basis"),
    CheckConstraint("(date_basis = 'none') = (event_date IS NULL)", name="date_basis_has_date"),
    Index("ix_work_events_work_date", "work_id", text("event_date DESC")),
    Index("ix_work_events_observation_id", "observation_id"),
    Index("ix_work_events_curation_command_id", "curation_command_id"),
    Index("ix_work_events_source_record_id", "source_record_id"),
)

classifications = Table(
    "classifications",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("rules_version", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("areas", JSONB, nullable=False, server_default=EMPTY_ARRAY),
    Column("topics", JSONB, nullable=False, server_default=EMPTY_ARRAY),
    Column("contributions", JSONB, nullable=False, server_default=EMPTY_ARRAY),
    Column("evidence", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("review_provenance", Text, nullable=False),
    Column("created_at", TS, nullable=False),
    UniqueConstraint("source_record_id", "content_sha256", "rules_version"),
    CheckConstraint(_in("decision", DECISIONS), name="decision"),
    CheckConstraint(_in("review_provenance", REVIEW_PROVENANCE), name="review_provenance"),
)

publications = Table(
    "publications",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("work_id", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("provider", Text, nullable=False),
    Column("provider_key", Text, nullable=False),
    Column("doi", Text, nullable=True),
    Column("metadata", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=True),
    UniqueConstraint("provider", "provider_key"),
    Index("ix_publications_doi", "doi"),
    Index("ix_publications_work_id", "work_id"),
    Index("ix_publications_source_record_id", "source_record_id"),
)

work_relations = Table(
    "work_relations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("from_work", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("to_work", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("relation", Text, nullable=False),
    Column("evidence", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("created_at", TS, nullable=False),
    UniqueConstraint("from_work", "to_work", "relation"),
    CheckConstraint("from_work <> to_work", name="not_self"),
    CheckConstraint(_in("relation", RELATIONS), name="relation"),
    Index("ix_work_relations_to_work", "to_work"),
)

identity_reviews = Table(
    "identity_reviews",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("candidate_key", Text, nullable=False, unique=True),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=False),
    Column("candidate_work_id", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("reason", Text, nullable=False),
    Column("evidence", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("state", Text, nullable=False),
    Column("created_at", TS, nullable=False),
    CheckConstraint(_in("state", REVIEW_STATES), name="state"),
    Index("ix_identity_reviews_state", "state"),
    Index("ix_identity_reviews_source_record_id", "source_record_id"),
    Index("ix_identity_reviews_candidate_work_id", "candidate_work_id"),
)

source_assignment_overrides = Table(
    "source_assignment_overrides",
    metadata,
    Column(
        "source_record_id",
        UUID(as_uuid=True),
        ForeignKey("source_records.id"),
        primary_key=True,
    ),
    Column("target_work_id", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("command_id", UUID(as_uuid=True), ForeignKey("curation_commands.id"), nullable=False),
    Index("ix_source_assignment_overrides_target_work_id", "target_work_id"),
    Index("ix_source_assignment_overrides_command_id", "command_id"),
)

separation_constraints = Table(
    "separation_constraints",
    metadata,
    Column("work_a", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("work_b", UUID(as_uuid=True), ForeignKey("works.id"), nullable=False),
    Column("command_id", UUID(as_uuid=True), ForeignKey("curation_commands.id"), nullable=False),
    PrimaryKeyConstraint("work_a", "work_b"),
    CheckConstraint("work_a < work_b", name="sorted_distinct_pair"),
    Index("ix_separation_constraints_work_b", "work_b"),
    Index("ix_separation_constraints_command_id", "command_id"),
)

coverage_slices = Table(
    "coverage_slices",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("stream_id", UUID(as_uuid=True), ForeignKey("provider_streams.id"), nullable=False),
    Column("slice_key", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("expected_count", BigInteger, nullable=True),
    Column("seen_count", BigInteger, nullable=False, server_default=text("0")),
    Column("valid_count", BigInteger, nullable=False, server_default=text("0")),
    Column("quarantined_count", BigInteger, nullable=False, server_default=text("0")),
    Column("notes", JSONB, nullable=False, server_default=EMPTY_OBJECT),
    Column("run_id", UUID(as_uuid=True), ForeignKey("harvest_runs.id"), nullable=True),
    Column("updated_at", TS, nullable=False),
    UniqueConstraint("stream_id", "slice_key"),
    CheckConstraint(_in("state", COVERAGE_STATES), name="state"),
    Index("ix_coverage_slices_run_id", "run_id"),
)

releases = Table(
    "releases",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("state", Text, nullable=False),
    Column("catalog_snapshot_at", TS, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("blob_prefix", Text, nullable=False),
    Column("created_at", TS, nullable=False),
    Column("deployed_at", TS, nullable=True),
    # Additive: the public release-id string.
    Column("release_key", Text, nullable=False, unique=True),
    CheckConstraint(_in("state", RELEASE_STATES), name="state"),
)
