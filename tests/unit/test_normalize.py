from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from proof_index.errors import ConfigError
from proof_index.hashing import stable_int64
from proof_index.models.classification import load_taxonomy
from proof_index.models.dates import PartialDate
from proof_index.models.source import Author, NormalizedRecord, SourceLink, Tombstone
from proof_index.normalize.authors import author_key, normalize_orcid
from proof_index.normalize.identifiers import (
    is_valid_source_id,
    normalize_curated_id,
    normalize_dblp_key,
    normalize_doi,
    normalize_eprint_id,
    parse_arxiv_id,
    qualified_identifier,
)
from proof_index.normalize.links import check_url
from proof_index.normalize.text import clean_text, meaningful_title_tokens, title_key

ROOT = Path(__file__).resolve().parents[2]


# --- identifiers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020/0007", "2020/7"),
        ("2020/007", "2020/7"),
        ("https://eprint.iacr.org/2020/007", "2020/7"),
        ("https://eprint.iacr.org/2020/007.pdf", "2020/7"),
        ("oai:eprint.iacr.org:2020/1234", "2020/1234"),
        ("1995/1", None),
        ("2020/0", None),
        ("2020-7", None),
    ],
)
def test_eprint_ids(raw: str, expected: str | None) -> None:
    assert normalize_eprint_id(raw) == expected


def test_modern_arxiv_id_keeps_padding_and_separates_version() -> None:
    parsed = parse_arxiv_id("2101.00001v2")
    assert parsed is not None
    assert (parsed.base, parsed.version) == ("2101.00001", "v2")
    url = parse_arxiv_id("https://arxiv.org/abs/0704.0001v1")
    assert url is not None and (url.base, url.version) == ("0704.0001", "v1")
    oai = parse_arxiv_id("oai:arXiv.org:1501.00001")
    assert oai is not None and oai.base == "1501.00001" and oai.version is None


def test_old_arxiv_identifier_preserves_archive_prefix() -> None:
    parsed = parse_arxiv_id("hep-th/9901001v3")
    assert parsed is not None
    assert (parsed.base, parsed.version) == ("hep-th/9901001", "v3")
    subj = parse_arxiv_id("arXiv:math.GT/0309136")
    assert subj is not None and subj.base == "math.GT/0309136"
    cs = parse_arxiv_id("http://arxiv.org/abs/cs/0101001v1")
    assert cs is not None and cs.base == "cs/0101001"


@pytest.mark.parametrize("bad", ["2113.00001", "1501.0001", "1401.00001", "0601.0001", "foo"])
def test_invalid_arxiv_ids(bad: str) -> None:
    assert parse_arxiv_id(bad) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1145/22145.22178", "10.1145/22145.22178"),
        (" https://doi.org/10.1007/3-540-47721-7_12 ", "10.1007/3-540-47721-7_12"),
        ("doi:10.1137/0218012", "10.1137/0218012"),
        ("https://dx.doi.org/10.1145/ABC", "10.1145/abc"),
        ("11.1145/x", None),
        ("10.1145", None),
    ],
)
def test_doi_normalization(raw: str, expected: str | None) -> None:
    assert normalize_doi(raw) == expected


def test_dblp_and_curated_ids() -> None:
    assert normalize_dblp_key("https://dblp.org/rec/conf/crypto/Foo20.html") == "conf/crypto/Foo20"
    assert normalize_dblp_key("journals/jacm/GoldwasserMR89") == "journals/jacm/GoldwasserMR89"
    assert normalize_dblp_key("not a key") is None
    assert normalize_curated_id("gmr-1985") == "curated:gmr-1985"
    assert normalize_curated_id("curated:Bad Slug") is None


def test_source_id_validation_and_qualification() -> None:
    assert is_valid_source_id("eprint", "2020/7")
    assert not is_valid_source_id("eprint", "2020/0007")
    assert not is_valid_source_id("arxiv", "2101.00001v2")
    assert qualified_identifier("crossref", "10.1/x") == "doi:10.1/x"
    assert qualified_identifier("arxiv", "hep-th/9901001") == "arxiv:hep-th/9901001"


# --- links -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "issue"),
    [
        ("javascript:alert(1)", "url_scheme_not_allowed"),
        ("data:text/html,<script>", "url_invalid_characters"),
        ("ftp://example.org/x", "url_scheme_not_allowed"),
        ("https://user:pass@example.org/", "url_embedded_credentials"),
        ("https://user@example.org/", "url_embedded_credentials"),
        ("https:///nohost", "url_missing_host"),
        ("https://exa mple.org", "url_invalid_characters"),
        ("", "url_missing"),
        ("//example.org/x", "url_scheme_not_allowed"),
    ],
)
def test_unsafe_links_rejected(raw: str, issue: str) -> None:
    result = check_url(raw)
    assert result.url is None
    assert result.issue == issue


def test_safe_link_normalizes_scheme_and_host() -> None:
    assert check_url("HTTPS://Eprint.IACR.org/2020/7").url == "https://eprint.iacr.org/2020/7"


def test_source_link_model_rejects_unsafe_url() -> None:
    with pytest.raises(ValidationError):
        SourceLink(kind="landing", url="javascript:alert(1)", origin="source_metadata")


# --- text and authors ------------------------------------------------------------


