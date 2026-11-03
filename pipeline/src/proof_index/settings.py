"""The one place environment variables are read (spec section 4).

Settings are loaded lazily; nothing here touches the network or the database.
Commands ask for what they need through ``require_*`` helpers, which raise a
``ConfigError`` naming the missing variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from proof_index.errors import ConfigError

ENV_KEYS: dict[str, str] = {
    "database_url": "DATABASE_URL",
    "blob_backend": "BLOB_BACKEND",
    "local_blob_root": "LOCAL_BLOB_ROOT",
    "export_root": "EXPORT_ROOT",
    "source_config": "SOURCE_CONFIG",
    "site_config": "SITE_CONFIG",
    "public_site_url": "PUBLIC_SITE_URL",
    "public_base_path": "PUBLIC_BASE_PATH",
    "s3_bucket": "S3_BUCKET",
    "s3_endpoint_url": "S3_ENDPOINT_URL",
    "aws_region": "AWS_REGION",
    "project_contact": "PROJECT_CONTACT",
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database_url: str | None = None
    blob_backend: Literal["local", "s3"] = "local"
    local_blob_root: Path = Path("./work/blobs")
    export_root: Path = Path("./work/releases")
    source_config: Path = Path("./config/sources.yaml")
    site_config: Path = Path("./config/site.yaml")
    public_site_url: str = "http://localhost:4321"
    public_base_path: str = Field(default="/")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    aws_region: str | None = None
    project_contact: str | None = None

    @field_validator("public_base_path")
    @classmethod
    def _base_path(cls, value: str) -> str:
        if not value.startswith("/") or not value.endswith("/"):
            raise ValueError(
                "PUBLIC_BASE_PATH must start and end with '/' (e.g. / or /proof-papers/)"
            )
        if "//" in value or ".." in value:
            raise ValueError("PUBLIC_BASE_PATH must not contain '//' or '..'")
        return value

    @field_validator("public_site_url")
    @classmethod
    def _site_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("PUBLIC_SITE_URL must be an http(s) origin")
        return value.rstrip("/")

    # -- requirement helpers -------------------------------------------------

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigError(
                "DATABASE_URL is not set. Copy .env.example to .env for local work, "
                "or export DATABASE_URL."
            )
        return self.database_url

    def require_s3(self) -> tuple[str, str | None, str | None]:
        if self.blob_backend != "s3":
            raise ConfigError("BLOB_BACKEND is not 's3'")
        if not self.s3_bucket:
            raise ConfigError("BLOB_BACKEND=s3 requires S3_BUCKET")
        return self.s3_bucket, self.s3_endpoint_url, self.aws_region

    def missing(self) -> list[str]:
        """Environment variable names whose absence would block a normal pipeline run."""
        out: list[str] = []
        if not self.database_url:
            out.append("DATABASE_URL")
        if self.blob_backend == "s3" and not self.s3_bucket:
            out.append("S3_BUCKET")
        return out


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` dotenv file. Comments and blank lines are ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = Path(".env"),
) -> Settings:
    """Build settings from the process environment, with ``.env`` as a fallback only.

    Real environment variables always win over ``.env`` values. Empty strings are
    treated as unset.
    """
    env: dict[str, str] = {}
    if dotenv_path is not None and dotenv_path.is_file():
        env.update(parse_dotenv(dotenv_path.read_text(encoding="utf-8")))
    env.update(os.environ if environ is None else environ)

    data: dict[str, str] = {}
    for field_name, env_key in ENV_KEYS.items():
        value = env.get(env_key)
        if value is not None and value.strip() != "":
            data[field_name] = value.strip()
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{ENV_KEYS.get(str(err['loc'][0]), err['loc'][0])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None


def redact_database_url(url: str) -> str:
    """Render a database URL with its password hidden. Never raises."""
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable DATABASE_URL>"
