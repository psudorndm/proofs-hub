"""Alembic environment. Migrations are explicit; online mode only."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from proof_index.db.tables import metadata
from proof_index.settings import load_settings

config = context.config
target_metadata = metadata


def _url() -> str:
    explicit = config.attributes.get("database_url") or context.get_x_argument(
        as_dictionary=True
    ).get("url")
    if explicit:
        return str(explicit)
    return load_settings().require_database_url()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
