from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from proof_index.classify.engine import (
    ClassificationOverride,
    Classifier,
    ClassifierInput,
    input_for,
)
from proof_index.classify.rules import RulesFile, compile_rules, load_rules
from proof_index.errors import ConfigError
from proof_index.models.classification import load_taxonomy
from proof_index.models.source import NormalizedRecord

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = load_taxonomy(ROOT / "config" / "taxonomy.yaml")
RULES = load_rules(ROOT / "config" / "relevance-rules.yaml", TAXONOMY)
CLF = Classifier(RULES)
CASES = {
    c["case"]: c
    for c in json.loads((ROOT / "tests/fixtures/synthetic/cases.json").read_text())["cases"]
}
CRYPTO_VENUES = frozenset({"synthcrypto"})


def classify_case(name: str) -> Any:
    record = NormalizedRecord.model_validate(CASES[name]["record"])
    return CLF.classify(input_for(record, CRYPTO_VENUES))


@pytest.mark.parametrize("name", [c for c, v in CASES.items() if "decision" in v["expect"]])
def test_synthetic_case_decisions(name: str) -> None:
    expect = CASES[name]["expect"]
    result = classify_case(name)
    assert result.decision == expect["decision"], (
        name,
        result.review_reasons,
        result.matched_rules,
    )
    if "areas" in expect:
        assert result.areas == expect["areas"]
    for area in expect.get("areas_include", []):
        assert area in result.areas
    for topic in expect.get("topics_include", []):
        assert topic in result.topics
    assert result.rules_version == "rules-v1"
    assert result.review_provenance == "automatic"


def test_security_proof_and_formal_crypto_examples_are_in_scope() -> None:
    f04 = classify_case("F04")
    assert f04.decision == "include" and f04.areas == ["security-proofs"]
    assert "security-reductions" in f04.topics
    f05 = classify_case("F05")
    assert f05.decision == "include" and "formal-verification" in f05.areas
    assert "formal-protocol-verification" in f05.topics


def test_area_match_does_not_imply_every_topic() -> None:
    f01 = classify_case("F01")
    assert f01.topics == ["zero-knowledge"]


def test_venue_alone_is_review_not_include() -> None:
    f08 = classify_case("F08")
    assert f08.decision == "review"
    assert any("abstract unavailable" in r for r in f08.review_reasons)


def test_missing_abstract_is_not_automatic_exclusion() -> None:
    title_only = CLF.classify(ClassifierInput("Succinct arguments from lattices", None))
    assert title_only.decision == "include"
    weak = CLF.classify(ClassifierInput("A new folding technique", None, venue_evidence=True))
    assert weak.decision == "review"


def test_ambiguous_tokens_alone_do_not_force_review() -> None:
    result = CLF.classify(
        ClassifierInput(
            "A proof of the security of bridges",
            "We study verification of the theorem about bridge structures.",
        )
    )
    assert result.decision == "exclude"


def test_bare_acronyms_need_context() -> None:
    assert (
        CLF.classify(ClassifierInput("IP over PCP links", "Network routing.")).decision == "exclude"
    )
    ctx = CLF.classify(ClassifierInput("Short PCPs for encryption", "A cryptographic result."))
    assert ctx.decision == "review"


def test_independent_cue_requires_distinct_span() -> None:
    # The only cue is "zero-knowledge proof" in the abstract: its own "zero-knowledge"
    # substring cannot corroborate it.
    alone = CLF.classify(ClassifierInput("On a new system", "A zero-knowledge proof."))
    assert alone.decision == "review"
    corroborated = CLF.classify(
        ClassifierInput("On a new system", "A zero-knowledge proof for encryption.")
    )
    assert corroborated.decision == "include"


def test_contradictory_context_suppresses_ambiguous_only() -> None:
    protein = CLF.classify(
        ClassifierInput("Protein folding dynamics", "Folding of amino acid chains.")
    )
    assert protein.decision == "exclude"
    # A real cryptographic paper that mentions another discipline is not excluded.
    crypto = CLF.classify(
        ClassifierInput(
            "Zero-knowledge proofs of protein folding results",
            "We prove correctness of folding computations.",
        )
    )
    assert crypto.decision == "review"
    assert crypto.areas == ["proof-systems"]
    assert crypto.review_reasons[0].startswith("conflicting context")


def test_formal_tool_needs_crypto_context() -> None:
    f15 = classify_case("F15")
    assert f15.decision == "exclude"
    single = CLF.classify(
        ClassifierInput("A study of lemmas", "We use EasyCrypt for an encryption scheme.")
    )
    assert single.decision == "review"  # one formal cue in the abstract is not enough
    double = CLF.classify(
        ClassifierInput(
            "A study of lemmas",
            "We give a mechanized proof in EasyCrypt of an encryption scheme.",
        )
    )
    assert double.decision == "include" and "formal-verification" in double.areas


def test_named_cryptanalysis_allowlist() -> None:
    named = CLF.classify(ClassifierInput("Cryptanalysis of reduced-round AES", None))
    assert named.decision == "include" and named.areas == ["security-proofs"]
    unknown = CLF.classify(ClassifierInput("Cryptanalysis of the Foo machine", None))
    assert unknown.decision == "review"


def test_uppercase_stark_only() -> None:
    assert (
        CLF.classify(ClassifierInput("A stark contrast in markets", "Economics.")).decision
        == "exclude"
    )
    stark = CLF.classify(ClassifierInput("Faster STARKs", "Hash-based arguments."))
    assert stark.decision == "include"


def test_contributions() -> None:
    survey = CLF.classify(
        ClassifierInput("SoK: Zero-knowledge proof systems", "We survey constructions.")
    )
    assert "survey-tutorial" in survey.contributions
    attack = CLF.classify(
        ClassifierInput("Forgery attacks on signature schemes", "We give a key-recovery attack.")
    )
    assert attack.contributions[0] == "analysis-attack"
    impl = CLF.classify(
        ClassifierInput("GPU acceleration of SNARK provers", "An implementation with benchmarks.")
    )
    assert "implementation-optimization" in impl.contributions
    plain = classify_case("F12")
    assert plain.contributions == []  # insufficient evidence: no fabricated type


def test_offsets_are_normalized_and_labeled() -> None:
    result = CLF.classify(ClassifierInput("Zero–knowledge   proofs", None))
    hit = next(m for m in result.matched_rules if m.rule_id == "PS_DIRECT_ZK_PROOF")
    assert (hit.normalized_start, hit.normalized_end) == (0, 21)
    assert hit.field == "title"


def test_override_wins() -> None:
    override = ClassificationOverride(
        decision="include",
        areas=["proof-systems"],
        topics=["interactive-foundations"],
        contributions=["construction-theory"],
        review_provenance="agent-provisional",
        reason="foundational curated record",
    )
    result = CLF.classify(ClassifierInput("A historic title", None), override)
    assert result.decision == "include" and result.review_provenance == "agent-provisional"


def test_rules_reject_unknown_topic_and_bad_group_area() -> None:
    import yaml

    data = yaml.safe_load((ROOT / "config/relevance-rules.yaml").read_text())
    data["rules"][0]["topic"] = "not-a-topic"
    with pytest.raises(ConfigError):
        compile_rules(RulesFile.model_validate(data), TAXONOMY)
    data = yaml.safe_load((ROOT / "config/relevance-rules.yaml").read_text())
    data["rules"][0]["area"] = "security-proofs"
    with pytest.raises(ValueError):
        RulesFile.model_validate(data)
