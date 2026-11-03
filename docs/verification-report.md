# Verification report

Exact commands run and concise results, grouped by task. Newest task last.
Results here are observations from the stated environment on the stated date; they
are not claims about other environments.

## T00 — Workspace and dependency baseline (2026-11-03, cloud sandbox, Ubuntu 24.04.5)

Workspace inspection:

```text
$ git status
On branch claude/fervent-bell-od7lan
No commits yet
$ ls -la            # only .git/ present
```

Result: empty repository; no existing user or planning files to preserve.

Runtimes:

```text
$ uv --version                     -> uv 0.11.32
$ uv python install 3.12.13        -> Installed Python 3.12.13
$ /usr/bin/python3.12 --version    -> Python 3.12.3 (system, not used for the lock)
$ node --version                   -> v22.23.4
$ npm --version                    -> 10.9.9
$ docker --version                 -> Docker version 29.9.2 (Compose plugin v5.6.1)
$ docker info                      -> daemon not running initially; started `dockerd`, then Server Version 29.9.2
$ docker pull postgres:17          -> pulled
$ docker run --rm postgres:17 postgres --version -> postgres (PostgreSQL) 17.11
$ psql --version                   -> psql (PostgreSQL) 16.15 (host client only)
```

Network reachability (single bounded request per host, no retries):

```text
https://pypi.org/simple/pydantic/                              -> 200
https://registry.npmjs.org/astro                               -> 200
https://eprint.iacr.org/oai?verb=Identify                      -> proxy CONNECT 403 (environment policy)
https://oaipmh.arxiv.org/oai?verb=Identify                     -> proxy CONNECT 403 (environment policy)
https://export.arxiv.org/api/query?search_query=cat:cs.CR&max_results=1 -> proxy CONNECT 403
https://dblp.org/xml/release/                                  -> proxy CONNECT 403
https://api.crossref.org/works?rows=1                          -> proxy CONNECT 403
```

Interpretation: the 403s come from this sandbox's egress policy, not from the
providers. They say nothing about whether the providers would accept our requests.
No live provider response has been observed in this environment.

## T01 — Packaging and local infrastructure (2026-11-03)

```text
$ uv lock                      -> Resolved 48 packages (exclude-newer 2026-10-20T00:00:00Z)
$ uv sync --locked             -> ok; pydantic 2.14.1, sqlalchemy 2.0.55, psycopg 3.3.6,
                                  alembic 1.20.0, httpx 0.28.1, lxml 6.1.3, typer 0.27.3,
                                  boto3 1.43.115, pytest 9.1.1, ruff 0.17.1, mypy 2.4.0
$ uv run pytest -q             -> 20 passed
$ uv run ruff check / format --check / mypy (strict) -> clean
$ make db-up                   -> Container proofs-hub-postgres-1 Healthy
$ uv run proof-index doctor --check-db
    database_url postgresql+psycopg://proofs:***@127.0.0.1:5432/proofs
    check database: ok (17.11 (Debian 17.11-1.pgdg13+2))   exit 0
$ uv run proof-index --help    -> lists `doctor`
```

`test_package_import_makes_no_network_calls` imports the package in a fresh
interpreter with socket creation disabled. `test_doctor_database_failure_exit_code_and_no_password`
confirms exit code 6 and that the password never appears in output.

## T02 — Shared schemas and pure normalization (2026-11-03)

```text
$ uv run pytest -q             -> 101 passed
$ uv run ruff check; uv run mypy (strict, 16 files) -> clean
```

Covered: the full section 12 calendar table at 2026-11-03 (`test_spec_calendar_table`),
`test_recent_window_uses_zero_through_six_days`, `test_future_date_does_not_enter_recent_results`,
`test_year_only_date_is_not_fabricated_as_january_first`,
`test_partial_date_range_uses_documented_overlap_semantics`,
`test_old_arxiv_identifier_preserves_archive_prefix`, ePrint `2020/0007 -> 2020/7`, DOI
prefix stripping, unsafe links (`javascript:`, embedded credentials, missing host),
empty-title rejection, absent authors/abstract allowed, whitespace-invariant content
hash, set-vs-ordered hash fields, and taxonomy closed-vocabulary validation.

## T03 — Database migrations and repository (2026-11-03)

```text
$ make migrate                 -> alembic upgrade head (revision 0001) on local PostgreSQL 17.11
$ make test-db                 -> 113 passed (12 PostgreSQL integration tests + unit tests)
$ uv run ruff check; uv run mypy -> clean
```

