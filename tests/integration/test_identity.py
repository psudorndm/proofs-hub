"""Identity resolution and canonical projection (spec 11.1–11.2, 18.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select

from proof_index.classify.engine import Classifier
from proof_index.classify.rules import load_rules
from proof_index.classify.store import ClassificationHook
from proof_index.clock import FixedClock
from proof_index.db import tables as t
from proof_index.http.rate_limit import Budget
from proof_index.ingest.hooks import HookChain
from proof_index.models.classification import load_taxonomy
from proof_index.resolve.canonical import CanonicalBuilder
from proof_index.resolve.identity import IdentityResolver

from .test_runner import INCR, JsonAdapter, make_runner

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]
CASES = {
    c["case"]: c["record"]
    for c in json.loads((ROOT / "tests/fixtures/synthetic/cases.json").read_text())["cases"]
}


class Harness:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        self.engine = engine
        self.clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
        rules = load_rules(
            ROOT / "config/relevance-rules.yaml", load_taxonomy(ROOT / "config/taxonomy.yaml")
        )
        classify = ClassificationHook(Classifier(rules), self.clock, frozenset({"synthcrypto"}))
        self.builder = CanonicalBuilder(self.clock, rules.version)
        self.resolver = IdentityResolver(self.clock, rules.version, self.builder.rebuild)
        self.queue: list[list[dict[str, Any]]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(self.queue.pop(0)).encode())

        self.runner, _ = make_runner(
            engine,
            tmp_path,
            handle,
            hooks=HookChain(classify, self.resolver),  # type: ignore[arg-type]
            adapter_factory=lambda name, cfg: JsonAdapter(cfg),
            clock=self.clock,
        )

    def ingest(self, *records: dict[str, Any]) -> None:
        self.queue.append(list(records))
        summary = self.runner.run_stream(INCR, "sync", Budget(None))
        assert summary.state == "succeeded", summary
        self.clock.advance(timedelta(hours=1))

    def work_of(self, source: str, source_id: str) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(
                select(t.source_records.c.work_id)
                .where(t.source_records.c.source == source)
                .where(t.source_records.c.source_id == source_id)
            ).scalar_one()

    def canonical(self, work_id: Any) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(t.works).where(t.works.c.id == work_id)).one()
        return {
            "revision": row.canonical_revision,
            "visibility": row.visibility,
            "status": row.record_status,
            **row.canonical,
        }

    def count(self, table: Any) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def mutate(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    data = json.loads(json.dumps(base))
    data.update(changes)
    return data


@pytest.fixture()
def h(engine: Engine, tmp_path: Path) -> Harness:
    return Harness(engine, tmp_path)


def test_f01_f02_merge_via_explicit_identifier(h: Harness) -> None:
    h.ingest(CASES["F01"])
    h.ingest(CASES["F02"])
    w1, w2 = h.work_of("eprint", "2020/7"), h.work_of("arxiv", "2001.00007")
    assert w1 is not None and w1 == w2
    canon = h.canonical(w1)
    assert canon["title"] == "Zero-knowledge proofs for private arithmetic computations"
    assert canon["title_provenance"]["source_record_id"] is not None
    # ePrint is preferred for text; the earliest supported date wins.
    assert canon["published"] == {"year": 2020, "month": 1, "day": 3, "precision": "day"}
    assert sorted(s["provider"] for s in canon["sources"]) == ["arxiv", "eprint"]
    assert canon["identifiers"] == ["arxiv:2001.00007", "eprint:2020/7"]
    assert h.count(t.works) == 1


def test_f02_before_f01_still_merges_by_reverse_reference(h: Harness) -> None:
    h.ingest(CASES["F02"])
    h.ingest(CASES["F01"])
    assert h.work_of("eprint", "2020/7") == h.work_of("arxiv", "2001.00007")


def test_f10_similar_title_different_authors_stays_separate(h: Harness) -> None:
    h.ingest(CASES["F01"])
    h.ingest(CASES["F10"])
    assert h.work_of("eprint", "2020/7") != h.work_of("eprint", "2020/9")


def test_identical_rerun_preserves_ids_and_revision(h: Harness) -> None:
    h.ingest(CASES["F01"], CASES["F02"])
    work = h.work_of("eprint", "2020/7")
    revision = h.canonical(work)["revision"]
    h.ingest(CASES["F01"], CASES["F02"])
    assert h.work_of("eprint", "2020/7") == work
    assert h.canonical(work)["revision"] == revision
    assert h.count(t.works) == 1


def test_title_author_match_requires_unique_candidate_and_no_conflicts(h: Harness) -> None:
    base = CASES["F04"]  # "Security proofs for key encapsulation mechanisms": 5 meaningful tokens
    h.ingest(base)
    # Same title and authors from another provider: attaches.
    same = mutate(base, source="arxiv", source_id="2101.00042", identifiers={})
    h.ingest(same)
    assert h.work_of("arxiv", "2101.00042") == h.work_of("eprint", "2021/42")
    # Same title, different authors: a new work plus an identity review.
    other = mutate(
        base, source="arxiv", source_id="2101.00043", authors=[{"display_name": "Someone Else"}]
    )
    h.ingest(other)
    assert h.work_of("arxiv", "2101.00043") != h.work_of("eprint", "2021/42")
    # Two works now share the title: a third copy is not attached to either.
    third = mutate(base, source="arxiv", source_id="2101.00044")
    h.ingest(third)
    assert h.work_of("arxiv", "2101.00044") not in {
        h.work_of("eprint", "2021/42"),
        h.work_of("arxiv", "2101.00043"),
    }
    with h.engine.connect() as conn:
        reasons = sorted(conn.execute(select(t.identity_reviews.c.reason)).scalars())
    assert reasons == [
        "exact_title_ambiguous_or_short",
        "exact_title_ambiguous_or_short",
        "exact_title_insufficient_evidence",
    ]


def test_title_author_match_blocked_by_conflicting_doi(h: Harness) -> None:
    base = mutate(CASES["F04"], doi="10.9999/original")
    h.ingest(base)
    conflicting = mutate(base, source="arxiv", source_id="2101.00045", doi="10.9999/other")
    h.ingest(conflicting)
    assert h.work_of("arxiv", "2101.00045") != h.work_of("eprint", "2021/42")
    with h.engine.connect() as conn:
        review = conn.execute(select(t.identity_reviews)).one()
    assert review.reason == "exact_title_insufficient_evidence"
    assert review.evidence["doi_conflict"] is True


def test_short_titles_need_identifiers(h: Harness) -> None:
    short = mutate(CASES["F12"], title="Succinct arguments revisited")
    h.ingest(short)
    dup = mutate(short, source="arxiv", source_id="2301.00099")
    h.ingest(dup)
    assert h.work_of("arxiv", "2301.00099") != h.work_of("eprint", "2023/5")


def test_shared_proceedings_doi_does_not_merge_unrelated_papers(h: Harness) -> None:
    paper_a = mutate(
        CASES["F04"],
        source="dblp",
        source_id="conf/synthcrypto/A21",
        doi="10.9999/proceedings.2021",
        venue_key="synthcrypto",
    )
    paper_b = mutate(
        CASES["F05"],
        source="dblp",
        source_id="conf/synthcrypto/B21",
        doi="10.9999/proceedings.2021",
        venue_key="synthcrypto",
    )
    h.ingest(paper_a)
    h.ingest(paper_b)
    assert h.work_of("dblp", "conf/synthcrypto/A21") != h.work_of("dblp", "conf/synthcrypto/B21")
    with h.engine.connect() as conn:
        assert conn.execute(select(t.identity_reviews.c.reason)).scalars().all() == [
            "doi_match_incompatible_metadata"
        ]


def test_excluded_record_has_no_work(h: Harness) -> None:
    h.ingest(CASES["F06"])
    assert h.work_of("arxiv", "2105.00001") is None
    assert h.count(t.works) == 0


def test_review_record_gets_work_with_review_visibility(h: Harness) -> None:
    h.ingest(CASES["F08"])
    work = h.work_of("dblp", "conf/synthcrypto/Generic20")
    assert h.canonical(work)["visibility"] == "review"


def test_withdrawn_source_with_active_counterpart(h: Harness) -> None:
    h.ingest(CASES["F13"])
    h.ingest(CASES["F16"])
    work = h.work_of("eprint", "2023/6")
    assert work == h.work_of("arxiv", "2301.00010")
    canon = h.canonical(work)
    assert canon["status"] == "active"
    assert {s["provider"]: s["status"] for s in canon["sources"]} == {
        "arxiv": "withdrawn",
        "eprint": "active",
    }
    assert "some_sources_withdrawn_or_deleted" in canon["metadata_issues"]
    assert canon["areas"] == ["proof-systems", "security-proofs"]


def test_revision_increments_only_on_public_change(h: Harness) -> None:
    h.ingest(CASES["F01"])
    work = h.work_of("eprint", "2020/7")
    first = h.canonical(work)["revision"]
    h.ingest(mutate(CASES["F01"], abstract=CASES["F01"]["abstract"] + " Revised."))
    assert h.canonical(work)["revision"] == first + 1


def test_missing_abstract_retained_and_unknown_date(h: Harness) -> None:
    h.ingest(CASES["F12"])
    canon = h.canonical(h.work_of("eprint", "2023/5"))
    assert canon["published"]["precision"] == "unknown"
    assert canon["visibility"] == "included"
