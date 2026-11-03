"""``config/sources.yaml`` model: providers, rate limits, and harvest streams.

A stream's ``config_hash`` covers everything that changes what it requests. A
different hash for the same stream key is refused by the runner: changing query
configuration needs a new stream key (spec section 8).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from proof_index.errors import ConfigError
from proof_index.hashing import sha256_json
from proof_index.models.source import SourceName

_STREAM_KEY = re.compile(r"^[a-z0-9]+(?::[A-Za-z0-9._\-]+)+:v\d+$")


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_interval_seconds: float = Field(gt=0)
    endpoints: dict[str, str]
    max_response_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    guidance_urls: list[str] = Field(default_factory=list)
    guidance_checked_on: str | None = None
    live_verified: bool = False
    notes: str | None = None


class StreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    provider: SourceName
    adapter: Literal["eprint", "arxiv_oai", "arxiv_atom", "dblp", "crossref", "curated"]
    kind: Literal["bootstrap", "incremental", "supplemental", "snapshot", "enrichment"]
    enabled: bool = True
    overlap_days: int = Field(default=2, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)
    # For incremental streams: the bootstrap stream whose completion it reconciles.
    pairs_with: str | None = None

    @model_validator(mode="after")
    def _key(self) -> Self:
        if not _STREAM_KEY.match(self.key):
            raise ValueError(f"stream key {self.key!r} must look like provider:...:vN")
        if not self.key.startswith(f"{self.provider}:"):
            raise ValueError(f"stream key {self.key!r} must start with its provider")
        return self

    def config_hash(self) -> str:
        return sha256_json(
            {
                "provider": self.provider,
                "adapter": self.adapter,
                "kind": self.kind,
                "params": self.params,
            }
        )


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    user_agent: str
    providers: dict[SourceName, ProviderConfig]
    streams: list[StreamConfig]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        keys = [s.key for s in self.streams]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate stream keys")
        for stream in self.streams:
            if stream.provider not in self.providers:
                raise ValueError(f"stream {stream.key} uses unconfigured provider")
            if stream.pairs_with is not None and stream.pairs_with not in keys:
                raise ValueError(f"stream {stream.key} pairs_with unknown {stream.pairs_with}")
        return self

    def provider(self, name: str) -> ProviderConfig:
        try:
            return self.providers[name]  # type: ignore[index]
        except KeyError:
            raise ConfigError(f"provider {name!r} is not configured") from None

    def stream(self, key: str) -> StreamConfig:
        for stream in self.streams:
            if stream.key == key:
                return stream
        raise ConfigError(f"stream {key!r} is not configured in sources.yaml")

    def streams_for(self, provider: str, kind: str | None = None) -> list[StreamConfig]:
        return [
            s
            for s in self.streams
            if s.provider == provider and s.enabled and (kind is None or s.kind == kind)
        ]

    def rate_intervals(self) -> dict[str, float]:
        return {name: p.min_interval_seconds for name, p in self.providers.items()}

    def config_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))

    def render_user_agent(self, contact: str | None) -> str:
        suffix = f"; {contact}" if contact else ""
        return self.user_agent.replace("{contact}", suffix)


def load_sources(path: Path) -> SourcesConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"source configuration not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"source configuration is not valid YAML: {exc}") from None
    try:
        return SourcesConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid source configuration {path}: {exc}") from None
