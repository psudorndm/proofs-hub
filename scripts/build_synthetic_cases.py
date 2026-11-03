"""Write tests/fixtures/synthetic/cases.json (spec section 18.1).

SYNTHETIC test cases only: invented titles, authors, IDs, and DOIs under the
reserved-looking prefix 10.9999. They are not bibliographic claims.
"""

from __future__ import annotations

import json
from pathlib import Path

from proof_index.models.source import NormalizedRecord

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic" / "cases.json"


def day(y: int, m: int, d: int) -> dict:
    return {"year": y, "month": m, "day": d, "precision": "day"}


def year(y: int) -> dict:
    return {"year": y, "month": None, "day": None, "precision": "year"}


UNKNOWN = {"year": None, "month": None, "day": None, "precision": "unknown"}
ZK_ABSTRACT = (
    "We construct a zero-knowledge proof system for arithmetic circuits and prove its "
    "soundness under a standard cryptographic assumption."
)


def rec(
    source: str,
    source_id: str,
    title: str,
    authors: list[str],
    abstract: str | None,
    published: dict,
    **extra: object,
) -> dict:
    data = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "authors": [{"display_name": a} for a in authors],
        "abstract": abstract,
        "published": published,
        "source_modified_at": None,
        "harvest_datestamp": None,
        "source_version": None,
        "doi": None,
        "identifiers": {},
        "source_categories": [],
        "venue_key": None,
        "venue_name": None,
        "links": [],
        "status": "active",
        "evidence": {"synthetic": "tests/fixtures/synthetic/cases.json"},
    }
    data.update(extra)
    NormalizedRecord.model_validate(data)
    return data


