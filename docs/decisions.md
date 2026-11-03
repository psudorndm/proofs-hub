# Engineering decisions

This file records deviations from, and clarifications of, the fixed decisions in
`docs/spec/proof-papers-sol-implementation-spec.md` (section 2). Each entry gives
the evidence behind it.

## D001 — Repository root is the project root (T00, 2026-11-03)

Spec section 3 shows a `proof-papers/` directory. The workspace repository
(`psudorndm/proofs-hub`) was empty when work started: no commits, no planning
documents, no user files. Because the repository is dedicated to this project
and GitHub Actions only reads workflows from the repository root's
`.github/workflows/`, the `proof-papers/` tree from section 3 is laid out at the
repository root. Paths in the spec such as `proof-papers/pipeline/...` map to
`pipeline/...` here.

The specification itself is preserved unchanged at
`docs/spec/proof-papers-sol-implementation-spec.md`
(SHA-256 `6c8c4536c09c9649c14e22315a301ce921f9569f0cc8d6d6cf11e47997b5ce05`).

## D002 — Runtime baselines selected (T00, 2026-11-03)

| Concern | Spec baseline | Selected for this build | Evidence |
|---|---|---|---|
| Python | 3.12 | CPython 3.12.13 (uv-managed) | `uv python install 3.12.13` succeeded; system also has 3.12.3 and 3.13.18 |
| Python packaging | uv | uv 0.11.32 | `uv --version` |
| Node | 22 | Node v22.23.4 | `node --version` |
| npm | npm + lockfile | npm 10.9.9 | `npm --version` |
| PostgreSQL | 17 | `postgres:17` Docker image, server 17.11 | `docker run --rm postgres:17 postgres --version` |

Python 3.12 is pinned with `requires-python = ">=3.12,<3.13"` so that the lock
resolves for the baseline. The host's PostgreSQL 16 client (`psql` 16.15) is only
a convenience client; all server-side tests run against the PostgreSQL 17
container. `pg_dump` used by `backup` must match the server major version, so
backup/restore runs `pg_dump`/`pg_restore` from the PostgreSQL 17 container image
(decided in T15 if this changes).

## D003 — Docker daemon must be started manually in this sandbox (T00)

The Docker CLI and Compose plugin are installed, but the daemon was not running.
`dockerd` was started in the background for this session. This is a property of
the sandbox, not of the project; developer machines and GitHub Actions run their
own daemon/service containers.

## D004 — Content hash excludes the OAI harvest datestamp (T02)

Spec 5.4 includes "source-supplied dates" in the semantic hash but excludes
bookkeeping. `harvest_datestamp` (the OAI header datestamp) changes whenever a
provider re-exports a record, including for bookkeeping-only reasons, so it is
excluded from `content_sha256` along with `evidence`. It still participates in
the observation key and current-observation ordering (section 8).
`source_modified_at`, `source_version`, `published`, and `status` are included.
Links are hashed as a set sorted by (kind, url); authors keep their order.

## D005 — DOIs are stored lower-cased (T02)

DOIs are case-insensitive. `normalize_doi` strips URL/`doi:` prefixes and
whitespace and lower-cases the result; the original provider string remains in
the stored raw response.

## D006 — Additive database columns and one additive table (T03)

Section 6 lists minimum columns; these additions support behavior the spec
requires elsewhere and change no listed meaning:

- `provider_streams.checkpoint jsonb`, `created_at` — stream checkpoint data
  (e.g. the incremental boundary captured before a bootstrap, section 8).
- `harvest_runs.config_hash`, `paused_phase`, `snapshot_key`, `promotion_started_at`,
  `reconciled_at`, `records_quarantined`, `reported_total` — resumable phase, DBLP
  dump fingerprint (section 7), and the durable *pending promotion* marker that
  blocks export after a crash (section 8): a run with `promotion_started_at` set,
  not `succeeded`, and not `reconciled_at` blocks release export.
- `harvest_pages.quarantined_count`, `reported_total`, `fetched_at`; CHECK
  `is_terminal = (next_cursor IS NULL)` enforces "terminal agrees with cursor absence".
