"""Shared fixtures.

Database tests use real PostgreSQL. ``TEST_DATABASE_ADMIN_URL`` points at a server
where the tests may create and drop databases named ``proofs_test_*``. They never
use ``DATABASE_URL``. A migrated template database is built once per session and
each test gets a fresh copy.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from proof_index.db.repository import make_engine, migrate

TEST_PREFIX = "proofs_test_"


def _admin_url() -> str | None:
    return os.environ.get("TEST_DATABASE_ADMIN_URL") or None


def _with_database(url: str, name: str) -> str:
    assert name.startswith(TEST_PREFIX), "refusing to touch a non-test database"
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _admin_engine(url: str) -> Engine:
    return create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _admin_url():
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_ADMIN_URL not set (run `make test-db`)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = _admin_url()
    if url is None:
        pytest.skip("TEST_DATABASE_ADMIN_URL not set")
    return url


@pytest.fixture(scope="session")
def template_db(admin_url: str) -> Iterator[str]:
    name = f"{TEST_PREFIX}tmpl_{uuid.uuid4().hex[:10]}"
    admin = _admin_engine(admin_url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = _with_database(admin_url, name)
    try:
        migrate(url)
        # Close every connection so the database can serve as a template.
        yield name
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def db_url(admin_url: str, template_db: str) -> Iterator[str]:
    name = f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}"
    admin = _admin_engine(admin_url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"'))
    try:
        yield _with_database(admin_url, name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def empty_db_url(admin_url: str) -> Iterator[str]:
    """A brand-new database with no schema, for migration tests."""
    name = f"{TEST_PREFIX}empty_{uuid.uuid4().hex[:10]}"
    admin = _admin_engine(admin_url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(admin_url, name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def engine(db_url: str) -> Iterator[Engine]:
    eng = make_engine(db_url)
    try:
        yield eng
    finally:
        eng.dispose()
