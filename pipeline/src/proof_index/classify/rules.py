"""Load and compile ``config/relevance-rules.yaml`` once (spec section 10.1).

Patterns come only from this versioned file; no user-supplied regex is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from proof_index.errors import ConfigError
from proof_index.hashing import sha256_json
from proof_index.models.classification import Area, Contribution, Taxonomy

EvidenceGroup = Literal[
    "ps_direct",
    "ps_component",
    "crypto_context",
    "security_method",
    "protocol_object",
    "formal_method",
    "formal_tool",
    "ambiguous_only",
]
FieldSpec = Literal["title", "abstract", "title_or_abstract"]
AREA_GROUPS: dict[str, Area] = {
    "ps_direct": "proof-systems",
    "ps_component": "proof-systems",
    "security_method": "security-proofs",
    "formal_method": "formal-verification",
    "formal_tool": "formal-verification",
}


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    version: int = 1
    area: Area | None = None
    topic: str | None = None
    field: FieldSpec = "title_or_abstract"
    case_sensitive: bool = False
    pattern: str
    evidence_group: EvidenceGroup

    @model_validator(mode="after")
    def _area_matches_group(self) -> Self:
        expected = AREA_GROUPS.get(self.evidence_group)
        if expected is not None and self.area != expected:
            raise ValueError(f"rule {self.id}: group {self.evidence_group} implies area {expected}")
        if expected is None and (self.area is not None or self.topic is not None):
            raise ValueError(f"rule {self.id}: context/ambiguous rules carry no area or topic")
        return self


class ContextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    pattern: str
    suppresses: list[str]
    case_sensitive: bool = False


class AllowlistSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provenance: str
    pattern: str
    schemes: list[str]


class ContributionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    contribution: Contribution
    pattern: str
    field: FieldSpec = "title_or_abstract"
    case_sensitive: bool = False


class RulesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^rules-v\d+$")
    provenance: str
    rules: list[RuleSpec]
    contradictory_contexts: list[ContextSpec]
    cryptanalysis_allowlist: AllowlistSpec
    contributions: list[ContributionSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [r.id for r in self.rules] + [c.id for c in self.contradictory_contexts]
        ids += [c.id for c in self.contributions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        known = {r.id for r in self.rules}
        for ctx in self.contradictory_contexts:
            missing = set(ctx.suppresses) - known
            if missing:
                raise ValueError(f"{ctx.id} suppresses unknown rules {sorted(missing)}")
            if any(not s.startswith("AMB_") for s in ctx.suppresses):
                raise ValueError(f"{ctx.id} may only suppress ambiguous_only rules")
        return self


@dataclass(frozen=True)
class CompiledRule:
    spec: RuleSpec
    regex: re.Pattern[str]


@dataclass(frozen=True)
class CompiledRules:
    version: str
    config_hash: str
    rules: tuple[CompiledRule, ...]
    contexts: tuple[tuple[ContextSpec, re.Pattern[str]], ...]
    allowlist_regex: re.Pattern[str]
    allowlist: frozenset[str]
    contributions: tuple[tuple[ContributionSpec, re.Pattern[str]], ...]


def _compile(pattern: str, case_sensitive: bool, ident: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(f"rule {ident}: invalid pattern ({exc})") from None


def compile_rules(spec: RulesFile, taxonomy: Taxonomy | None = None) -> CompiledRules:
    if taxonomy is not None:
        for rule in spec.rules:
            if rule.topic is not None and rule.topic not in taxonomy.topic_ids:
                raise ConfigError(f"rule {rule.id}: unknown topic {rule.topic!r}")
            if rule.topic is not None and rule.area not in taxonomy.topic(rule.topic).areas:
                raise ConfigError(f"rule {rule.id}: topic {rule.topic} is not under {rule.area}")
    return CompiledRules(
        version=spec.version,
        config_hash=sha256_json(spec.model_dump(mode="json")),
        rules=tuple(
            CompiledRule(r, _compile(r.pattern, r.case_sensitive, r.id)) for r in spec.rules
        ),
        contexts=tuple(
            (c, _compile(c.pattern, c.case_sensitive, c.id)) for c in spec.contradictory_contexts
        ),
        allowlist_regex=_compile(spec.cryptanalysis_allowlist.pattern, False, "allowlist"),
        allowlist=frozenset(s.casefold() for s in spec.cryptanalysis_allowlist.schemes),
        contributions=tuple(
            (c, _compile(c.pattern, c.case_sensitive, c.id)) for c in spec.contributions
        ),
    )


DEFAULT_RULES_PATH = Path("config/relevance-rules.yaml")


def load_rules(path: Path = DEFAULT_RULES_PATH, taxonomy: Taxonomy | None = None) -> CompiledRules:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"rules file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"rules file is not valid YAML: {exc}") from None
    try:
        spec = RulesFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid rules file {path}: {exc}") from None
    return compile_rules(spec, taxonomy)
