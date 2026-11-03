# Implementation status
Current task: T04
Last completed task: T03
Confirmed decisions: broad scope (proof systems + security proofs + formal verification + protocol analysis); public, no reader accounts; full available history, no year cutoff; daily updates after historical import

Nothing in this repository is a completed production project yet. No backfill has
run, no live provider data has been ingested, and no evaluation has been performed.

| Task | State | Evidence | Remaining |
|---|---|---|---|
| T00 | done | Empty repo inspected (nothing to preserve); spec copied to `docs/spec/`; runtimes recorded in `docs/decisions.md` D002 and `docs/verification-report.md` T00 | — |
| T01 | done | `uv.lock` committed; `proof-index --help`/`doctor --check-db` against PostgreSQL 17.11; 20 unit tests; import makes no network calls (verification report T01) | Make targets for later commands are added with their tasks |
| T02 | done | `models/{dates,source,classification}.py`, `normalize/*`, `config/taxonomy.yaml`; 101 unit tests incl. spec calendar table (verification report T02) | Public model (`models/public.py`) lands with T11 |
| T03 | done | `db/tables.py`, migration `0001`, `db/repository.py`, disposable-DB fixtures; 12 PostgreSQL integration tests (verification report T03) | — |
| T04 | not_started | — | — |
| T05 | not_started | — | Live verification blocked (see External blockers) |
| T06 | not_started | — | — |
| T07 | not_started | — | — |
| T08 | not_started | — | — |
| T09 | not_started | — | — |
| T10 | not_started | — | — |
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
- Database migration head: 0001

## Runtime baseline (tested)
- CPython 3.12.13 via uv 0.11.32
- Node v22.23.4, npm 10.9.9
- PostgreSQL 17.11 (`postgres:17` Docker image); Docker 29.9.2 with Compose v5.6.1

## Next actions
1. T04: BlobStore protocol with local and S3 backends; HTTP client with timeouts, retries, Retry-After, size limits, sanitized logging; provider rate limiter with PostgreSQL advisory locks; writer lock.
2. T04 gate: local put/get/hash tests; MockTransport retry/status tests; no secret leakage.

## External blockers
- **Provider network access (this sandbox).** The environment's egress policy denies `eprint.iacr.org`, `oaipmh.arxiv.org`, `export.arxiv.org`, `dblp.org`, and `api.crossref.org` (proxy CONNECT 403, observed 2026-11-03). Live verification (T05), the real end-to-end sample (T16), and historical import (T17) cannot run here until those hosts are allowed in the environment's Network access settings, or the jobs run elsewhere (e.g. GitHub Actions). Independent work still possible: all parser, runner, database, classification, export, and UI work using synthetic and clearly labeled fixtures.
- **ePrint field semantics.** Spec section 23: ePrint original/update/version fields have never been verified against an accessible live response. Still unverified.
- **Docker daemon.** Not running at session start; started manually (`dockerd`). Needs restarting in a new session.