Integration tests create a migrated template database `proofs_test_tmpl_*` and a
fresh `proofs_test_*` copy per test; they never use `DATABASE_URL`. Covered:
migrate empty database -> downgrade base -> upgrade again; metadata/migration drift
check (it caught a `use_alter` FK that `op.create_table` silently skipped, now fixed);
FK enforcement; UNIQUE(source, source_id); composite FK rejecting a current
observation that belongs to another record; CHECK constraints (visibility,
self-merge, terminal page with cursor, paused-without-phase); sorted/distinct
separation pairs; immutable curation rows (UPDATE and DELETE rejected by trigger);
no cascading delete; full rollback of a failing transaction.

## T04 — Blob storage, HTTP, and locks (2026-11-03)

```text
$ make test-db                 -> 146 passed
$ uv run ruff check; uv run mypy (29 files) -> clean
```

Local BlobStore: put/get/exists, idempotent identical put, hash-mismatch and
overwrite rejection, path-traversal keys rejected. S3BlobStore: botocore `Stubber`
verifies the conditional `IfNoneMatch="*"` put with SHA-256 metadata and the
different-content error (no network). HTTP (httpx `MockTransport`, fake clock):
503 retry with 2 s/4 s backoff, `Retry-After` seconds honored, `Retry-After`
beyond the job budget raises `BudgetExhausted` without sleeping, 400/401/403/404
not retried, 4-attempt cap, connect-error retries then `TransportFailure`, size
limit, sanitized logs (no `mailto` value or URL password). PostgreSQL locks: a
second holder gets exit code 5, release on exception, a terminated session
releases its lock, and holding a lock leaves no `idle in transaction` session.

## T05 — Source contracts and parsers (2026-11-03)

```text
$ uv run pytest -q             -> 152 passed, 17 skipped (DB tests; run via make test-db)
$ uv run ruff check; uv run mypy (40 files) -> clean
$ uv run proof-index sources verify --source eprint --bounded
    identify / list-metadata-formats / page-eprint_all_incremental_v1:
    FAILED (TransportFailure: proxy refused the connection ... (403 Forbidden); not retried)
    report: work/verification/verify-eprint-20261103T191741Z.json   exit 3
$ uv run proof-index sources verify --source arxiv --bounded
    all 5 steps refused by the sandbox proxy (403); exit 3
```

No live provider response was obtained; all parser evidence is from synthetic
fixtures (`tests/fixtures/README.md`). Parser tests cover:
`test_oai_no_records_match_is_successful_empty_window`, `badResumptionToken` →
`ExpiredCursor`, `badArgument`/`cannotDisseminateFormat`/unknown codes fail,
HTML login page, truncated XML, DOCTYPE entity, wrong root, bare XML declaration,
namespace-URI reading with non-standard prefixes, empty-title quarantine with the
rest of the page kept, deleted header → tombstone, `javascript:` link dropped,
arXiv local category filter, `test_arxiv_atom_and_oai_share_source_identity`, old
arXiv IDs (`cs/0101001`), explicit ePrint references, Atom error entries, Atom
result-ceiling failure, startIndex mismatch. `test_verify.py` exercises the
verification success path through a mock transport and found a real set-spec
matching bug (`cs:cs:CR` uses colons), now fixed.

## T06 — Resumable ingestion runner (2026-11-03)

```text
$ make test-db                 -> 188 passed (19 runner tests on PostgreSQL 17)
$ uv run ruff check; uv run mypy (47 files) -> clean
$ make migrate                 -> head 0002
$ uv run proof-index sync --source eprint
    {"complete": false, "streams": [{"state": "paused", "error_code": "transport_failure",
     "exit_code": 3, "pages_fetched": 0, ...}]}   exit 3   (sandbox proxy refused the host)
```

Runner tests (real transactions, injected crashes): full bootstrap run with
watermark/coverage/incremental-boundary capture; `test_oai_no_records_match_is_successful_empty_window`;
`test_oai_protocol_error_does_not_advance_watermark`; HTML-with-200 fails;
`test_repeated_resumption_token_fails_completeness_check`; count mismatch fails;
`test_expired_token_replays_window_without_duplicate_works`;
`test_partial_page_write_resumes_from_last_durable_cursor` (resumes at the token, not
the start); `test_partial_promotion_blocks_export_until_repaired`; budget pause exits
0 with `complete:false` and resumes; 503 pauses with exit 3; lock held → exit 5;
changed stream config refuses reuse; incremental `from` = watermark − 2 days;
`test_rerun_of_identical_fixture_does_not_create_new_observations`;
`test_older_backfill_cannot_replace_newer_current_observation`; equal ordering +
different content → `conflict`; `test_undated_A_B_A_reversion_is_retained_once`;
newer version replaces current.

## T07 — Classification (2026-11-03)