- `staged_records.item_ordinal` — stable promotion order within a page.
- `source_observations.item_kind`, `parser_version`, `previous_observation_id`.
- `curation_commands.command_id` (UNIQUE) — the YAML command id; plus an
  `import_curated` kind for the curated foundational import. An UPDATE/DELETE
  trigger makes rows immutable.
- `work_events.curation_command_id`, `source_record_id`, `details`; `date_basis`
  allows `none` (exactly when `event_date` is NULL).
- `publications.source_record_id`, `releases.release_key`, `classifications.created_at`,
  `coverage_slices.updated_at` (coverage `run_id` is nullable for `not_started` slices).
- New table `quarantine_items` for item-level parse failures that have no valid
  source identity (which `staged_records.source_id NOT NULL` cannot hold).

Nullable JSONB columns use `none_as_null=True` so Python `None` is SQL NULL, not
JSON `null`. A test (`test_migration_matches_table_metadata`) fails if the Alembic
migration and the Core metadata drift apart.

## D007 — Provider lock is a job-scoped session lock; spacing is in-process (T04)

The provider advisory lock (`provider:<name>`) is held for a whole sync/backfill
invocation on its own autocommit connection, so only one process talks to a
provider at a time and no SQL transaction stays open during sleeps. Inside that
process, `LimiterRegistry` spaces requests per provider (both arXiv adapters share
`arxiv`). S3 creation uses a conditional `IfNoneMatch="*"` write and stores the
SHA-256 as object metadata. The HTTP client redacts `mailto`, token, key,
password, and signature query values in logs and in the stored sanitized URL.

## D008 — Adapters store only provider-supplied links; identifier URLs are derived later (T05)

Adapters record links that appear in provider metadata, with
`origin="source_metadata"`. Landing URLs that follow from an identifier alone
(`https://arxiv.org/abs/<id>`, `https://eprint.iacr.org/<id>`, `https://doi.org/<doi>`)
are derived by the public projection (T11) and labeled as derived, rather than
stored as if a provider had supplied them. No PDF URL is constructed from an ID.

## D009 — Proxy refusals are not retried (T05)

`httpx.ProxyError` (e.g. an egress policy answering CONNECT with 403) fails
immediately as `TransportFailure`, like a provider 403. Retrying a policy denial
only adds load and delay.

## D010 — Runner recovery choices (T06)

- **Network failures pause, protocol failures fail.** After the HTTP client's
  retries, `TransportFailure` and retryable statuses (429/5xx) put the run in
  `paused` (phase `fetching`) with the error recorded and exit code 3, so durable
  pages are kept and the next invocation resumes. Protocol errors, page-structure
  errors, non-retryable 4xx, cursor loops, and count mismatches mark the run
  `failed`. The spec's state machine lists `paused` for budget stops; using it for
  transient network loss is a deliberate extension.
- **Expired cursor replay** increments `harvest_runs.cursor_epoch` and restarts the
  run's fixed window; staging is idempotent (UNIQUE run/source/id/hash), and
  completeness checks use only the final epoch. At most 3 replays.
- **Totals** are validated per partition (all OAI pages, or one Atom query): all
  reported totals in a partition must agree and equal parsed + quarantined +
  locally filtered items. `strict_totals` can be disabled per runner if a live
  provider proves to report estimates; that change must be recorded here.
- **Watermark** on success is the first page's provider `responseDate` (falling
  back to run start), never later than the window's start of observation, and is
  never moved backwards. A bootstrap run first fixes its paired incremental
  stream's starting boundary if unset, and never rewinds an existing one.
- **Current selection across representations.** Ordering compares modification
  time, then version number, then harvest datestamp, whichever both sides have.
  With nothing comparable, the same adapter falls back to fetch order; different
  adapters (arXiv Atom vs OAI) are treated as *equal*, so a differing hash becomes
  a `conflict` observation instead of flip-flopping the current record.
- **Identical content short-circuits.** An incoming row whose content hash equals
  the current observation's reuses it (no new observation), so bookkeeping-only
  re-exports do not grow history.
- **Lock order** is provider lock, then catalog-writer lock (promotion only).
