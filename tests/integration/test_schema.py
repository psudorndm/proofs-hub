"""Schema, constraint, and transaction behavior against real PostgreSQL 17."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, func, insert, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from proof_index.db import tables as t
from proof_index.db.repository import (
    current_revision,
    downgrade,
    make_engine,
    migrate,
    new_id,
    transaction,
)

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _stream(conn: Connection) -> uuid.UUID:
    sid = new_id()
    conn.execute(
        insert(t.provider_streams).values(
            id=sid,
            provider="arxiv",
            stream_key=f"arxiv:test:{sid}",
            config_hash="c",
            created_at=NOW,
        )
    )
    return sid


def _run(conn: Connection, stream_id: uuid.UUID) -> uuid.UUID:
    rid = new_id()
    conn.execute(
        insert(t.harvest_runs).values(
            id=rid,
            stream_id=stream_id,
            mode="sync",
            state="fetching",
            started_at=NOW,
            config_hash="c",
        )
    )
    return rid


def _page(conn: Connection, run_id: uuid.UUID, ordinal: int = 0) -> uuid.UUID:
    pid = new_id()
    conn.execute(
        insert(t.harvest_pages).values(
            id=pid,
            run_id=run_id,
            ordinal=ordinal,
            request_fingerprint="f",
            blob_key="k",
            raw_sha256="h",
            next_cursor=None,
            is_terminal=True,
            parsed_count=0,
            state="durable",
            fetched_at=NOW,
        )
    )
    return pid


def _source_record(conn: Connection, source_id: str = "2101.00001") -> uuid.UUID:
    rid = new_id()
    conn.execute(
        insert(t.source_records).values(
            id=rid, source="arxiv", source_id=source_id, first_seen_at=NOW, last_seen_at=NOW
        )
    )
    return rid


def _observation(
    conn: Connection, record_id: uuid.UUID, run_id: uuid.UUID, page_id: uuid.UUID
) -> uuid.UUID:
    oid = new_id()
    conn.execute(
        insert(t.source_observations).values(
            id=oid,
            source_record_id=record_id,
            observation_key=f"k-{oid}",
            run_id=run_id,
            page_id=page_id,
            normalized={},
            content_sha256="h",
            observed_at=NOW,
            change_kind="initial",
            is_current_eligible=True,
            parser_version="p1",
        )
    )
    return oid


def _work(conn: Connection, **values: Any) -> uuid.UUID:
    wid = new_id()
    row = {
        "id": wid,
        "visibility": "included",
        "record_status": "active",
        "canonical": {},
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(values)
    conn.execute(insert(t.works).values(**row))
    return wid


def _command(conn: Connection) -> uuid.UUID:
    cid = new_id()
    conn.execute(
        insert(t.curation_commands).values(
            id=cid,
            command_hash=f"h-{cid}",
            command_id=f"cmd-{cid}",
            kind="include",
            payload={},
            reviewer_label="tester",
            review_kind="agent-provisional",
            reason="test",
            applied_at=NOW,
            before_state={},
            after_state={},
        )
    )
    return cid


def test_migrate_empty_database_and_round_trip(empty_db_url: str) -> None:
    migrate(empty_db_url)
    eng = make_engine(empty_db_url)
    try:
        with eng.connect() as conn:
            assert current_revision(conn) == "0002"
            names = set(inspect(conn).get_table_names())
        expected = {table.name for table in t.metadata.sorted_tables}
        assert expected <= names
        downgrade(empty_db_url, "base")
        with eng.connect() as conn:
            assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        migrate(empty_db_url)
        with eng.connect() as conn:
            assert current_revision(conn) == "0002"
    finally:
        eng.dispose()


def test_migration_matches_table_metadata(engine: Engine) -> None:
    """The migration and the Core metadata must not drift apart."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), t.metadata)
    assert diff == []


