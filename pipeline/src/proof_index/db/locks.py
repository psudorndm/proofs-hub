"""PostgreSQL session advisory locks (spec sections 7–8).

Each lock lives on its own dedicated autocommit connection, so holding it never
holds an ordinary SQL transaction open during network sleeps or requests. A
process crash closes the connection and PostgreSQL releases the lock.

Keys are stable 64-bit integers derived from SHA-256, never Python's ``hash()``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

from proof_index.errors import LockHeldError
from proof_index.hashing import stable_int64

CATALOG_WRITER = "catalog-writer"


def provider_lock_name(provider: str) -> str:
    """Both arXiv adapters (Atom and OAI) map to the same ``provider:arxiv`` lock."""
    return f"provider:{provider}"


def lock_key(name: str) -> int:
    return stable_int64(f"proof-index-lock:{name}")


@contextmanager
def advisory_lock(engine: Engine, name: str) -> Iterator[None]:
    """Acquire ``name`` without waiting or raise ``LockHeldError`` (exit code 5)."""
    key = lock_key(name)
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar_one()
        if not acquired:
            raise LockHeldError(f"lock {name!r} is already held by another process")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    finally:
        conn.close()


def is_locked(engine: Engine, name: str) -> bool:
    """True when another session holds ``name`` (probes from a fresh session)."""
    try:
        with advisory_lock(engine, name):
            return False
    except LockHeldError:
        return True
