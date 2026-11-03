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
