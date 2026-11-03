"""``config/site.yaml``: public site identity (configurable, no invented affiliation)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from proof_index.errors import ConfigError


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    description: str
    scope_line: str
    maintainer_label: str | None = None
    contact: str | None = None


def load_site(path: Path) -> SiteConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"site configuration not found: {path}") from None
    try:
        return SiteConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid site configuration: {exc}") from None