```text
$ make test-db                 -> 220 passed
$ uv run ruff check; uv run mypy (51 files) -> clean
$ uv run proof-index classify --changed       -> {"examined": 0, ...}  (local catalog is empty)
$ uv run proof-index classify --all --rules-version rules-v9
    error[invalid_input]: --rules-version rules-v9 does not match the configured rules file (rules-v1)
```

Synthetic case results (`tests/fixtures/synthetic/cases.json`, rules-v1):
F01/F02/F03/F10/F11/F12/F14 include proof-systems; F04 include security-proofs;
F05 include formal-verification; F13/F16 include proof-systems + security-proofs;
F06, F07, F15 exclude; F08 review. These are synthetic test cases, not an
evaluation of real-record precision or recall (T18).
Behavior tests: `test_security_proof_and_formal_crypto_examples_are_in_scope`,
`test_missing_abstract_is_not_automatic_exclusion`, distinct-span independence,
ambiguous tokens alone do not force review, contradictory contexts suppress only
ambiguous names, formal tools need crypto context, named-cryptanalysis allowlist,
uppercase-only STARK, contributions (including "no fabricated type"), normalized
offsets, override precedence, taxonomy validation of rule topics. PostgreSQL tests:
promotion hook writes classification rows (tombstones skipped); `--changed` fills
only missing rows; `--all` recomputation is deterministic.

## T08 — Identity and canonical work projection (2026-11-03)

```text
$ make test-db                 -> 233 passed (13 identity tests on PostgreSQL 17)
$ uv run ruff check; uv run mypy (56 files) -> clean; migration head 0003
```

Covered on synthetic cases: F01+F02 merge through the explicit ePrint reference
(both arrival orders); F10 stays separate; identical rerun preserves work IDs and
`canonical_revision`; `test_title_author_match_requires_unique_candidate_and_no_conflicts`
(same title+authors attaches; different authors → separate work + review; two
candidate works → no attach); conflicting DOI blocks title+author attach; short
titles (<5 meaningful tokens) need identifiers;
`test_shared_proceedings_doi_does_not_merge_unrelated_papers`; excluded F06 has no
work; review F08 gets a `review` work; F13+F16 withdrawn counterpart keeps the work
active with per-source withdrawal; revision increments only on public change;
missing abstract and unknown date retained.

## T09 — Events and curation (2026-11-03)

```text
$ make test-db                 -> 252 passed (19 event/curation tests on PostgreSQL 17)
$ uv run ruff check; uv run mypy (59 files) -> clean; migration head 0004
$ uv run proof-index review export --out work/review.jsonl
    {"identity_reviews": 0, "relevance_reviews": 0}   (local catalog is empty)
```

Events: F03 after a baseline F02 yields exactly one `source_revision` dated
2020-03-01 (source basis) and keeps the publication date; rerun creates nothing;
`test_source_added_does_not_reset_original_publication` (and the publication date is
corrected to the earliest supported date); bootstrap first observation creates only
the publication event; metadata update after a baseline is `metadata_updated` with
observed basis; no observed event during bootstrap; whitespace-only change creates
no event; withdrawal and restoration events; event keys unique and stable.
Curation: include is idempotent and command-ID reuse with different content
conflicts; `test_stale_curation_revision_rejects_write`; set_fields wins with
curation provenance; unknown label IDs rejected; merge moves sources and records
overrides; `test_merge_aliases_are_acyclic_and_resolve_to_live_work`;
`test_manual_split_survives_reclassification_and_resolution` (and a later merge is
blocked by the separation constraint); split requires complete assignments;
revert of include restores the automatic decision and double revert is refused;
`test_revert_preserves_later_independent_source_observations`; relate and
non-feed curation events.

## T10 — Historical adapters and enrichment (2026-11-03)

```text
$ make test-db                 -> 263 passed (11 historical tests on PostgreSQL 17)
$ uv run ruff check; uv run mypy (63 files) -> clean; migration head 0005
$ uv run proof-index backfill --source dblp
    error[invalid_input]: DBLP backfill needs --dump (download the current release ...)
```

Synthetic DBLP dump (`tests/fixtures/dblp/`, with a synthetic stand-in DTD):
candidate filtering (venue or title signal), filtered/quarantined counts, unknown
total preserved as null, editors excluded and numeric disambiguators removed,
cross-ID merge with the ePrint work, `test_dblp_dump_restart_resumes_from_durable_ordinal`,
changed dump starts a new snapshot and supersedes the unfinished one. Crossref
(synthetic JSON through a mock transport): attach to the DOI's work, 404 and
proceedings DOIs quarantined, earliest-manifestation date (not created/deposited/
indexed), conflicting metadata → review without overwrite, paused run resumes only
its stored DOI list. Curated: included + foundational + agent-provisional labels,
idempotent re-import, corrected title keeps the stable slug and work ID, schema
example and invalid lines rejected before staging, identifiers join an existing work.
No real DBLP dump, Crossref response, or verified foundational entry was used.

