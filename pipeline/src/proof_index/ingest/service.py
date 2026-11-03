"""Wire the runner from settings for CLI use (sync/backfill)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Engine, select

from proof_index.classify.engine import Classifier
from proof_index.classify.rules import load_rules
from proof_index.classify.store import ClassificationHook
from proof_index.clock import Clock, SystemClock
from proof_index.config.sources import SourcesConfig, load_sources
from proof_index.db import tables as t
from proof_index.db.repository import make_engine
from proof_index.errors import ConfigError, InputError
from proof_index.http.client import ProviderHttpClient
from proof_index.http.rate_limit import Budget, LimiterRegistry
from proof_index.ingest.hooks import HookChain, PromotionHooks
from proof_index.ingest.runner import RESUMABLE_STATES, HarvestRunner, RunSummary
from proof_index.models.classification import load_taxonomy
from proof_index.resolve.canonical import CanonicalBuilder
from proof_index.resolve.identity import IdentityResolver
from proof_index.settings import Settings
from proof_index.storage.factory import make_blob_store

PAGED_ADAPTERS = {"eprint", "arxiv_oai", "arxiv_atom"}


@dataclass
class Pipeline:
    settings: Settings
    config: SourcesConfig
    engine: Engine
    clock: Clock

    @classmethod
    def from_settings(cls, settings: Settings, clock: Clock | None = None) -> Pipeline:
        return cls(
            settings=settings,
            config=load_sources(settings.source_config),
            engine=make_engine(settings.require_database_url()),
            clock=clock or SystemClock(),
        )

    def runner(self, hooks: PromotionHooks | None = None) -> HarvestRunner:
        limiters = LimiterRegistry(self.config.rate_intervals())
        user_agent = self.config.render_user_agent(self.settings.project_contact)

        def http_factory(provider: str, budget: Budget) -> ProviderHttpClient:
            return ProviderHttpClient(
                user_agent=user_agent,
                limiter=limiters.get(provider),
                budget=budget,
                clock=self.clock,
            )

        return HarvestRunner(
            self.engine,
            make_blob_store(self.settings),
            self.config,
            self.clock,
            http_factory,
            hooks=hooks if hooks is not None else default_hooks(self),
        )


def crypto_venue_keys(path: Path = Path("config/venues.yaml")) -> frozenset[str]:
    """Venue keys whose papers carry cryptographic venue evidence (registry from T10)."""
    if not path.is_file():
        return frozenset()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return frozenset(str(v["key"]) for v in data.get("venues", []) if v.get("cryptographic", False))


def classification_hook(pipeline: Pipeline) -> ClassificationHook:
    taxonomy = load_taxonomy(Path("config/taxonomy.yaml"))
    rules = load_rules(Path("config/relevance-rules.yaml"), taxonomy)
    return ClassificationHook(Classifier(rules), pipeline.clock, crypto_venue_keys())


def identity_resolver(pipeline: Pipeline, rules_version: str) -> IdentityResolver:
    builder = CanonicalBuilder(pipeline.clock, rules_version)
    return IdentityResolver(pipeline.clock, rules_version, builder.rebuild)


def default_hooks(pipeline: Pipeline) -> PromotionHooks:
    """Classification, then identity resolution and canonical rebuild."""
    classify_hook = classification_hook(pipeline)
    return HookChain(classify_hook, identity_resolver(pipeline, classify_hook.classifier.version))


def select_streams(
    config: SourcesConfig, source: str, kinds: tuple[str, ...], stream: str | None
) -> list[str]:
    if source not in config.providers:
        raise InputError(f"unknown source {source!r}")
    if stream is not None:
        chosen = config.stream(stream)
        if chosen.provider != source:
            raise InputError(f"stream {stream} does not belong to {source}")
        return [chosen.key]
    keys = [
        s.key
        for s in config.streams
        if s.provider == source and s.enabled and s.kind in kinds and s.adapter in PAGED_ADAPTERS
    ]
    if not keys:
        raise ConfigError(f"no enabled paged {'/'.join(kinds)} streams for {source}")
    return keys


def has_resumable_run(engine: Engine, stream_key: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            select(t.harvest_runs.c.id)
            .select_from(
                t.harvest_runs.join(
                    t.provider_streams, t.provider_streams.c.id == t.harvest_runs.c.stream_id
                )
            )
            .where(t.provider_streams.c.stream_key == stream_key)
            .where(t.harvest_runs.c.state.in_(RESUMABLE_STATES))
            .limit(1)
        ).first()
    return row is not None


def overall_exit(summaries: list[RunSummary]) -> int:
    codes = [s.exit_code for s in summaries if s.exit_code != 0]
    return max(codes) if codes else 0
