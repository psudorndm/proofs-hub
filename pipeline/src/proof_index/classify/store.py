"""Persist per-source classifications and run batch (re)classification.

Rows are keyed by (source_record_id, content_sha256, rules_version) and never
updated: the same content under the same rules always yields the same row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from proof_index.classify.engine import ClassificationOverride, Classifier, input_for
from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.db.repository import new_id, transaction
from proof_index.ingest.hooks import CurrentChange
from proof_index.models.classification import ClassificationResult
from proof_index.models.source import NormalizedRecord, Tombstone

OverrideLookup = Callable[[Connection, uuid.UUID], ClassificationOverride | None]
AfterClassify = Callable[[Connection, uuid.UUID], None]


def _no_override(conn: Connection, source_record_id: uuid.UUID) -> ClassificationOverride | None:
    return None


def store_classification(
    conn: Connection,
    source_record_id: uuid.UUID,
    content_sha256: str,
    result: ClassificationResult,
    clock: Clock,
) -> bool:
    inserted = conn.execute(
        pg_insert(t.classifications)
        .values(
            id=new_id(),
            source_record_id=source_record_id,
            content_sha256=content_sha256,
            rules_version=result.rules_version,
            decision=result.decision,
            areas=list(result.areas),
            topics=list(result.topics),
            contributions=list(result.contributions),
            evidence={
                "matched_rules": [m.model_dump(mode="json") for m in result.matched_rules],
                "review_reasons": result.review_reasons,
                "offsets": "normalized field text",
            },
            review_provenance=result.review_provenance,
            created_at=clock.now(),
        )
        .on_conflict_do_nothing(
            index_elements=["source_record_id", "content_sha256", "rules_version"]
        )
    )
    return bool(inserted.rowcount)


def result_from_row(row: Any) -> ClassificationResult:
    from proof_index.models.classification import RuleMatch

    return ClassificationResult(
        decision=row.decision,
        areas=row.areas,
        topics=row.topics,
        contributions=row.contributions,
        matched_rules=[RuleMatch.model_validate(m) for m in row.evidence.get("matched_rules", [])],
        review_reasons=row.evidence.get("review_reasons", []),
        rules_version=row.rules_version,
        review_provenance=row.review_provenance,
    )


class ClassificationHook:
    """Promotion hook: classify a source record whenever its current state changes."""

    def __init__(
        self,
        classifier: Classifier,
        clock: Clock,
        crypto_venue_keys: frozenset[str] = frozenset(),
        override_lookup: OverrideLookup = _no_override,
    ) -> None:
        self.classifier = classifier
        self.clock = clock
        self.crypto_venue_keys = crypto_venue_keys
        self.override_lookup = override_lookup

    def classify_record(
        self, conn: Connection, source_record_id: uuid.UUID, record: NormalizedRecord
    ) -> ClassificationResult:
        override = self.override_lookup(conn, source_record_id)
        result = self.classifier.classify(input_for(record, self.crypto_venue_keys), override)
        store_classification(conn, source_record_id, record.content_sha256(), result, self.clock)
        return result

    def current_changed(self, conn: Connection, change: CurrentChange) -> None:
        if isinstance(change.item, Tombstone):
            return
        self.classify_record(conn, change.source_record_id, change.item)


@dataclass
class BatchReport:
    rules_version: str
    examined: int = 0
    classified: int = 0
    unchanged: int = 0
    mismatched: list[str] = field(default_factory=list)
    decisions: dict[str, int] = field(default_factory=dict)


def classify_current(
    engine: Engine,
    hook: ClassificationHook,
    *,
    only_missing: bool,
    after: AfterClassify | None = None,
    batch_size: int = 500,
) -> BatchReport:
    """Classify current observations. Caller holds the catalog-writer lock.

    ``only_missing`` (``--changed``) skips records already classified for this
    content under the active rules version. Otherwise (``--all``) stored rows are
    recomputed and any non-deterministic difference is reported.
    """
    version = hook.classifier.version
    report = BatchReport(rules_version=version)
    last_id: uuid.UUID | None = None
    while True:
        with transaction(engine) as conn:
            query = (
                select(
                    t.source_records.c.id,
                    t.source_observations.c.normalized,
                    t.source_observations.c.content_sha256,
                    t.classifications.c.id.label("classification_id"),
                    t.classifications.c.decision,
                    t.classifications.c.areas,
                    t.classifications.c.topics,
                )
                .select_from(
                    t.source_records.join(
                        t.source_observations,
                        t.source_observations.c.id == t.source_records.c.current_observation_id,
                    ).outerjoin(
                        t.classifications,
                        and_(
                            t.classifications.c.source_record_id == t.source_records.c.id,
                            t.classifications.c.content_sha256
                            == t.source_observations.c.content_sha256,
                            t.classifications.c.rules_version == version,
                        ),
                    )
                )
                .where(t.source_observations.c.item_kind == "record")
                .order_by(t.source_records.c.id)
                .limit(batch_size)
            )
            if last_id is not None:
                query = query.where(t.source_records.c.id > last_id)
            rows = conn.execute(query).all()
            if not rows:
                return report
            for row in rows:
                last_id = row.id
                report.examined += 1
                if only_missing and row.classification_id is not None:
                    report.unchanged += 1
                    continue
                record = NormalizedRecord.model_validate(row.normalized)
                result = hook.classify_record(conn, row.id, record)
                report.decisions[result.decision] = report.decisions.get(result.decision, 0) + 1
                if row.classification_id is None:
                    report.classified += 1
                    if after is not None:
                        after(conn, row.id)
                elif (row.decision, row.areas, row.topics) != (
                    result.decision,
                    list(result.areas),
                    list(result.topics),
                ):
                    report.mismatched.append(str(row.id))
                else:
                    report.unchanged += 1