def test_foreign_keys_enforced(engine: Engine) -> None:
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(
            insert(t.source_records).values(
                id=new_id(),
                source="arxiv",
                source_id="2101.00002",
                work_id=uuid.uuid4(),
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )


def test_source_identity_unique(engine: Engine) -> None:
    with transaction(engine) as conn:
        _source_record(conn, "2101.00003")
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        _source_record(conn, "2101.00003")


def test_current_observation_must_belong_to_record(engine: Engine) -> None:
    with transaction(engine) as conn:
        run = _run(conn, _stream(conn))
        page = _page(conn, run)
        rec_a = _source_record(conn, "2101.00004")
        rec_b = _source_record(conn, "2101.00005")
        obs_a = _observation(conn, rec_a, run, page)
        conn.execute(
            update(t.source_records)
            .where(t.source_records.c.id == rec_a)
            .values(current_observation_id=obs_a)
        )
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(
            update(t.source_records)
            .where(t.source_records.c.id == rec_b)
            .values(current_observation_id=obs_a)
        )


def test_check_constraints(engine: Engine) -> None:
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        _work(conn, visibility="hidden")
    with transaction(engine) as conn:
        wid = _work(conn)
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(update(t.works).where(t.works.c.id == wid).values(merged_into=wid))
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        run = _run(conn, _stream(conn))
        conn.execute(
            insert(t.harvest_pages).values(
                id=new_id(),
                run_id=run,
                ordinal=0,
                request_fingerprint="f",
                blob_key="k",
                raw_sha256="h",
                next_cursor={"token": "x"},
                is_terminal=True,  # terminal page with a cursor is inconsistent
                parsed_count=0,
                state="durable",
                fetched_at=NOW,
            )
        )


def test_paused_state_requires_phase(engine: Engine) -> None:
    with transaction(engine) as conn:
        run = _run(conn, _stream(conn))
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(
            update(t.harvest_runs).where(t.harvest_runs.c.id == run).values(state="paused")
        )
    with transaction(engine) as conn:
        conn.execute(
            update(t.harvest_runs)
            .where(t.harvest_runs.c.id == run)
            .values(state="paused", paused_phase="fetching")
        )


def test_separation_pair_sorted_and_distinct(engine: Engine) -> None:
    with transaction(engine) as conn:
        a, b = sorted([_work(conn), _work(conn)])
        cmd = _command(conn)
        conn.execute(insert(t.separation_constraints).values(work_a=a, work_b=b, command_id=cmd))
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(insert(t.separation_constraints).values(work_a=b, work_b=a, command_id=cmd))
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(insert(t.separation_constraints).values(work_a=a, work_b=a, command_id=cmd))


def test_curation_commands_are_immutable(engine: Engine) -> None:
    with transaction(engine) as conn:
        cid = _command(conn)
    with pytest.raises(DBAPIError, match="immutable"), transaction(engine) as conn:
        conn.execute(
            update(t.curation_commands).where(t.curation_commands.c.id == cid).values(reason="x")
        )
    with pytest.raises(DBAPIError, match="immutable"), transaction(engine) as conn:
        conn.execute(text("DELETE FROM curation_commands"))


def test_no_cascading_deletes(engine: Engine) -> None:
    with transaction(engine) as conn:
        wid = _work(conn)
        conn.execute(
            insert(t.source_records).values(
                id=new_id(),
                source="arxiv",
                source_id="2101.00006",
                work_id=wid,
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        conn.execute(t.works.delete().where(t.works.c.id == wid))
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.source_records)).scalar_one() == 1


def test_failing_transaction_rolls_back_cleanly(engine: Engine) -> None:
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        _source_record(conn, "2101.00007")
        _work(conn)
        _source_record(conn, "2101.00007")  # duplicate -> whole transaction fails
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(t.source_records)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(t.works)).scalar_one() == 0


def test_staged_promoted_state_requires_observation(engine: Engine) -> None:
    with pytest.raises(IntegrityError), transaction(engine) as conn:
        run = _run(conn, _stream(conn))
        page = _page(conn, run)
        conn.execute(
            insert(t.staged_records).values(
                id=new_id(),
                run_id=run,
                page_id=page,
                source="arxiv",
                source_id="2101.00008",
                payload={},
                content_sha256="h",
                item_kind="record",
                state="promoted",
                item_ordinal=0,
            )
        )
