# Implementation status
Current task: T11
Last completed task: T10
Confirmed decisions: broad scope (proof systems + security proofs + formal verification + protocol analysis); public, no reader accounts; full available history, no year cutoff; daily updates after historical import

Nothing in this repository is a completed production project yet. No backfill has
run, no live provider data has been ingested, and no evaluation has been performed.

| Task | State | Evidence | Remaining |
|---|---|---|---|
| T00 | done | Empty repo inspected (nothing to preserve); spec copied to `docs/spec/`; runtimes recorded in `docs/decisions.md` D002 and `docs/verification-report.md` T00 | — |
| T01 | done | `uv.lock` committed; `proof-index --help`/`doctor --check-db` against PostgreSQL 17.11; 20 unit tests; import makes no network calls (verification report T01) | Make targets for later commands are added with their tasks |
| T02 | done | `models/{dates,source,classification}.py`, `normalize/*`, `config/taxonomy.yaml`; 101 unit tests incl. spec calendar table (verification report T02) | Public model (`models/public.py`) lands with T11 |
| T03 | done | `db/tables.py`, migration `0001`, `db/repository.py`, disposable-DB fixtures; 12 PostgreSQL integration tests (verification report T03) | — |
| T04 | done | `storage/{base,local,s3,factory}.py`, `http/{client,rate_limit,errors}.py`, `db/locks.py`; 146 tests (verification report T04) | S3 backend tested only with botocore Stubber; no real bucket |
| T05 | partial | Adapters `eprint`, `arxiv_oai`, `arxiv_atom`; OAI protocol layer; `config/sources.yaml`; `sources verify --bounded` (ran: proxy 403, reports written); `docs/source-contracts.md`; 21 parser tests + verify test on synthetic fixtures | **Live verification blocked**: all mappings are assumptions; ePrint date/version semantics and arXiv set list unresolved |
| T06 | done | `ingest/{runner,staging,promote,reconcile,hooks,service}.py`, `sync`/`backfill` CLI, migration 0002; 19 PostgreSQL recovery tests (verification report T06) | Classification/identity/event hooks are wired in T07–T09 |
| T07 | done | `config/relevance-rules.yaml` (rules-v1), `classify/{rules,engine,store}.py`, promotion hook, `classify --changed/--all`; synthetic F-cases + 32 tests (verification report T07) | Rules untuned on real records (T18); venue registry arrives with T10 |
| T08 | done | `resolve/{candidates,identity,canonical}.py`, `models/canonical.py`, migration 0003, identity hook in the promotion chain; 13 PostgreSQL tests (verification report T08) | Curation overrides plug in with T09 |
| T09 | done | `resolve/{events,curation,review}.py`, `curate validate/apply`, `review export`, migration 0004; 19 PostgreSQL tests (verification report T09) | — |
| T10 | done | `adapters/{dblp,crossref,curated}.py`, `config/venues.yaml`, `ingest/historical.py`, `run_snapshot`, `backfill --source dblp`, `import-curated`, `enrich-dois`, migration 0005; 11 PostgreSQL tests on synthetic inputs (verification report T10) | No real dump/API response processed (host blocked); `foundations.jsonl` intentionally empty until entries are verified (T17) |
| T11 | not_started | — | — |
| T12 | not_started | — | — |
| T13 | not_started | — | — |
| T14 | not_started | — | — |
| T15 | not_started | — | — |
| T16 | not_started | — | Needs provider network access |
| T17 | not_started | — | Needs provider network access and durable compute |
| T18 | not_started | — | Needs real records; independent human labels absent |
| T19 | not_started | — | — |
| T20 | not_started | — | Needs deployment authorization |

## Current interfaces
- Public schema version: 1 (not yet implemented)
- Rules version: rules-v1 (not yet implemented)
- Database migration head: 0005

## Runtime baseline (tested)
- CPython 3.12.13 via uv 0.11.32
- Node v22.23.4, npm 10.9.9
- PostgreSQL 17.11 (`postgres:17` Docker image); Docker 29.9.2 with Compose v5.6.1

## Next actions
1. T11: public paper model + JSON Schema export; release export from a REPEATABLE READ snapshot behind the pending-promotion guard; lists (new/updated/archive/foundations/topics), feeds, BibTeX, coverage/status, search facets, aliases, checksums, manifest; `release export/validate`; fixture export mode.
2. T11 gate: valid fixture release; no internal secret fields; IDs/counts/hashes consistent.
3. When provider access exists: `sources verify --source eprint --bounded --capture-fixtures tests/fixtures/eprint` then reconcile `docs/source-contracts.md`.

## External blockers
- **Provider network access (this sandbox).** The environment's egress policy denies `eprint.iacr.org`, `oaipmh.arxiv.org`, `export.arxiv.org`, `dblp.org`, and `api.crossref.org` (proxy CONNECT 403, observed 2026-11-03). Live verification (T05), the real end-to-end sample (T16), and historical import (T17) cannot run here until those hosts are allowed in the environment's Network access settings, or the jobs run elsewhere (e.g. GitHub Actions). Independent work still possible: all parser, runner, database, classification, export, and UI work using synthetic and clearly labeled fixtures.
- **ePrint field semantics.** Spec section 23: ePrint original/update/version fields have never been verified against an accessible live response. Still unverified.
- **DBLP dump / Crossref API.** Same egress block (`dblp.org`, `api.crossref.org`); DBLP import needs a downloaded dump + DTD passed as files.
- **Docker daemon.** Not running at session start; started manually (`dockerd`). Needs restarting in a new session.
