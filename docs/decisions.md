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

## D011 — Identity resolution details (T08)

- Identity keys (`title_key`, `title_tokens`, `doi`, `explicit_refs`) are columns on
  `source_records` with B-tree/GIN indexes (migration 0003), refreshed from each new
  current observation. Exact-title lookup needs an index even without `pg_trgm`.
- Explicit cross-source references are matched in both directions (a record's own
  references, and other records that reference it), so arrival order does not
  matter (F02 before F01 still merges).
- Compatibility for explicit-ID and DOI matches: title-token Jaccard ≥ 0.5 (or
  equal title keys) and at least one shared author when both sides list authors.
  A DOI shared with an incompatible record is a review, never a merge (proceedings DOIs).
- Every review-only outcome still gives the record its own work; the review row
  links it to the candidate so a curator can merge. Records already attached to a
  work are never moved automatically.
- Similarity candidates use prefix filtering on sorted meaningful title tokens
  (GIN overlap), then exact Jaccard ≥ 0.85 in Python.
- Canonical publication-date conflicts are flagged
  (`publication_dates_differ_across_sources`) only when day-precision dates from
  different sources are more than 30 days apart; small preprint/journal offsets
  are normal and not flagged.
- `canonical_revision` increments only when `CanonicalWork.public_hash()` changes;
  provenance and observation-ID changes alone do not count.

## D012 — Event and curation details (T09)

- **arXiv OAI revisions.** The `arXiv` OAI format has no version numbers. When both
  the previous and new arXiv observations lack versions but `updated` advanced, the
  change counts as a `source_revision` dated by `updated` (arXiv sets `updated`
  only for new versions — an assumption until verified live). Elsewhere a revision
  needs an advancing `vN`.
- **New-list eligibility** is decided at export from the publication event's
  day-precision date and the recent window, so discovery time never matters.
  `metadata_updated` events fire only when the work's public canonical hash
  changed; bootstrap/backfill runs never create observed-date update events.
- **Withdrawal** covers both explicit `withdrawn` status and provider deletion
  tombstones (`details.to = "deleted"`); absence from a response is never withdrawal.
- **Curation state** (include, exclude, set_labels, set_fields) is folded from the
  active, non-reverted command list at every rebuild. Reverting a revert is refused;
  apply a new command instead. State commands addressed to an absorbed work stop
  applying after a merge (they are not re-targeted automatically).
- **Deferrable FKs** (migration 0004): overrides, separation constraints, and
  events reference the immutable command row, which is inserted last in the same
  transaction once its after-state is known.
- **Revert precondition:** current source-record/publication assignments and
  `merged_into` of every involved work must equal the original after-state;
  canonical revisions may differ (later independent observations are preserved).
  A split's new works become aliases of the original work on revert.

## D013 — Historical inputs (T10)

- DBLP and the curated file are *local snapshots*: `HarvestRunner.run_snapshot`
  stages bounded batches as pages with `{"after_ordinal": n}` cursors and reuses the
  same promotion, hooks, finalization, and coverage code as HTTP streams.
- Large dumps are stored with `BlobStore.put_file_if_absent` (streamed copy/upload),
  keyed by SHA-256, as the raw evidence for every DBLP page of that snapshot.
- Crossref enrichment uses the generic paged runner with the DOI list fixed in
  `harvest_runs.request_params` (migration 0005), so resumes never re-plan.
  `RequestSpec.ok_statuses` lets the adapter turn a 404 into an item quarantine.
- Identity special cases: Crossref records only enrich an existing work (no new
  works); curated records always get a work regardless of rule classification.
- Curated labels/foundational status are applied as idempotent internal
  `import_curated` curation commands whose ID includes a hash of the labels, so a
  label change produces a new audited command and an unchanged file is a no-op.
- The venue registry (`config/venues.yaml`) is agent-compiled and its DBLP prefixes
  are unverified until a real dump is processed.

## D014 — Release export details (T11)

- **Public catalog = included, live works.** Works under `review` or `excluded`
  are not published; merged works appear only in `aliases.json`.
