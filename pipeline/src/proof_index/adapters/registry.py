"""Map stream adapter names to adapter instances."""

from __future__ import annotations

from proof_index.adapters.base import SourceAdapter
from proof_index.config.sources import SourcesConfig
from proof_index.errors import ConfigError


def adapter_for(name: str, config: SourcesConfig) -> SourceAdapter:
    if name == "eprint":
        from proof_index.adapters.eprint import EprintOaiAdapter

        return EprintOaiAdapter(config)
    if name == "arxiv_oai":
        from proof_index.adapters.arxiv_oai import ArxivOaiAdapter

        return ArxivOaiAdapter(config)
    if name == "arxiv_atom":
        from proof_index.adapters.arxiv_atom import ArxivAtomAdapter

        return ArxivAtomAdapter(config)
    if name == "crossref":
        from proof_index.adapters.crossref import CrossrefAdapter

        return CrossrefAdapter(config)
    raise ConfigError(f"adapter {name!r} is not a paged HTTP adapter")
