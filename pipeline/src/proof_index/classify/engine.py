"""Evidence-group decision engine (spec sections 10.1–10.3).

The engine classifies one source record at a time. Matching an area never
implies every topic under it: topics come only from matched rules whose area is
supported. Offsets are positions in the *normalized* field text.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from proof_index.classify.rules import CompiledRules
from proof_index.models.classification import (
    AREAS,
    CONTRIBUTIONS,
    Area,
    ClassificationResult,
    Contribution,
    Decision,
    ReviewProvenance,
    RuleMatch,
)
from proof_index.models.source import NormalizedRecord
from proof_index.normalize.text import match_text

SNIPPET_RADIUS = 40


@dataclass(frozen=True)
class ClassifierInput:
    title: str
    abstract: str | None
    venue_evidence: bool = False


@dataclass(frozen=True)
class Hit:
    rule_id: str
    group: str
    field: str
    start: int
    end: int
    text: str
    snippet: str
    area: str | None
    topic: str | None

    def overlaps(self, other: Hit) -> bool:
        return self.field == other.field and self.start < other.end and other.start < self.end


class ClassificationOverride(BaseModel):
    """An explicit curated decision for a record or work (applied first)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    areas: list[Area]
    topics: list[str]
    contributions: list[Contribution]
    review_provenance: ReviewProvenance
    reason: str


def independent(a: Hit, b: Hit) -> bool:
    """Different conceptual group and a different phrase span."""
    return a.group != b.group and not a.overlaps(b)


def _snippet(text: str, start: int, end: int) -> str:
    return text[max(0, start - SNIPPET_RADIUS) : min(len(text), end + SNIPPET_RADIUS)]


def input_for(
    record: NormalizedRecord, crypto_venue_keys: frozenset[str] = frozenset()
) -> ClassifierInput:
    venue = record.source == "eprint" or (
        record.venue_key is not None and record.venue_key in crypto_venue_keys
    )
    return ClassifierInput(title=record.title, abstract=record.abstract, venue_evidence=venue)


