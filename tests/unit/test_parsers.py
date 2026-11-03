from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from proof_index.adapters.arxiv_atom import ArxivAtomAdapter, build_queries
from proof_index.adapters.arxiv_oai import ArxivOaiAdapter, explicit_eprint_ids
from proof_index.adapters.base import (
    Checkpoint,
    ExpiredCursor,
    HarvestRequest,
    PageStructureError,
    ParseContext,
    ProtocolError,
)
from proof_index.adapters.eprint import EprintOaiAdapter
from proof_index.config.sources import load_sources
from proof_index.models.source import NormalizedRecord, Tombstone

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
CONFIG = load_sources(ROOT / "config" / "sources.yaml")
FETCHED = datetime(2026, 11, 3, 6, 17, tzinfo=UTC)


def ctx(
    provider: str,
    stream: str,
    params: dict[str, object] | None = None,
    cursor: dict[str, object] | None = None,
) -> ParseContext:
    return ParseContext(
        provider=provider,  # type: ignore[arg-type]
        stream_key=stream,
        parser_version="test",
        fetched_at=FETCHED,
        plan_params=params or {},
        cursor=cursor,
    )


EPRINT = EprintOaiAdapter(CONFIG)
EPRINT_CTX = ctx("eprint", "eprint:all:bootstrap:v1")


def parse_eprint(name: str):  # type: ignore[no-untyped-def]
    return EPRINT.parse_page((FIX / "eprint" / name).read_bytes(), EPRINT_CTX)


# --- OAI protocol edges --------------------------------------------------------------


def test_oai_no_records_match_is_successful_empty_window() -> None:
    page = parse_eprint("synthetic-no-records-match.xml")
    assert page.records == [] and page.terminal and page.next_cursor is None
    assert page.no_records_match
    assert page.provider_response_at == datetime(2026, 11, 3, 6, 17, 5, tzinfo=UTC)


def test_bad_resumption_token_is_expired_cursor() -> None:
    with pytest.raises(ExpiredCursor):
        parse_eprint("synthetic-bad-resumption-token.xml")


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("synthetic-bad-argument.xml", "badArgument"),
        ("synthetic-cannot-disseminate.xml", "cannotDisseminateFormat"),
        ("synthetic-unknown-error.xml", "somethingOdd"),
    ],
)
def test_oai_protocol_errors_fail(name: str, code: str) -> None:
    with pytest.raises(ProtocolError) as info:
        parse_eprint(name)
    assert info.value.protocol_code == code
    assert not isinstance(info.value, ExpiredCursor)
    assert info.value.exit_code == 3


@pytest.mark.parametrize(
    "name",
    [
        "synthetic-html-login.html",
        "synthetic-truncated.xml",
        "synthetic-doctype-entity.xml",
        "synthetic-wrong-root.xml",
    ],
)
def test_malformed_pages_fail_structure(name: str) -> None:
    with pytest.raises(PageStructureError):
        parse_eprint(name)


def test_xml_declaration_alone_is_not_valid_data() -> None:
    with pytest.raises(PageStructureError):
        EPRINT.parse_page(b'<?xml version="1.0"?>', EPRINT_CTX)
    with pytest.raises(PageStructureError):
        EPRINT.parse_page(b"", EPRINT_CTX)


# --- ePrint mapping -------------------------------------------------------------------------


def test_eprint_page_mapping_and_quarantine() -> None:
    page = parse_eprint("synthetic-listrecords-page1.xml")
    assert not page.terminal
    assert page.next_cursor == {
        "resumptionToken": "synthetic-token-1",
        "cursor": "0",
        "completeListSize": 4,
    }
    assert page.reported_total == 4
    assert len(page.records) == 2
    assert len(page.quarantined_items) == 1
    failure = page.quarantined_items[0]
    assert failure.source_id == "2020/8" and failure.item_ordinal == 1

    record = page.records[0]
    assert isinstance(record, NormalizedRecord)
    assert record.source_id == "2020/7"
    assert record.title == "Zero-knowledge proofs for private arithmetic computations"
    assert [a.display_name for a in record.authors] == ["Alice Example", "Bob Example"]
    assert record.published.iso() == "2020-01-03"
    assert record.source_modified_at == datetime(2020, 2, 10, tzinfo=UTC)
    assert record.doi == "10.9999/synthetic.0001"
    # The javascript: identifier is dropped; the valid landing URL stays.
    assert [link.url for link in record.links] == ["https://eprint.iacr.org/2020/007"]
    assert record.harvest_datestamp == datetime(2026, 10, 30, tzinfo=UTC)

    tomb = page.records[1]
    assert isinstance(tomb, Tombstone) and tomb.source_id == "2019/100"


def test_eprint_terminal_page_with_empty_token() -> None:
    page = parse_eprint("synthetic-listrecords-page2.xml")
    assert page.terminal and page.next_cursor is None
    assert page.reported_total == 4
    record = page.records[0]
    assert isinstance(record, NormalizedRecord)
    assert record.published.precision == "year"
    assert record.source_modified_at is None


def test_eprint_request_planning() -> None:
    plan = EPRINT.plan(
        HarvestRequest(
            stream_key="eprint:all:incremental:v1",
            mode="sync",
            window_from=datetime(2026, 10, 30, tzinfo=UTC),
        ),
        Checkpoint(),
    )
    first = EPRINT.request_page(plan, None)
    assert dict(first.params) == {
        "verb": "ListRecords",
        "metadataPrefix": "oai_dc",
        "from": "2026-10-30",
    }
    nxt = EPRINT.request_page(plan, {"resumptionToken": "abc"})
    assert dict(nxt.params) == {"verb": "ListRecords", "resumptionToken": "abc"}
    assert first.fingerprint() != nxt.fingerprint()