def test_clean_text_unicode_dashes_and_whitespace() -> None:
    assert clean_text("  Zero–knowledge\n\tproofs­ ") == "Zero-knowledge proofs"
    assert clean_text("ﬁne") == "fine"  # NFKC ligature


def test_title_key_keeps_math_distinctions() -> None:
    assert title_key("SNARKs for C: Verifying Program Executions") == title_key(
        "snarks for c - verifying program executions"
    )
    assert title_key("Bounds for n+1 provers") != title_key("Bounds for n-1 provers")
    assert meaningful_title_tokens("The Knowledge of the Proof") == ["knowledge", "proof"]


def test_orcid_checksum() -> None:
    assert normalize_orcid("https://orcid.org/0000-0002-1825-0097") == "0000-0002-1825-0097"
    assert normalize_orcid("0000-0002-1825-0098") is None
    assert normalize_orcid("000000021694233X") == "0000-0002-1694-233X"


def test_author_display_string_is_not_split() -> None:
    author = Author(display_name="Goldwasser, Shafi")
    assert author.given is None and author.family is None
    assert author_key(" Shafi  GOLDWASSER ") == "shafi goldwasser"


# --- records -----------------------------------------------------------------------


def make_record(**overrides: object) -> NormalizedRecord:
    data: dict[str, object] = {
        "source": "eprint",
        "source_id": "2020/7",
        "title": "Zero-knowledge proofs for private arithmetic computations",
        "authors": [Author(display_name="Alice A"), Author(display_name="Bob B")],
        "abstract": "We construct a zero-knowledge proof system.",
        "published": PartialDate.of_year(2020),
        "source_modified_at": None,
        "harvest_datestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "source_version": None,
        "doi": None,
        "identifiers": {},
        "source_categories": [],
        "venue_key": None,
        "venue_name": None,
        "links": [],
        "status": "active",
        "evidence": {"title": "metadata/oai_dc:dc/dc:title"},
    }
    data.update(overrides)
    return NormalizedRecord(**data)  # type: ignore[arg-type]


def test_missing_title_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        make_record(title="   ")


def test_absent_authors_and_abstract_are_allowed() -> None:
    record = make_record(authors=[], abstract="   ")
    assert record.authors == []
    assert record.abstract is None


def test_unnormalized_source_id_rejected() -> None:
    with pytest.raises(ValidationError):
        make_record(source_id="2020/0007")


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        make_record(unexpected="x")


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError):
        make_record(harvest_datestamp=datetime(2026, 1, 1))  # noqa: DTZ001


def test_hash_ignores_whitespace_evidence_and_harvest_stamp() -> None:
    base = make_record()
    variant = make_record(
        title="  Zero-knowledge   proofs for private\narithmetic computations ",
        evidence={"title": "different/path"},
        harvest_datestamp=datetime(2026, 2, 2, tzinfo=UTC),
    )
    assert base.content_sha256() == variant.content_sha256()


def test_hash_is_order_insensitive_for_sets_but_not_authors() -> None:
    a = make_record(source_categories=["cs.CR", "cs.CC"], identifiers={"x": ["2", "1"]})
    b = make_record(source_categories=["cs.CC", "cs.CR", "cs.CR"], identifiers={"x": ["1", "2"]})
    assert a.content_sha256() == b.content_sha256()
    swapped = make_record(authors=[Author(display_name="Bob B"), Author(display_name="Alice A")])
    assert swapped.content_sha256() != a.content_sha256()


def test_hash_changes_with_version_status_and_dates() -> None:
    base = make_record().content_sha256()
    assert make_record(source_version="v2").content_sha256() != base
    assert make_record(status="withdrawn").content_sha256() != base
    assert make_record(published=PartialDate.of_year(2021)).content_sha256() != base
    assert make_record(source_modified_at=datetime(2026, 3, 1, tzinfo=UTC)).content_sha256() != base


def test_tombstone_has_no_bibliographic_fields() -> None:
    tomb = Tombstone(source="arxiv", source_id="2101.00001", harvest_datestamp=None)
    assert "title" not in tomb.model_dump()


def test_stable_int64_is_deterministic() -> None:
    assert stable_int64("provider:arxiv") == stable_int64("provider:arxiv")
    assert -(2**63) <= stable_int64("x") < 2**63


# --- taxonomy -----------------------------------------------------------------------


def test_taxonomy_loads_with_spec_ids() -> None:
    taxonomy = load_taxonomy(ROOT / "config/taxonomy.yaml")
    assert len(taxonomy.topics) == 20
    assert "zero-knowledge" in taxonomy.topic_ids
    assert taxonomy.topic("quantum-security").areas == ["security-proofs", "proof-systems"]
    assert taxonomy.validate_labels(["proof-systems"], ["zero-knowledge"], ["application"]) == []
    assert taxonomy.validate_labels(["crypto"], ["zk"], ["paper"]) == [
        "unknown area 'crypto'",
        "unknown topic 'zk'",
        "unknown contribution 'paper'",
    ]


def test_taxonomy_rejects_unknown_area(tmp_path: Path) -> None:
    bad = (
        (ROOT / "config/taxonomy.yaml")
        .read_text()
        .replace("areas: [proof-systems]\n", "areas: [proofs]\n", 1)
    )
    path = tmp_path / "taxonomy.yaml"
    path.write_text(bad)
    with pytest.raises(ConfigError):
        load_taxonomy(path)