- **Static lists:** New = active works with a day-precision publication date
  0–29 days before the release's as-of date; Updated = active works whose latest
  qualifying update event (source revision, metadata update, withdrawal,
  restoration) falls in the same 30-day window. Archive pages are per publication
  year (`unknown` last), Foundations and topic lists cover all dates. Withdrawn and
  unavailable works keep detail pages but are left out of these default lists.
- **Feeds** use the same windows as the lists (at most 100 entries each) so a feed
  reader never receives historical discoveries as new. New entry IDs are tag URIs
  of the work ID; Updated entry IDs hash the event key.
- **Additive public fields** (schema v1): `PublicPaper.history`; `PaperCard`
  (`json_path`, `has_abstract`, `has_code`, `venue`, `doi`); list pages carry
  `list`, `page_count`, `as_of_date`, `window_days`; the manifest also carries
  `as_of_date`, `site_title`, `site_description`, `public_site_url`.
- **Derived landing URLs** (D008) use ePrint's zero-padded display form
  (`2021/042`); BibTeX notes use the same form.
- **Card JSON paths** point at `<base>assets/releases/<release-id>/papers/<id>.json`,
  the versioned asset location of spec 16.1.
- **Ranks and date tokens** for Pagefind live in `search-facets.json` (per-paper
  `rank_newest/oldest/updated`, `publication_period`, `update_period`, identifiers).
- The export writes into a temporary sibling directory and renames it into place;
  an existing release directory is never overwritten.

## D015 — Frontend toolchain pins (T12)

- Astro 7.3.8, TypeScript 6.0.3, Ajv 8.20.0, KaTeX 0.19.0, Pagefind 1.5.2,
  Vitest 4.1.12, jsdom 30.1.2, @astrojs/check 0.9.10, @types/node 22.20.5 — exact
  versions, each published before 2026-10-20 (two-week minimum age).
- npm 10.9.9 (bundled with Node 22.23.4) crashes in arborist (`Cannot read
  properties of null (reading 'edgesOut')`) while resolving Vitest ≥ 4.1.10's
  optional peer graph (`msw` → `typescript >= 4.8.x`). Vitest 4.0.18 avoided the
  crash but carries a critical advisory (GHSA-qxg6-89mg-rqv8), so it was rejected.
  `apps/web/.npmrc` sets `legacy-peer-deps=true`; with it the locked tree installs
  via `npm ci`, `npm audit` reports 0 vulnerabilities, and the only unmet entries are
  optional platform bindings. Revisit when npm is upgraded.
- The build reads exactly one validated release (`CATALOG_DIR`), refuses a release
  exported for a different `PUBLIC_BASE_PATH` or with a different public schema,
  and copies release assets to `assets/releases/<release-id>/` (plus up to two
  previous releases via `PREVIOUS_CATALOG_DIRS`).
- Astro 7's `astro preview` runs a managed background server (`astro preview stop`).

## D016 — Search implementation details (T13)

- **Pagefind semantics were verified empirically on the installed 1.5.2 build**
  (probe index + Chromium): attribute-sourced filters (`area[data-value]`) accept
  values containing colons; `{any: [...]}` is OR and a plain array is AND; facets
  combine with AND; `data-pagefind-ignore` sections are not indexed; and an empty
  `{any: []}` matches **every** record — so the adapter returns zero locally for a
  constrained-but-empty date set and never sends it.
- The index is built with the Node API (`addHTMLFile`) from only the generated
  canonical paper pages, using each paper's `canonical_path` as URL, and written to
  `assets/releases/<release-id>/pagefind/` so pages and index always come from the
  same export. The build fails if any page is missing, unindexed, or lacks its ID.
- Sort ranks are zero-padded to 8 digits so string and numeric order agree.
- Per-paper search attributes moved from `search-facets.json` to an indexer-only
  `search-index-data.json`; browsers download only the token/value file.
- `window=unknown` is the explicit "Unknown date" selection (spec 12). Without a
  date constraint, unknown dates are included; any date constraint excludes them.
- Old-style arXiv IDs (`hep-th/9901001`) are treated as identifier queries only with
  an explicit `arXiv:` prefix or arXiv URL, so ordinary `word/word` text is never
  misread as an identifier. Bare `YYYY/NNNN` is treated as an ePrint ID.
