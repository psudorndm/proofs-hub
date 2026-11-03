from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from proof_index.db.locks import (
    CATALOG_WRITER,
    advisory_lock,
    is_locked,
    lock_key,
    provider_lock_name,
)
from proof_index.db.repository import make_engine
from proof_index.errors import LockHeldError

pytestmark = pytest.mark.db


def test_second_holder_gets_lock_held_error(engine: Engine, db_url: str) -> None:
    other = make_engine(db_url)
    try:
        with advisory_lock(engine, provider_lock_name("arxiv")):
            assert is_locked(other, provider_lock_name("arxiv"))
            with pytest.raises(LockHeldError) as info, advisory_lock(other, "provider:arxiv"):
                pass
            assert info.value.exit_code == 5
            # Different lock names are independent.
            with advisory_lock(other, CATALOG_WRITER):
                pass
        assert not is_locked(other, provider_lock_name("arxiv"))
    finally:
        other.dispose()


def test_lock_released_when_body_raises(engine: Engine) -> None:
    with pytest.raises(RuntimeError), advisory_lock(engine, CATALOG_WRITER):
        raise RuntimeError("boom")
    assert not is_locked(engine, CATALOG_WRITER)


def test_crashed_session_releases_lock(engine: Engine) -> None:
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_key(CATALOG_WRITER)})
    pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
    assert is_locked(engine, CATALOG_WRITER)
    # Simulate process death: the server-side session ends without an unlock.
    with engine.connect() as admin:
        admin.execute(text("SELECT pg_terminate_backend(:p, 5000)"), {"p": pid})
    conn.invalidate()
    assert not is_locked(engine, CATALOG_WRITER)


def test_lock_holds_no_open_transaction(engine: Engine) -> None:
    with advisory_lock(engine, provider_lock_name("eprint")), engine.connect() as probe:
        idle_in_tx = probe.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'idle in transaction'"
            )
        ).scalar_one()
    assert idle_in_tx == 0


def test_lock_keys_are_stable() -> None:
    assert lock_key("provider:arxiv") == lock_key("provider:arxiv")
    assert lock_key("provider:arxiv") != lock_key("provider:eprint")