## T11 — Release export and schema contract (2026-11-03)

```text
$ make test-db                 -> 278 passed (12 release tests + 3 BibTeX unit tests)
$ uv run ruff check; uv run mypy (72 files) -> clean
$ make fixture-export          -> work/releases/fixture-preview, 9 papers, validated
$ uv run python scripts/validate-release.py work/releases/fixture-preview --require-live
    error[data_validation_failure]: ... live deployment guard: release is a fixture dataset
$ make schema                  -> apps/web/src/lib/schema/{public-paper,paper-card,list-page,manifest}.schema.json
```

The fixture release is built by pushing the synthetic cases through the real
promotion/classification/identity/event/curation code in a disposable
`proofs_fixture_*` database, then exported with `dataset_mode=fixture`.
Tests: counts and manifest consistency; `test_fixture_release_is_rejected_by_live_deploy_guard`;
list semantics (F13 New, F14 archived not New, F12 in `archive/unknown`, F09 in
Foundations, F01–F03 once, empty topic lists valid); unique ranks with unknown dates
last both ways; withdrawn source with active counterpart; `test_untrusted_title_and_link_are_safe`;
`test_public_export_excludes_internal_secrets_and_error_traces`; checksum tampering
and forbidden keys detected; `test_new_and_updated_feeds_have_stable_unique_ids`;
no overwrite of an existing release; `test_partial_promotion_blocks_export`; non-root
base path in canonical/JSON/feed URLs; `test_bibtex_escapes_special_characters_and_preserves_author_order`.

## T12 — Static reading UI (2026-11-03)

```text
$ cd apps/web && npm ci                       -> 0 vulnerabilities (npm audit)
$ CATALOG_DIR=work/releases/fixture-preview npm run check   -> astro check: 0 errors; tsc clean
$ CATALOG_DIR=... npm test                     -> 31 passed (card model, dates, URLs, contract keys
                                                  vs exported schemas, runtime validation of every
                                                  fixture paper, WCAG AA token contrast light+dark)
$ CATALOG_DIR=... npm run build                -> 47 page(s) built
$ PUBLIC_BASE_PATH=/proof-papers/ (release exported for /proof-papers/) npm run build -> 47 pages
$ build with a release exported for another base path -> prepare-catalog refuses
```

Browser check (Playwright Chromium against `astro preview` of the real build):
crawled 43 internal URLs at `/` and 43 at `/proof-papers/`: 0 broken links, 0 pages
wider than the viewport at 1280 px and 390 px, 0 console/page errors; no built HTML
link escapes the base path. Screenshots of the home page (desktop and mobile) and a
detail page were reviewed: preview label, freshness and coverage notes, area tabs,
view tabs, card badges, withdrawn-source note, sources table, inclusion evidence,
and history render as specified. Keyboard-only and screen-reader checks are not yet
done (T19).

## T13 — Search index and controller (2026-11-03)

```text
$ cd apps/web && npm test                     -> 47 passed (adds URL state, filter translation,
                                                 identifier parsing, hydration, release mismatch,
                                                 late-response guard, DOM renderer safety)
$ npm run check                               -> 0 errors
$ make test-db                                -> 278 passed
$ make web-browser-check                      -> fixture-search (49 papers, 40 generated synthetic
                                                 fillers); 90 pages; build-search indexed 49
                                                 canonical paper pages; 22/22 browser checks passed
```

The browser checks run real Pagefind searches in Chromium against `astro preview` of
the production build (clock pinned to 2026-11-03): multi-page results and Next/Back
with URL state (`test_url_state_round_trip_and_browser_back`); source OR returning the
two-source work once; area OR returning the two-area work once; cross-facet AND;
area tab reflects URL; explicit unknown-date selection; day-precision-only rolling
window; `test_unknown_dates_sort_last` for newest and oldest; exact identifier search
and unknown identifier; reversed range rejected inline; constrained empty date set
returns zero; static vs client-rendered card parity on the same work;
`test_release_mismatch_requests_reload_not_silent_merge` (intercepted payload);
index-failure message; no overflow at 390 px; no JavaScript errors.
Unit tests cover `test_facet_OR_with_cross_facet_AND`,
`test_constrained_empty_date_token_set_returns_zero`,
`test_partial_date_range_uses_documented_overlap_semantics`,
`test_search_hydrates_only_requested_page` (25 of 60 hydrated, cache reused), and
`test_late_search_response_cannot_replace_current_results`.