class Classifier:
    def __init__(self, rules: CompiledRules) -> None:
        self.rules = rules

    @property
    def version(self) -> str:
        return self.rules.version

    def _fields(self, item: ClassifierInput) -> dict[str, str]:
        fields = {"title": match_text(item.title)}
        if item.abstract:
            fields["abstract"] = match_text(item.abstract)
        return fields

    def _hits(self, fields: dict[str, str]) -> tuple[list[Hit], list[str]]:
        combined = " \n ".join(fields.values())
        suppressed: set[str] = set()
        contexts: list[str] = []
        for ctx_spec, regex in self.rules.contexts:
            if regex.search(combined):
                contexts.append(ctx_spec.id)
                suppressed.update(ctx_spec.suppresses)
        hits: list[Hit] = []
        for compiled in self.rules.rules:
            spec = compiled.spec
            if spec.id in suppressed:
                continue
            names = ("title", "abstract") if spec.field == "title_or_abstract" else (spec.field,)
            for name in names:
                text = fields.get(name)
                if not text:
                    continue
                for m in compiled.regex.finditer(text):
                    hits.append(
                        Hit(
                            spec.id,
                            spec.evidence_group,
                            name,
                            m.start(),
                            m.end(),
                            m.group(0),
                            _snippet(text, m.start(), m.end()),
                            spec.area,
                            spec.topic,
                        )
                    )
        return hits, contexts

    def _named_cryptanalysis(self, title: str) -> Hit | None:
        for m in self.rules.allowlist_regex.finditer(title):
            name = m.group("name").rstrip("-").casefold()
            if name in self.rules.allowlist:
                return Hit(
                    "SP_CRYPTANALYSIS_NAMED",
                    "security_method",
                    "title",
                    m.start(),
                    m.end(),
                    m.group(0),
                    _snippet(title, m.start(), m.end()),
                    "security-proofs",
                    "cryptanalysis",
                )
        return None

    def classify(
        self, item: ClassifierInput, override: ClassificationOverride | None = None
    ) -> ClassificationResult:
        fields = self._fields(item)
        hits, contexts = self._hits(fields)
        by_group: dict[str, list[Hit]] = {}
        for hit in hits:
            by_group.setdefault(hit.group, []).append(hit)

        def group(*names: str) -> list[Hit]:
            return [h for n in names for h in by_group.get(n, [])]

        supported: set[str] = set()
        reasons: list[str] = []
        evidence_hits: list[Hit] = list(hits)

        # proof-systems (10.3 step 2)
        direct = group("ps_direct")
        component = group("ps_component")
        crypto = group("crypto_context")
        if any(h.field == "title" for h in direct):
            supported.add("proof-systems")
        elif any(independent(d, c) for d in direct for c in crypto + component):
            supported.add("proof-systems")
        elif any(
            independent(p, c) and "title" in (p.field, c.field) for p in component for c in crypto
        ):
            supported.add("proof-systems")

        # security-proofs (step 3)
        methods = group("security_method")
        objects = group("protocol_object", "crypto_context")
        if any(
            independent(m, o) and "title" in (m.field, o.field) for m in methods for o in objects
        ):
            supported.add("security-proofs")
        else:
            named = self._named_cryptanalysis(fields["title"])
            if named is not None:
                supported.add("security-proofs")
                evidence_hits.append(named)

        # formal-verification (step 4)
        formal = group("formal_method", "formal_tool")
        crypto_or_object = group("crypto_context", "protocol_object")
        if any(independent(f, c) for f in formal for c in crypto_or_object):
            in_title = any(f.field == "title" for f in formal)
            distinct_abstract: list[Hit] = []
            for f in (h for h in formal if h.field == "abstract"):
                if all(
                    not f.overlaps(o) and f.text.casefold() != o.text.casefold()
                    for o in distinct_abstract
                ):
                    distinct_abstract.append(f)
            if in_title or len(distinct_abstract) >= 2:
                supported.add("formal-verification")

        crypto_evidence = bool(crypto_or_object)
        if (direct or component) and "proof-systems" not in supported:
            reasons.append("proof-system cue without the required corroborating context")
        if methods and "security-proofs" not in supported:
            reasons.append(
                "security-method cue without protocol/cryptographic context in the title"
            )
        if formal and crypto_evidence and "formal-verification" not in supported:
            reasons.append(
                "formal-method cue with cryptographic context but insufficient formal evidence"
            )
        ambiguous_title = [h for h in group("ambiguous_only") if h.field == "title"]
        if ambiguous_title and (crypto_evidence or (item.venue_evidence and not item.abstract)):
            reasons.append(
                "ambiguous title term ("
                + ", ".join(sorted({h.text for h in ambiguous_title}))
                + ") with cryptographic evidence"
            )
        if not item.abstract and item.venue_evidence and not supported:
            reasons.append("abstract unavailable; source/venue indicates cryptography")

        decision: Decision
        if supported and contexts:
            decision = "review"
            reasons.insert(0, "conflicting context: " + ", ".join(contexts))
        elif supported:
            decision = "include"
        elif reasons:
            decision = "review"
        else:
            decision = "exclude"

        areas = [a for a in AREAS if a in supported]
        topics = sorted({h.topic for h in evidence_hits if h.topic and h.area in supported})
        contributions = self._contributions(fields) if decision != "exclude" else []
        matched = [
            RuleMatch(
                rule_id=h.rule_id,
                evidence_group=h.group,
                field=h.field,
                snippet=h.snippet,
                normalized_start=h.start,
                normalized_end=h.end,
            )
            for h in sorted(evidence_hits, key=lambda h: (h.field != "title", h.start, h.rule_id))
        ]
        if override is not None:
            return ClassificationResult(
                decision=override.decision,
                areas=override.areas,
                topics=sorted(override.topics),
                contributions=override.contributions,
                matched_rules=matched,
                review_reasons=[f"curation override: {override.reason}"],
                rules_version=self.version,
                review_provenance=override.review_provenance,
            )
        return ClassificationResult(
            decision=decision,
            areas=areas,
            topics=topics,
            contributions=contributions,
            matched_rules=matched,
            review_reasons=reasons if decision == "review" else [],
            rules_version=self.version,
            review_provenance="automatic",
        )

    def _contributions(self, fields: dict[str, str]) -> list[Contribution]:
        found: set[str] = set()
        for spec, regex in self.rules.contributions:
            names: Iterable[str] = (
                ("title", "abstract") if spec.field == "title_or_abstract" else (spec.field,)
            )
            if any(regex.search(fields[n]) for n in names if n in fields):
                found.add(spec.contribution)
        return [c for c in CONTRIBUTIONS if c in found]
