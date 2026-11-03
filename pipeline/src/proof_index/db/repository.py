"""Engine, transaction, and migration helpers.

Higher layers (ingest, resolve, publish) build their SQL on these primitives.
Network requests and sleeps must never happen inside ``transaction()``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from proof_index.errors import StorageError
from proof_index.settings import redact_database_url

REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "db" / "alembic.ini"


def new_id() -> uuid.UUID:
    """Application-generated UUID (spec section 5)."""
    return uuid.uuid4()


def make_engine(url: str, *, echo: bool = False) -> Engine:
    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """One short transaction: commits on success, rolls back on any exception."""
    try:
        with engine.begin() as conn:
            yield conn
    except OperationalError as exc:
        raise StorageError(
            f"database unavailable ({type(exc.orig).__name__ if exc.orig else 'OperationalError'})"
        ) from exc


@contextmanager
def repeatable_read(engine: Engine) -> Iterator[Connection]:
    """A read-only REPEATABLE READ snapshot (used by release export)."""
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="REPEATABLE READ")
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            yield conn


def migrate(url: str, revision: str = "head") -> None:
    """Apply Alembic migrations to ``url``."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "db" / "migrations"))
    cfg.attributes["database_url"] = url
    try:
        command.upgrade(cfg, revision)
    except DBAPIError as exc:
        raise StorageError(f"migration failed against {redact_database_url(url)}") from exc


def downgrade(url: str, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "db" / "migrations"))
    cfg.attributes["database_url"] = url
    command.downgrade(cfg, revision)


def current_revision(conn: Connection) -> str | None:
    row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return None if row is None else str(row[0])
