"""Identity keys and candidate lookups (spec section 11.1)."""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import Connection, and_, select, update

from proof_index.db import tables as t
from proof_index.models.source import NormalizedRecord, Tombstone
from proof_index.normalize.authors import author_keys
from proof_index.normalize.identifiers import (
    normalize_dblp_key,
    normalize_doi,
    normalize_eprint_id,
    parse_arxiv_id,
    qualified_identifier,
)
from proof_index.normalize.text import meaningful_title_tokens, title_key

SIMILARITY_THRESHOLD = 0.85
MIN_TITLE_TOKENS = 5
MAX_ALIAS_HOPS = 64


def explicit_refs(record: NormalizedRecord) -> list[str]:
    refs: set[str] = set()
    for kind, values in record.identifiers.items():
        for value in values:
            if kind == "eprint" and (n := normalize_eprint_id(value)):
                refs.add(qualified_identifier("eprint", n))
            elif kind == "arxiv" and (a := parse_arxiv_id(value)):
                refs.add(qualified_identifier("arxiv", a.base))
            elif kind == "doi" and (d := normalize_doi(value)):
                refs.add(f"doi:{d}")
            elif kind == "dblp" and (k := normalize_dblp_key(value)):
                refs.add(qualified_identifier("dblp", k))
    refs.discard(qualified_identifier(record.source, record.source_id))
    return sorted(refs)


def update_identity_keys(
    conn: Connection, source_record_id: uuid.UUID, item: NormalizedRecord | Tombstone
) -> None:
    if isinstance(item, Tombstone):
        return  # keep the last known keys; deletion does not change identity
    conn.execute(
        update(t.source_records)
        .where(t.source_records.c.id == source_record_id)
        .values(
            title_key=title_key(item.title),
            title_tokens=sorted(set(meaningful_title_tokens(item.title))),
            doi=item.doi,
            explicit_refs=explicit_refs(item),
        )
    )


def resolve_live(conn: Connection, work_id: uuid.UUID) -> uuid.UUID:
    """Follow ``merged_into`` to the live survivor, failing on cycles."""
    seen: set[uuid.UUID] = set()
    current = work_id
    for _ in range(MAX_ALIAS_HOPS):
        if current in seen:
            raise ValueError(f"merge alias cycle at work {current}")
        seen.add(current)
        nxt = conn.execute(
            select(t.works.c.merged_into).where(t.works.c.id == current)
        ).scalar_one()
        if nxt is None:
            return current
        current = nxt
    raise ValueError(f"merge alias chain too long from work {work_id}")


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def record_by_qualified_id(conn: Connection, qid: str) -> Any | None:
    if qid.startswith("doi:"):
        return None
    source, _, source_id = qid.partition(":")
    if source == "curated":
        source_id = qid
    return conn.execute(
        select(t.source_records).where(
            and_(t.source_records.c.source == source, t.source_records.c.source_id == source_id)
        )
    ).first()


def records_referencing(conn: Connection, qid: str, exclude: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            select(t.source_records)
            .where(t.source_records.c.explicit_refs.contains([qid]))
            .where(t.source_records.c.id != exclude)
            .where(t.source_records.c.work_id.is_not(None))
        ).all()
    )


def records_with_doi(conn: Connection, doi: str, exclude: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            select(t.source_records)
            .where(t.source_records.c.doi == doi)
            .where(t.source_records.c.id != exclude)
            .where(t.source_records.c.work_id.is_not(None))
        ).all()
    )


def works_with_publication_doi(conn: Connection, doi: str) -> list[uuid.UUID]:
    return list(
        conn.execute(select(t.publications.c.work_id).where(t.publications.c.doi == doi)).scalars()
    )


def records_with_title_key(conn: Connection, key: str, exclude: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            select(t.source_records)
            .where(t.source_records.c.title_key == key)
            .where(t.source_records.c.id != exclude)
            .where(t.source_records.c.work_id.is_not(None))
        ).all()
    )


def similar_title_records(
    conn: Connection, tokens: list[str], exclude: uuid.UUID
) -> list[tuple[Any, float]]:
    """Prefix-filtered candidates with title-token Jaccard >= threshold."""
    unique = sorted(set(tokens))
    if len(unique) < MIN_TITLE_TOKENS:
        return []
    prefix_len = len(unique) - math.ceil(SIMILARITY_THRESHOLD * len(unique)) + 1
    prefix = unique[:prefix_len]
    rows = conn.execute(
        select(t.source_records)
        .where(t.source_records.c.title_tokens.overlap(prefix))
        .where(t.source_records.c.id != exclude)
        .where(t.source_records.c.work_id.is_not(None))
        .limit(500)
    ).all()
    out = []
    for row in rows:
        score = jaccard(set(unique), set(row.title_tokens or []))
        if score >= SIMILARITY_THRESHOLD:
            out.append((row, score))
    return out


def current_record(conn: Connection, source_record_id: uuid.UUID) -> NormalizedRecord | None:
    row = conn.execute(
        select(t.source_observations.c.normalized, t.source_observations.c.item_kind)
        .select_from(
            t.source_records.join(
                t.source_observations,
                t.source_observations.c.id == t.source_records.c.current_observation_id,
            )
        )
        .where(t.source_records.c.id == source_record_id)
    ).first()
    if row is None or row.item_kind != "record":
        return None
    return NormalizedRecord.model_validate(row.normalized)


def authors_of(record: NormalizedRecord) -> list[str]:
    return author_keys([a.display_name for a in record.authors])
