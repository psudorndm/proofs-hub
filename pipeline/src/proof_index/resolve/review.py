"""Export review queues for curators (JSONL; no reviewer contact details)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine, select

from proof_index.db import tables as t


def export_reviews(engine: Engine, out: Path) -> dict[str, int]:
    out.parent.mkdir(parents=True, exist_ok=True)
    relevance = identity = 0
    with engine.connect() as conn, out.open("w", encoding="utf-8") as handle:
        works = conn.execute(
            select(t.works.c.id, t.works.c.canonical_revision, t.works.c.canonical)
            .where(t.works.c.visibility == "review")
            .where(t.works.c.merged_into.is_(None))
            .order_by(t.works.c.id)
        ).all()
        for work in works:
            canon = work.canonical or {}
            handle.write(
                json.dumps(
                    {
                        "queue": "relevance",
                        "work_id": str(work.id),
                        "expected_canonical_revision": work.canonical_revision,
                        "title": canon.get("title"),
                        "identifiers": canon.get("identifiers", []),
                        "explanation": canon.get("inclusion_explanation"),
                        "metadata_issues": canon.get("metadata_issues", []),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            relevance += 1
        reviews = conn.execute(
            select(t.identity_reviews)
            .where(t.identity_reviews.c.state == "pending")
            .order_by(t.identity_reviews.c.created_at, t.identity_reviews.c.id)
        ).all()
        for review in reviews:
            handle.write(
                json.dumps(
                    {
                        "queue": "identity",
                        "review_id": str(review.id),
                        "source_record_id": str(review.source_record_id),
                        "candidate_work_id": str(review.candidate_work_id),
                        "reason": review.reason,
                        "evidence": review.evidence,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            identity += 1
    return {"relevance_reviews": relevance, "identity_reviews": identity}