# --- arXiv OAI ------------------------------------------------------------------------------


def test_arxiv_oai_mapping_filter_and_old_ids() -> None:
    adapter = ArxivOaiAdapter(CONFIG)
    params = CONFIG.stream("arxiv:cs-core:bootstrap:v1").params
    page = adapter.parse_page(
        (FIX / "arxiv" / "synthetic-oai-listrecords.xml").read_bytes(),
        ctx("arxiv", "arxiv:cs-core:bootstrap:v1", dict(params)),
    )
    assert page.terminal and page.filtered_count == 1
    ids = [r.source_id for r in page.records]
    assert ids == ["2001.00007", "cs/0101001", "2002.00001"]
    first = page.records[0]
    assert isinstance(first, NormalizedRecord)
    assert first.source_version is None
    assert first.title == "Zero-knowledge proofs for private arithmetic computations"
    assert [(a.display_name, a.given, a.family) for a in first.authors] == [
        ("Alice Example", "Alice", "Example"),
        ("Bob Example Jr", "Bob", "Example"),
    ]
    assert first.identifiers == {"eprint": ["2020/7"]}
    assert first.source_categories == ["cs.CC", "cs.CR"]
    old = page.records[1]
    assert isinstance(old, NormalizedRecord)
    assert old.source_modified_at == datetime(2001, 3, 15, tzinfo=UTC)
    assert isinstance(page.records[2], Tombstone)


def test_explicit_eprint_reference_extraction() -> None:
    assert explicit_eprint_ids("see https://eprint.iacr.org/2020/007") == ["2020/7"]
    assert explicit_eprint_ids("IACR Cryptology ePrint Archive, Report 2019/1021") == ["2019/1021"]
    assert explicit_eprint_ids("pages 2019/1021 of something") == []


# --- arXiv Atom ----------------------------------------------------------------------------------


def test_arxiv_atom_and_oai_share_source_identity() -> None:
    adapter = ArxivAtomAdapter(CONFIG)
    plan = adapter.plan(
        HarvestRequest(stream_key="arxiv:adjacent-terms:supplemental:v1", mode="backfill"),
        Checkpoint(),
    )
    page = adapter.parse_page(
        (FIX / "arxiv" / "synthetic-atom-feed.xml").read_bytes(),
        ctx("arxiv", plan.stream_key, plan.params, {"query_index": 0, "start": 0}),
    )
    atom = page.records[0]
    assert isinstance(atom, NormalizedRecord)
    assert (atom.source, atom.source_id, atom.source_version) == ("arxiv", "2001.00007", "v2")
    assert {link.kind for link in atom.links} == {"landing", "pdf"}
    assert atom.identifiers == {"eprint": ["2020/7"]}
    assert len(page.quarantined_items) == 1
    # Not terminal: 3 results reported, 2 entries returned.
    assert page.next_cursor == {"query_index": 0, "start": 2}


def test_atom_moves_to_next_query_then_terminates() -> None:
    adapter = ArxivAtomAdapter(CONFIG)
    body = (
        (FIX / "arxiv" / "synthetic-atom-feed.xml")
        .read_bytes()
        .replace(b"<opensearch:totalResults>3", b"<opensearch:totalResults>2")
    )
    params = {"queries": ["q0", "q1"]}
    page = adapter.parse_page(body, ctx("arxiv", "s", params, {"query_index": 0, "start": 0}))
    assert page.next_cursor == {"query_index": 1, "start": 0}
    last = adapter.parse_page(body, ctx("arxiv", "s", params, {"query_index": 1, "start": 0}))
    assert last.terminal


def test_atom_error_entry_and_result_limit() -> None:
    adapter = ArxivAtomAdapter(CONFIG)
    with pytest.raises(ProtocolError):
        adapter.parse_page(
            (FIX / "arxiv" / "synthetic-atom-error.xml").read_bytes(),
            ctx("arxiv", "s", {"queries": ["q"]}),
        )
    body = (FIX / "arxiv" / "synthetic-atom-feed.xml").read_bytes()
    with pytest.raises(ProtocolError, match="result_limit_exceeded"):
        adapter.parse_page(body, ctx("arxiv", "s", {"queries": ["q"], "max_results_per_query": 2}))


def test_atom_start_index_mismatch_fails() -> None:
    adapter = ArxivAtomAdapter(CONFIG)
    body = (FIX / "arxiv" / "synthetic-atom-feed.xml").read_bytes()
    with pytest.raises(PageStructureError):
        adapter.parse_page(
            body, ctx("arxiv", "s", {"queries": ["q"]}, {"query_index": 0, "start": 200})
        )


def test_atom_query_building() -> None:
    queries = build_queries(
        {
            "categories": ["cs.IT", "quant-ph"],
            "fields": ["ti", "abs"],
            "terms": ["zero knowledge", "SNARK"],
        },
        (None, None),
    )
    assert (
        queries[0]
        == 'cat:cs.IT AND (ti:"zero knowledge" OR abs:"zero knowledge" OR ti:SNARK OR abs:SNARK)'
    )
    assert queries[1].startswith("cat:quant-ph AND ")


def test_sources_config_loads() -> None:
    assert CONFIG.rate_intervals()["arxiv"] == 3.1
    assert CONFIG.stream("arxiv:cs-core:incremental:v1").pairs_with == "arxiv:cs-core:bootstrap:v1"
    a = CONFIG.stream("arxiv:cs-core:bootstrap:v1").config_hash()
    b = CONFIG.stream("arxiv:cs-core:incremental:v1").config_hash()
    assert a != b
