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
