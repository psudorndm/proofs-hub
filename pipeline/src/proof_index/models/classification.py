"""Taxonomy vocabulary and classification result models (spec sections 10 and 17)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from proof_index.errors import ConfigError

Area = Literal["proof-systems", "security-proofs", "formal-verification"]
AREAS: tuple[Area, ...] = ("proof-systems", "security-proofs", "formal-verification")
Contribution = Literal[
    "construction-theory",
    "analysis-attack",
    "implementation-optimization",
    "application",
    "survey-tutorial",
]
CONTRIBUTIONS: tuple[Contribution, ...] = (
    "construction-theory",
    "analysis-attack",
    "implementation-optimization",
    "application",
    "survey-tutorial",
)
Decision = Literal["include", "review", "exclude"]
ReviewProvenance = Literal["automatic", "agent-provisional", "human-reviewed"]

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    display_name: str

    @model_validator(mode="after")
    def _id_format(self) -> Self:
        if not _ID.match(self.id):
            raise ValueError(f"taxonomy id {self.id!r} must be lower-case kebab-case")
        return self


class AreaEntry(_Entry):
    pass


class TopicEntry(_Entry):
    areas: list[Area] = Field(min_length=1)


class ContributionEntry(_Entry):
    pass


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    areas: list[AreaEntry]
    topics: list[TopicEntry]
    contributions: list[ContributionEntry]

    @model_validator(mode="after")
    def _closed_vocabulary(self) -> Self:
        area_ids = [a.id for a in self.areas]
        if sorted(area_ids) != sorted(AREAS):
            raise ValueError(f"areas must be exactly {list(AREAS)}")
        contribution_ids = [c.id for c in self.contributions]
        if sorted(contribution_ids) != sorted(CONTRIBUTIONS):
            raise ValueError(f"contributions must be exactly {list(CONTRIBUTIONS)}")
        topic_ids = [t.id for t in self.topics]
        if len(set(topic_ids)) != len(topic_ids):
            raise ValueError("duplicate topic ids")
        overlap = set(topic_ids) & (set(area_ids) | set(contribution_ids))
        if overlap:
            raise ValueError(f"topic ids collide with area/contribution ids: {sorted(overlap)}")
        return self

    @property
    def topic_ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.topics)

    def topic(self, topic_id: str) -> TopicEntry:
        for entry in self.topics:
            if entry.id == topic_id:
                return entry
        raise KeyError(topic_id)

    def validate_labels(
        self, areas: list[str], topics: list[str], contributions: list[str]
    ) -> list[str]:
        """Return a list of problems (empty when all IDs are known)."""
        problems = [f"unknown area {a!r}" for a in areas if a not in AREAS]
        problems += [f"unknown topic {t!r}" for t in topics if t not in self.topic_ids]
        problems += [f"unknown contribution {c!r}" for c in contributions if c not in CONTRIBUTIONS]
        return problems

    def display_name(self, label_id: str) -> str:
        for entry in [*self.areas, *self.topics, *self.contributions]:
            if entry.id == label_id:
                return entry.display_name
        raise KeyError(label_id)


DEFAULT_TAXONOMY_PATH = Path("config/taxonomy.yaml")


def load_taxonomy(path: Path = DEFAULT_TAXONOMY_PATH) -> Taxonomy:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"taxonomy file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"taxonomy file is not valid YAML: {path}: {exc}") from None
    try:
        return Taxonomy.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid taxonomy {path}: {exc}") from None


class RuleMatch(BaseModel):
    """One rule hit. Offsets refer to the *normalized* field text, not the original."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    evidence_group: str
    field: Literal["title", "abstract", "venue"]
    snippet: str
    normalized_start: int
    normalized_end: int


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    areas: list[Area]
    topics: list[str]
    contributions: list[Contribution]
    matched_rules: list[RuleMatch]
    review_reasons: list[str]
    rules_version: str
    review_provenance: ReviewProvenance = "automatic"
