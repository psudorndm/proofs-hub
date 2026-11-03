from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select

from proof_index.classify.engine import Classifier
from proof_index.classify.rules import load_rules
from proof_index.classify.store import ClassificationHook, classify_current
from proof_index.clock import FixedClock
from proof_index.db import tables as t
from proof_index.db.repository import transaction
from proof_index.http.rate_limit import Budget
from proof_index.models.classification import load_taxonomy

from .test_runner import BOOT, NORMAL, make_runner, oai_handler

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


def hook() -> ClassificationHook:
    rules = load_rules(
        ROOT / "config/relevance-rules.yaml", load_taxonomy(ROOT / "config/taxonomy.yaml")
    )
    return ClassificationHook(Classifier(rules), FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC)))


def test_promotion_classifies_changed_records(engine: Engine, tmp_path: Path) -> None:
    clf_hook = hook()
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL), hooks=clf_hook)  # type: ignore[arg-type]
    assert runner.run_stream(BOOT, "backfill", Budget(None)).state == "succeeded"
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                t.source_records.c.source_id,
                t.classifications.c.decision,
                t.classifications.c.areas,
            )
            .join(t.classifications, t.classifications.c.source_record_id == t.source_records.c.id)
            .order_by(t.source_records.c.source_id)
        ).all()
    # Two normal records are classified; the tombstone is not.
    assert [(r.source_id, r.decision, r.areas) for r in rows] == [
        ("2020/7", "include", ["proof-systems"]),
        ("2021/42", "include", ["security-proofs"]),
    ]


def test_classify_changed_fills_missing_and_all_is_deterministic(
    engine: Engine, tmp_path: Path
) -> None:
    clf_hook = hook()
    runner, _ = make_runner(engine, tmp_path, oai_handler([], NORMAL), hooks=clf_hook)  # type: ignore[arg-type]
    runner.run_stream(BOOT, "backfill", Budget(None))
    with transaction(engine) as conn:
        conn.execute(delete(t.classifications))
    report = classify_current(engine, clf_hook, only_missing=True)
    assert (report.examined, report.classified) == (2, 2)
    again = classify_current(engine, clf_hook, only_missing=True)
    assert (again.classified, again.unchanged) == (0, 2)
    full = classify_current(engine, clf_hook, only_missing=False)
    assert full.mismatched == [] and full.unchanged == 2
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.classifications)).scalar_one() == 2