CASES = [
    {
        "case": "F01",
        "expect": {
            "decision": "include",
            "areas": ["proof-systems"],
            "topics_include": ["zero-knowledge"],
        },
        "record": rec(
            "eprint",
            "2020/7",
            "Zero-knowledge proofs for private arithmetic computations",
            ["Alice Example", "Bob Example"],
            ZK_ABSTRACT,
            day(2020, 1, 3),
            links=[
                {
                    "kind": "landing",
                    "url": "https://eprint.iacr.org/2020/007",
                    "origin": "source_metadata",
                }
            ],
        ),
    },
    {
        "case": "F02",
        "expect": {"decision": "include", "areas": ["proof-systems"], "same_work_as": "F01"},
        "record": rec(
            "arxiv",
            "2001.00007",
            "Zero-knowledge proofs for private arithmetic computations",
            ["Alice Example", "Bob Example"],
            ZK_ABSTRACT,
            day(2020, 1, 5),
            source_version="v1",
            source_modified_at="2020-01-05T18:00:00Z",
            identifiers={"eprint": ["2020/7"]},
            source_categories=["cs.CR"],
        ),
    },
    {
        "case": "F03",
        "expect": {
            "decision": "include",
            "areas": ["proof-systems"],
            "same_work_as": "F01",
            "revision_of": "F02",
        },
        "record": rec(
            "arxiv",
            "2001.00007",
            "Zero-knowledge proofs for private arithmetic computations",
            ["Alice Example", "Bob Example"],
            ZK_ABSTRACT + " This version adds an implementation.",
            day(2020, 1, 5),
            source_version="v2",
            source_modified_at="2020-03-01T10:00:00Z",
            identifiers={"eprint": ["2020/7"]},
            source_categories=["cs.CR"],
        ),
    },
    {
        "case": "F04",
        "expect": {"decision": "include", "areas": ["security-proofs"]},
        "record": rec(
            "eprint",
            "2021/42",
            "Security proofs for key encapsulation mechanisms",
            ["Dana Example"],
            "We give tight security reductions for KEMs built from public-key encryption in the random oracle model.",
            day(2021, 1, 20),
        ),
    },
    {
        "case": "F05",
        "expect": {"decision": "include", "areas_include": ["formal-verification"]},
        "record": rec(
            "eprint",
            "2021/100",
            "Formal verification of authentication protocols with Tamarin",
            ["Erin Example"],
            "We use the Tamarin prover to analyse cryptographic authentication protocols in the symbolic model.",
            day(2021, 2, 1),
        ),
    },
    {
        "case": "F06",
        "expect": {"decision": "exclude"},
        "record": rec(
            "arxiv",
            "2105.00001",
            "Nova observations in nearby galaxies",
            ["Fay Astro"],
            "We report photometric light curves of a classical nova in two nearby galaxies.",
            day(2021, 5, 1),
            source_categories=["astro-ph.SR"],
        ),
    },
    {
        "case": "F07",
        "expect": {"decision": "exclude"},
        "record": rec(
            "arxiv",
            "2106.00002",
            "Lean proofs of elementary number theoretic identities",
            ["Gus Math"],
            "We formalize classical identities about divisors in Lean 4 using Mathlib.",
            day(2021, 6, 2),
            source_categories=["math.NT"],
        ),
    },
    {
        "case": "F08",
        "expect": {"decision": "review"},
        "record": rec(
            "dblp",
            "conf/synthcrypto/Generic20",
            "A generic verification approach",
            ["Hal Example"],
            None,
            year(2020),
            venue_key="synthcrypto",
            venue_name="Synthetic Cryptology Conference",
        ),
    },
    {
        "case": "F09",
        "expect": {"curated": True, "foundational": True},
        "record": rec(
            "curated",
            "curated:synthetic-zk-1985",
            "A synthetic foundational interactive proof record",
            ["Ida Historic", "Jon Historic"],
            None,
            year(1985),
            evidence={"title": "SYNTHETIC verified evidence field (fixture)"},
        ),
    },
    {
        "case": "F10",
        "expect": {"decision": "include", "distinct_work_from": "F01"},
        "record": rec(
            "eprint",
            "2020/9",
            "Zero-knowledge proofs for private arithmetic computation",
            ["Kim Other", "Lou Other"],
            ZK_ABSTRACT,
            day(2020, 1, 9),
            doi="10.9999/synthetic.f10",
        ),
    },
    {
        "case": "F11",
        "expect": {"decision": "include", "unsafe_text": True},
        "record": rec(
            "eprint",
            "2022/1",
            "<script>alert('x')</script> Zero-knowledge proofs & <b>tags</b>",
            ['Mia "Quote" Example'],
            "Abstract with <img src=x onerror=alert(1)> markup and a zero-knowledge proof.",
            day(2022, 1, 4),
        ),
    },
    {
        "case": "F12",
        "expect": {"decision": "include", "unknown_date": True},
        "record": rec(
            "eprint",
            "2023/5",
            "Succinct arguments for batch statements",
            ["Ned Example"],
            "A succinct argument for batches of NP statements from collision-resistant hash functions.",
            UNKNOWN,
        ),
    },
    {
        "case": "F13",
        "expect": {"decision": "include", "areas": ["proof-systems", "security-proofs"]},
        "record": rec(
            "eprint",
            "2023/6",
            "Zero-knowledge proofs with tight security reductions for signature schemes",
            ["Oli Example"],
            "We prove tight security for signature schemes built from Sigma protocols.",
            day(2026, 10, 30),
        ),
    },
    {
        "case": "F14",
        "expect": {"decision": "include", "old_first_seen_today": True},
        "record": rec(
            "eprint",
            "1999/1",
            "Zero-knowledge arguments from one-way functions",
            ["Pat Early"],
            "A zero-knowledge argument construction from any one-way function.",
            day(1999, 1, 5),
        ),
    },
    {
        "case": "F15",
        "expect": {"decision": "exclude"},
        "record": rec(
            "arxiv",
            "2107.00003",
            "Verifying compiler optimizations in Coq",
            ["Quinn PL"],
            "We verify loop optimizations of a functional language compiler using the Coq proof assistant.",
            day(2021, 7, 3),
            source_categories=["cs.PL"],
        ),
    },
    {
        "case": "F16",
        "expect": {"decision": "include", "same_work_as": "F13", "withdrawn_source": True},
        "record": rec(
            "arxiv",
            "2301.00010",
            "Zero-knowledge proofs with tight security reductions for signature schemes",
            ["Oli Example"],
            "We prove tight security for signature schemes built from Sigma protocols.",
            day(2026, 10, 30),
            status="withdrawn",
            identifiers={"eprint": ["2023/6"]},
            source_modified_at="2026-11-01T00:00:00Z",
            source_version="v2",
        ),
    },
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "notice": "SYNTHETIC test cases (spec section 18.1); not bibliographic claims.",
        "frozen_now": "2026-11-03T12:00:00Z",
        "cases": CASES,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(CASES)} cases)")


if __name__ == "__main__":
    main()
