# Sol implementation specification: cryptographic proofs research index

Version 1.0 — November 3, 2026.

This is the detailed engineering handoff. It settles the major design choices and decomposes implementation into bounded tasks. The earlier project plan explains the reasoning; the earlier agent prompt is the shorter brief. This document is self-contained and is the implementation-level refinement of those documents, not a request to restart architecture research.

No application has been implemented by creating this specification. Code fragments below are contracts or reference algorithms to incorporate and test, not claims of a running application.

## 0. How to use this document

Give Sol this file, access to the implementation workspace, and this starting instruction:

> Implement the project described in `proof-papers-sol-implementation-spec.md`. Read sections 1–4 and the task schedule first. Follow the dependency order and read each task's referenced sections before coding. Implement and verify one bounded task at a time, updating `docs/implementation-status.md`, then continue without asking me to approve routine implementation choices. Preserve the confirmed broad scope and full historical coverage. Follow your environment's instructions and permissions. Do not treat a preview, unexecuted backfill, or provisional evaluation as a completed production project. Begin by inspecting the workspace and completing task T00.

Do not paste all three planning documents into every subsequent prompt. This document contains the necessary product and technical contracts. During a long run, retain the stable decisions, current task, verification results, and remaining blockers in the status file. Read the relevant contract again before changing an interface.

When this document says “implement,” write functioning code. When it says “verify,” run the check and retain its result. When an external prerequisite is unavailable, record that fact and continue independent tasks. Do not manufacture credentials, citations, source responses, human reviews, or test results.

## 1. Confirmed product requirements

The user wants a public research index inspired by [Recent-post-quantum-papers](https://github.com/MaherMamah7/Recent-post-quantum-papers) and [pqc-reference.org](https://pqc-reference.org/), with:

1. Cryptographic proof systems **and** security proofs **and** formal verification **and** general cryptographic protocol analysis.
2. Search, filters, readable paper pages, and no required reader accounts.
3. Full available historical coverage, including foundational papers; no arbitrary year cutoff.
4. Daily ongoing updates after historical import.

The reference's single Python scraper, text files, and compact chronological cards are inspiration. We need richer provenance, identity, revisions, classification, and history. Build original code and styling; a permissive license was not established for the reference repository.

Use the working title **Cryptographic Proofs Research Index**. Make title, description, canonical base URL, optional maintainer label, and optional contact configurable. Do not invent an affiliation. Do not block local work on naming or hosting decisions.

Out of scope for this release: reader accounts, shared notes, email subscriptions, AI paper summaries, semantic chat, vector search, citation rankings, PDF hosting, automated theorem/security-property extraction, and unrestricted web crawling.

## 2. Fixed engineering decisions

These choices are settled for this build. Change one only when actual compatibility/performance evidence requires it, and explain the deviation in `docs/decisions.md`.

| Concern | Decision |
|---|---|
| Python runtime | Python 3.12 baseline; record the tested patch version |
| Python packaging | `uv`, a `pyproject.toml`, committed `uv.lock`, package under `pipeline/src/proof_index/` |
| Python implementation | Synchronous Python; no distributed task queue; bounded sequential provider requests |
| Validation | Pydantic v2; JSON Schema export from the public models |
| Database | PostgreSQL 17 baseline, SQLAlchemy 2 Core, psycopg 3, Alembic migrations |
| HTTP | HTTPX with explicit timeout, retry, rate, and response validation code |
| XML | lxml; secure default parser; a constrained local DTD resolver only for official DBLP entities if required |
| CLI | Typer; one `proof-index` entry point |
| Storage | `BlobStore` protocol; local filesystem backend and S3-compatible backend using boto3 |
| Web | Astro static output, TypeScript, plain CSS, native HTML controls; no React requirement |
| Node/package manager | Node 22 baseline and npm with committed `package-lock.json` |
| Search | Pagefind, behind our own `SearchService` adapter; exact facet/date translation specified below |
| Math | KaTeX with trust disabled and escaped fallback text |
| Tests | pytest, HTTPX MockTransport fixtures, real disposable PostgreSQL integration tests, Vitest, browser checks when supported |
| Local infrastructure | Docker Compose PostgreSQL bound to localhost; ordinary local directory for blobs |
| Production compute | GitHub Actions jobs with durable checkpoints; resumable bounded backfill invocations |
| Reader hosting | Static output; GitHub Pages deployment adapter is the default example |

These runtime choices are baselines, not claims to be the latest release. Resolve compatible package patch versions once, verify against official documentation, and lock them. Do not use experimental framework APIs or floating production dependency installs.

Keep the pipeline independent of the frontend. The frontend builds from an exported release directory and must not connect to the production database. Public JSON must not contain database credentials, raw request headers, reviewer contact details, or internal error traces.

## 3. Repository and ownership map

Create a new project subdirectory if the current workspace only contains planning documents. Do not overwrite those documents.

```text
proof-papers/
  pyproject.toml
  uv.lock
  compose.yaml
  .env.example
  .gitignore
  Makefile
  README.md
  pipeline/
    src/proof_index/
      cli.py
      settings.py
      clock.py
      models/{source,dates,classification,public}.py
      adapters/{base,eprint,arxiv_oai,arxiv_atom,dblp,crossref,curated}.py
      http/{client,rate_limit,errors}.py
      storage/{base,local,s3}.py
      db/{tables,repository,locks}.py
      ingest/{runner,staging,promote,reconcile}.py
      normalize/{text,identifiers,authors,dates,links}.py
      classify/{rules,engine,evaluate}.py
      resolve/{candidates,identity,curation,canonical}.py
      publish/{snapshot,export,feeds,bibtex,coverage,validate}.py
  db/
    alembic.ini
    migrations/
  config/
    site.yaml
    sources.yaml
    taxonomy.yaml
    relevance-rules.yaml
    venues.yaml
  curation/
    foundations.jsonl
    overrides/
    evaluation/
  apps/web/
    package.json
    package-lock.json
    astro.config.mjs
    src/
      pages/
      components/
      layouts/
      lib/{contracts,dates,urls,search,format}.ts
      styles/tokens.css
      styles/global.css
    scripts/build-search.mjs
    tests/
  tests/
    unit/
    integration/
    fixtures/{eprint,arxiv,dblp,crossref,synthetic}/
  scripts/
    export-schema.py
    validate-release.py
  docs/
    implementation-status.md
    decisions.md
    source-contracts.md
    taxonomy.md
    runbook.md
    coverage-report.md
    evaluation-report.md
  .github/workflows/{checks,refresh,backfill,deploy}.yml
  work/                    # ignored runtime/scratch files
```

Do not create every empty module at once. Create files as their task becomes implementable. A module must not know details from a later layer: adapters do not write SQL; parsers do not call the network; classification does not fetch PDFs; Astro does not decide identity.

## 4. Configuration and command contract

Read environment variables through one settings module. Required settings:

```dotenv
# Local development only; never use these example credentials for production.
DATABASE_URL=postgresql+psycopg://proofs:proofs@127.0.0.1:5432/proofs
BLOB_BACKEND=local
LOCAL_BLOB_ROOT=./work/blobs
EXPORT_ROOT=./work/releases
SOURCE_CONFIG=./config/sources.yaml
SITE_CONFIG=./config/site.yaml
# Set the deployed origin/base path only when known.
PUBLIC_SITE_URL=http://localhost:4321
PUBLIC_BASE_PATH=/
# Production storage settings; leave unset for local mode.
S3_BUCKET=
S3_ENDPOINT_URL=
AWS_REGION=
# Prefer the normal SDK credential chain rather than custom secret handling.
PROJECT_CONTACT=
```

Fail with a useful configuration error when a command needs missing configuration. `build` from an existing export must not require `DATABASE_URL`. `doctor` reports available runtimes, connection checks requested by the user, and missing settings without printing passwords.

Implement these CLI shapes; flags may be additive but retain these meanings:

```text
proof-index doctor
proof-index sources verify --source eprint --bounded
proof-index sync --source eprint
proof-index sync --source arxiv
proof-index backfill --source arxiv --resume --max-minutes 45
proof-index backfill --source eprint --resume --max-minutes 45
proof-index backfill --source dblp --resume --max-minutes 45
proof-index import-curated curation/foundations.jsonl
proof-index enrich-dois --max-records 500
proof-index classify --changed
proof-index classify --all --rules-version <version>
proof-index review export --out work/review.jsonl
proof-index curate validate <override.yaml>
proof-index curate apply <override.yaml>
proof-index catalog validate
proof-index coverage report --out docs/coverage-report.md
proof-index evaluate --dataset curation/evaluation/heldout.jsonl
proof-index release export --out work/releases/<release-id>
proof-index release validate work/releases/<release-id>
proof-index backup --out <destination>
proof-index restore --input <backup> --target-database <disposable-target>
```

Backfill “45 minutes” is a safe execution bound, never a coverage cutoff. Persist progress and emit a resumable status. Exit codes: `0` requested operation succeeded (including a safely checkpointed bounded backfill slice); `2` invalid configuration/input; `3` provider/network failure; `4` data validation failure; `5` writer lock already held; `6` database/storage failure. Machine-readable summaries include `complete:false` for an unfinished slice even if exit code is 0.

`sources verify --bounded` writes a verification report and optional captured fixtures only. It never promotes sampled records into the live catalog or advances production watermarks. A deliberately bounded verification request is not a completed archive harvest.

Make targets should wrap documented real commands: `setup`, `db-up`, `migrate`, `test`, `test-db`, `fixture-export`, `web-build`, `web-preview`. Build uses `npm ci` and a supplied `CATALOG_DIR`, then `npm run build`. A fixture preview must show an unmistakable “Preview dataset” label.

## 5. Shared data contracts

Use UUIDs generated by the application, UTC timestamps, UTF-8, SHA-256 hashes, and JSON serialization with sorted keys and compact separators for hashing. Never derive a public work ID from a title; title changes must not change URLs.

All Pydantic models use `extra="forbid"`. Store raw source payloads separately so adding known schema fields does not discard original evidence. Code examples in this document use Python type notation; implement real validation, not only annotations.

### 5.1 Partial publication date

```python
class PartialDate(BaseModel):
    year: int | None
    month: int | None
    day: int | None
    precision: Literal["day", "month", "year", "unknown"]
```

Validation: unknown means all three numeric fields are null; year precision means only year exists; month precision means valid year/month and null day; day precision is a valid calendar date. Permit historical years 1–9999. Quarantine implausible source values rather than silently clamping them. `date_interval()` returns inclusive earliest/latest possible dates or `None` for unknown. This interval is a computational representation, not a claimed exact publication date.

### 5.2 Authors and links

```python
class Author(BaseModel):
    display_name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None

class SourceLink(BaseModel):
    kind: Literal["landing", "pdf", "code", "publisher", "other"]
    url: str
    origin: Literal["source_metadata", "curated"]
    verified_official: bool = False
```

Keep author order. Do not split a comma-separated display string to infer family/given names when structured names are unavailable. Permit only HTTP(S) external links, reject credentials embedded in URLs, and preserve link provenance. No arbitrary URL fetch is needed to render a link.

### 5.3 Normalized source record

```python
class NormalizedRecord(BaseModel):
    schema_version: Literal[1] = 1
    source: Literal["eprint", "arxiv", "dblp", "crossref", "curated"]
    source_id: str
    title: str
    authors: list[Author]
    abstract: str | None
    published: PartialDate
    source_modified_at: datetime | None
    harvest_datestamp: datetime | None
    source_version: str | None
    doi: str | None
    identifiers: dict[str, list[str]]
    source_categories: list[str]
    venue_key: str | None
    venue_name: str | None
    links: list[SourceLink]
    status: Literal["active", "withdrawn", "deleted"]
    evidence: dict[str, str]
```

`evidence` maps fields to provider paths or curated evidence locations, not secret request data. Observation envelope fields—run ID, fetch time, batch hash, parser version—sit outside this model. Deleted-provider tombstones have a separate `Tombstone(source, source_id, harvest_datestamp, evidence)` type; do not invent title/authors to satisfy a normal record schema.

Use one normalized arXiv source identity whether received through Atom or OAI. The adapter/stream is stored on the observation envelope. Normalize ePrint `2020/0007` to `2020/7`; modern arXiv IDs retain their zero-padded numeric portion but lose the `vN` suffix in `source_id`; retain `vN` separately. Older arXiv IDs retain archive prefixes. DOI normalization removes a DOI URL/prefix and trims whitespace; compare case-insensitively while retaining the original source string in raw evidence.

### 5.4 Stable hash contract

Hash the canonical normalized record, excluding fetch/run times, storage pointers, parser version, and evidence-path formatting. Include actual bibliographic fields, source status, version, and source-supplied dates. Order-insensitive fields such as categories/identifiers are sorted and deduplicated first; authors remain ordered. A whitespace-only normalization-equivalent change must not create a new meaningful revision.

Maintain two hashes: raw response hash for audit and normalized content hash for semantic change detection. A changed parser version alone is not a paper revision.

## 6. Database contract

Implement Alembic migrations using SQLAlchemy Core tables. Use `timestamptz`, `uuid`, `text`, `jsonb`, `boolean`, and integer types; no custom PostgreSQL enum migrations are necessary. Express bounded values as CHECK constraints and mirror them in Pydantic.

The following table definitions are exact logical requirements. JSONB model validation is performed on writes; SQL constraints enforce relational integrity. Every foreign key referenced below must exist. Defaults for timestamps come from the injected clock at the application boundary where test determinism matters.

| Table | Columns and constraints |
|---|---|
| `provider_streams` | `id uuid PK`, `provider text`, `stream_key text UNIQUE`, `config_hash text`, `completed_watermark timestamptz NULL`, `last_attempt_at`, `last_success_at`, `last_error_code text NULL`; same provider may have several streams |
| `harvest_runs` | `id uuid PK`, `stream_id FK`, `mode text` (`sync/backfill/verify/reconcile`), `state text`, `window_from/window_until timestamptz NULL`, `started_at`, `finished_at NULL`, `response_time NULL`, `pages_fetched int`, `records_staged int`, `records_promoted int`, `error_code/error_summary NULL` |
| `harvest_pages` | `id uuid PK`, `run_id FK`, `ordinal int`, `request_fingerprint text`, `blob_key text`, `raw_sha256 text`, `next_cursor jsonb NULL`, `is_terminal boolean`, `parsed_count int`, `state text`; UNIQUE(run_id, ordinal) |
| `staged_records` | `id uuid PK`, `run_id FK`, `page_id FK`, `source text`, `source_id text`, `payload jsonb`, `content_sha256 text`, `item_kind text` (`record/tombstone`), `state text` (`pending/promoted/quarantined`), `promoted_observation_id uuid NULL FK`, `error_code NULL`; UNIQUE(run_id, source, source_id, content_sha256) |
| `source_records` | `id uuid PK`, `source text`, `source_id text`, `current_observation_id uuid NULL`, `work_id uuid NULL FK`, `first_seen_at`, `last_seen_at`; UNIQUE(source, source_id) |
| `source_observations` | `id uuid PK`, `source_record_id FK`, `observation_key text UNIQUE`, `run_id FK`, `page_id FK`, `normalized jsonb`, `content_sha256 text`, `source_modified_at NULL`, `harvest_datestamp NULL`, `source_version NULL`, `observed_at`, `change_kind text`, `is_current_eligible boolean` |
| `works` | `id uuid PK`, `visibility text` (`included/review/excluded`), `record_status text` (`active/withdrawn/unavailable`), `canonical jsonb`, `canonical_revision bigint`, `is_foundational boolean`, `created_at`, `updated_at`, `merged_into uuid NULL FK works`; CHECK merged_into != id |
| `work_events` | `id uuid PK`, `work_id FK`, `observation_id uuid NULL FK`, `kind text`, `event_date date NULL`, `date_basis text`, `feed_eligible boolean`, `event_key text UNIQUE`, `created_at`; kinds specified in section 11 |
| `classifications` | `id uuid PK`, `source_record_id FK`, `content_sha256 text`, `rules_version text`, `decision text`, `areas jsonb`, `topics jsonb`, `contributions jsonb`, `evidence jsonb`, `review_provenance text`; UNIQUE(source_record_id, content_sha256, rules_version) |
| `publications` | `id uuid PK`, `work_id FK`, `provider text`, `provider_key text`, `doi text NULL`, `metadata jsonb`; UNIQUE(provider, provider_key). Do not globally force DOI uniqueness across all works: contradictory assignments must be preserved for review |
| `work_relations` | `id uuid PK`, `from_work FK`, `to_work FK`, `relation text` (`extended_by/related_to`), `evidence jsonb`, `created_at`; CHECK from_work != to_work; UNIQUE(from_work,to_work,relation) |
| `identity_reviews` | `id uuid PK`, `candidate_key text UNIQUE`, `source_record_id FK`, `candidate_work_id FK`, `reason text`, `evidence jsonb`, `state text` (`pending/accepted/rejected`), `created_at` |
| `curation_commands` | `id uuid PK`, `command_hash text UNIQUE`, `kind text`, `payload jsonb`, `reviewer_label text`, `review_kind text`, `reason text`, `applied_at`, `before_state jsonb`, `after_state jsonb`, `reverts_command_id uuid NULL FK`; immutable audit rows |
| `source_assignment_overrides` | `source_record_id FK PK`, `target_work_id FK`, `command_id FK`; prevents resolver from undoing manual assignments |
| `separation_constraints` | `work_a FK`, `work_b FK`, `command_id FK`, PK(work_a,work_b); store sorted UUID pair and disallow equal IDs |
| `coverage_slices` | `id uuid PK`, `stream_id FK`, `slice_key text`, `state text`, `expected_count bigint NULL`, `seen_count bigint`, `valid_count bigint`, `quarantined_count bigint`, `notes jsonb`, `run_id FK`; UNIQUE(stream_id,slice_key) |
| `releases` | `id uuid PK`, `state text` (`exported/validated/built/deployed/failed`), `catalog_snapshot_at`, `manifest jsonb`, `blob_prefix text`, `created_at`, `deployed_at NULL` |

Create `works` before `source_records`; add `source_records.current_observation_id` and `staged_records.promoted_observation_id` FKs after creating observations. Add UNIQUE(source_record_id,id) on observations and a composite FK from source_records(id,current_observation_id) to observations(source_record_id,id), so the current observation must belong to the right record. No cascading deletion of observations, source records, curation, or works in normal operation.

Indexes: all join FKs; `source_records(work_id)`; `source_observations(source_record_id, observed_at DESC)`; `staged_records(run_id,state,id)`; `works(visibility,merged_into)`; `work_events(work_id,event_date DESC)`; `publications(doi)`; `identity_reviews(state)`; `harvest_runs(stream_id,started_at DESC)`. Use `pg_trgm` on a materialized normalized title column only if measured identity-candidate lookup needs it; do not require it initially.

Store `canonical` as a validated typed projection, not as the sole evidence store. It can be rebuilt from observations and curation. Increment `canonical_revision` only when the canonical public content changes. Work timestamps alone never determine paper revision events.

## 7. Adapter interfaces and rate control

Use these conceptual interfaces with concrete immutable dataclasses/Pydantic models:

```python
class SourceAdapter(Protocol):
    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan: ...
    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec: ...
    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage: ...

class ParsedPage(BaseModel):
    records: list[NormalizedRecord | Tombstone]
    next_cursor: dict | None
    terminal: bool
    provider_response_at: datetime | None
    reported_total: int | None
    quarantined_items: list[ParseFailure]

class BlobStore(Protocol):
    def put_if_absent(self, key: str, body: bytes, sha256: str) -> BlobRef: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str, sha256: str | None = None) -> bool: ...
```

Adapters plan and parse; the shared HTTP client sends requests; the runner stores and promotes. `ParsedPage.terminal` must agree with cursor absence. An error response is not a `ParsedPage(records=[], terminal=True)` except a documented no-records result such as OAI `noRecordsMatch`.

DBLP uses a separate streaming-dump parser yielding bounded batches compatible with staging. Do not wrap a multi-gigabyte dump in a `ParsedPage.records` list. Persist dump fingerprint and byte-independent record ordinal; resuming may re-stream and skip processed ordinals rather than seeking into compressed data. A changed dump starts a new logical snapshot, not a continuation of the old ordinal.

HTTP defaults: connection timeout 10s, read timeout 60s, maximum 4 attempts. Retry connection/timeouts, 429, and selected transient 5xx. Do not repeatedly retry 400/401/403/404. Honor `Retry-After`; if the required delay exceeds the current job's remaining budget, checkpoint and exit resumably. Bound normal XML response size at a configurable 50 MiB; DBLP uses streaming and separate limits. Log sanitized URLs/provider/status/elapsed time, not response secrets.

Rate limits live in `sources.yaml`. Start arXiv at one request every 3.1 seconds, one connection; ePrint at a conservative sequential delay and daily synchronization. Recheck official guidance before enabling live collection. Separate manual/backfill executions share the same provider rate lock. Use a dedicated PostgreSQL session advisory lock keyed by a stable SHA-256-derived 64-bit value, not Python's randomized `hash()`; release it in `finally`. Hold no ordinary SQL transaction during network sleep/request. A process crash releases its session lock.

Use one global catalog-writer session lock for promotion/curation. A release export uses a database snapshot and does not hold the provider lock. Do not call simultaneous source HTTP endpoints just because they have different adapter names: both arXiv adapters share the arXiv limiter.

## 8. Harvest state machine and promotion algorithm

Allowed run states:

```text
planned -> fetching -> staged -> promoting -> succeeded
fetching -> paused -> fetching
fetching -> failed
promoting -> paused -> promoting
promoting -> failed
```

`paused` here means a resumable pipeline job state (for its execution budget), not a chat/agent goal status. Source success timestamps and watermarks change only on `succeeded`.

Algorithm:

```text
1. Validate source configuration and acquire the required provider lock.
2. Reuse a resumable run with matching stream/window/config, or create a run.
3. For every page:
   a. Request under the shared limiter.
   b. Store immutable response bytes by SHA-256.
   c. Parse outside a SQL transaction.
   d. In one short transaction, insert page metadata, staged records,
      quarantine details, and its next cursor; mark the page durable.
   e. If budget is nearly exhausted, persist paused state and stop safely.
4. On terminal page, validate cursor/page/count invariants. Mark run staged.
5. Promote pending staged rows in batches of 250:
   a. Upsert source identity.
   b. Insert an observation idempotently.
   c. Select whether it can update current source state.
   d. Classify changed eligible state.
   e. Resolve work identity and rebuild affected canonical work projections.
   f. Add only supported events; mark stage row promoted.
   All these operations commit together for each batch.
6. After all rows are promoted or explicitly quarantined, validate the run.
7. In one short transaction, set succeeded, update the stream's completed
   watermark and last-success timestamp, and finalize coverage counts.
8. Release locks. The next release builder may now include the new state.
```

A run with malformed individual records may complete only when every failed item is preserved in quarantine and coverage reports the omissions. Malformed page structure, unknown protocol errors, inconsistent total counts, repeated cursor loops, or truncated responses fail the run. Do not let item quarantine conceal an entire unparsed response.

The global writer lock prevents export start during a partial promotion. An export obtains that lock briefly while opening its consistent REPEATABLE READ snapshot, then releases it once the snapshot is established. To simplify the first implementation, holding the writer lock through a short export is also acceptable; never hold it through the Astro build. A failed partial promotion may leave internal changes, but no release is exported until that run is resumed to completion or its changes are deliberately reconciled. Record this pending-promotion condition durably so a process crash cannot accidentally permit export.

Observation key for provider-ordered records: SHA-256 of source record identity, normalized content hash, source version, and provider modification/harvest stamp. Replaying the same response produces the same key. For records with no ordering fields: if the normalized hash equals the current observation, reuse that observation; otherwise key the transition using source identity, the previous current observation ID (or a fixed initial marker), and the incoming hash. Persist `promoted_observation_id` and the stage row's promoted state in the same transaction as updating current state. A resumed promoted row reuses its recorded result rather than recomputing a transition. This retains A→B→A once and makes crash/replay safe. Do not use a global UNIQUE(source_record,content_hash), which would erase reversions.

Current selection: compare documented provider ordering fields. A newer modification/version wins; equal fields plus identical hash is a no-op; equal fields with conflicting content creates a review/inconsistency record. An older historical observation is retained but must not overwrite a newer current record. Where no provider ordering exists, compare durable fetch-snapshot order and retain uncertainty. Do not assume the largest UUID or latest row insertion represents the newest source version.

Initial OAI synchronization starts without an artificial publication cutoff. Incremental `from` uses the last completed provider boundary minus a configurable overlap (initially 2 days). Save provider response time as the boundary when documented; use an explicit bounded `until` only if supported and tested. Never replace provider cursor semantics with publication date.

Keep bootstrap and incremental streams separate, for example `arxiv:cs.CR:bootstrap:v1` and `arxiv:cs.CR:incremental:v1`. Capture the initial incremental boundary before starting bootstrap; daily updates may proceed from that boundary while old data imports. On bootstrap completion, reconcile changes from that boundary without rewinding an already-later incremental watermark. Coverage records the bootstrap separately from freshness. This prevents a historical job from overwriting a current sync cursor.

Token expiry: discard the expired transient token, replay the fixed safe window, and upsert idempotently. A retry must not advance the completed watermark until the replay is complete. Changing adapter query configuration creates a new stream identity or a deliberate reconciliation; it must not silently reuse incompatible cursor state.

## 9. Provider-specific mapping and historical import

Implement fixture-driven mapping before broad network collection. Save `provider`, endpoint, access date, parser version, and field-path mappings in `docs/source-contracts.md`. Distinguish a documented assumption from a live verified response.

| Provider | Identity | Required mapping decisions | Historical strategy |
|---|---|---|---|
| ePrint OAI | Archive ID `YYYY/N`, from verified identifier/header | DC title/creator/description, original date versus header modification, status, source URLs, any explicit version evidence | Exhaust supported records, preserving resumption tokens and earliest-coverage metadata |
| arXiv OAI | arXiv identity without `vN` | Provider-specific metadata format, `created/updated`, author structure, category list, license/provenance, version history only if exposed | Full verified category sets; deduplicate cross-listed IDs |
| arXiv Atom | ID from entry URL, version separated | Atom title/summary/authors/published/updated; DOI/journal/comment extensions and source links | Supplemental title-and-abstract discovery, partitioned when result limits require it |
| DBLP | Exact DBLP record key | Relevant entry type, title including inline XML text, ordered authors, year, venue, DOI/URL; do not treat editors as authors | One official dump snapshot, streamed; retain matching venue or topic candidates |
| Crossref | Normalized DOI | Title arrays, author fields, publication-date variants, container title, relationships; XML abstract only if use is appropriate | Enrich discovered DOIs and bounded bibliographic candidates |
| Curated | Stable `curated:<slug>` | Verified bibliographic fields plus evidence/rationale/review type | Version-controlled foundational file, no age cutoff |

Rules for missing data: empty normalized title quarantines a normal record; absent authors are allowed but flagged and prevent title+author automatic matching; absent abstract is null; unknown source date stays unknown; unknown source version stays null. Bad URLs are dropped with an issue code while retaining raw evidence. One dropped optional URL should not discard an otherwise valid paper.

OAI parser behavior must explicitly distinguish `noRecordsMatch`, `badResumptionToken`, invalid arguments, unsupported formats, and unknown errors. Read namespace URIs, not hard-coded prefixes. Unexpected login/HTML pages are failures even when HTTP status is 200. An XML declaration alone does not establish valid source data.

Scope-specific category discovery: resolve the available arXiv set list once and persist the exact mapping for `cs.CR`, `cs.CC`, `cs.DS`, `cs.LO`, `cs.PL`. If a category set does not exist, document the smallest supported enclosing set and filter category fields locally; do not invent a set identifier. Supplemental queries cover adjacent `cs.IT`, `cs.AI`, `cs.LG`, and `quant-ph` with both title and abstract fields. Category-query definitions and versions belong in configuration.

DBLP candidate inclusion is broad: configured venues OR explicit relevant topic/tool signals. Store candidates for classification even if abstracts are missing. Papers outside configured venues can still enter through other sources or curation. The full DBLP dump is a discovery input; claiming that the entire dump is a relevant proof-paper corpus would be incorrect.

Crossref date precedence for bibliographic display: preserve all received date types; choose the earliest supported publication manifestation date, not indexing/deposit/creation dates. A DOI's first deposit date is not a publication date. Use publisher metadata to enrich a known work; if title/author information conflicts materially, keep a review item instead of overwriting.

Curated import format:

```json
{
  "curated_id": "stable-local-slug",
  "title": "Verified bibliographic title",
  "authors": [{"display_name": "Verified author name"}],
  "published": {"year": 1985, "month": null, "day": null, "precision": "year"},
  "doi": null,
  "source_identifiers": {},
  "landing_url": "https://example.org/authoritative-record",
  "areas": ["proof-systems"],
  "topics": ["zero-knowledge"],
  "contributions": ["construction-theory"],
  "foundational": true,
  "rationale": "Brief reason this belongs in the foundational collection.",
  "evidence_urls": ["https://example.org/authoritative-record"],
  "review_kind": "agent-provisional"
}
```

This is a schema example, not a real record. Do not publish it. Validate real URLs/metadata before importing the actual collection. Stable slugs do not change when title spelling is corrected.

Use these as **discovery queries**, not verified ready-to-import bibliography: “The Knowledge Complexity of Interactive Proof Systems”; “How to Prove Yourself: Practical Solutions to Identification and Signature Problems”; “Non-Interactive Zero-Knowledge and Its Applications”; “Probabilistic Checking of Proofs: A New Characterization of NP”; “Constant-Size Commitments to Polynomials and Their Applications”; “The Security of the RSA Cryptosystem and Signatures”; “Relations Among Notions of Security for Public-Key Encryption Schemes”; “Universally Composable Security”; “A Logic of Authentication”; “A Hierarchy of Authentication Specifications”; “The Inductive Approach to Verifying Cryptographic Protocols”; “The Tamarin Prover for the Symbolic Analysis of Security Protocols.” Verify the actual title, publication manifestation, authors, and date independently. Expand across all three primary areas; do not treat this seed list as exhaustive or authoritatively ranked.

Completion accounting: mark each source/venue/set snapshot as `not_started`, `running`, `complete`, `partial`, or `blocked`. Record known expected totals only when providers actually supply them. Never fill unknown denominators with the number we happened to download. Show source coverage and relevance-review coverage separately.

## 10. Deterministic classification design

Implement a rule engine with **evidence groups**, not a single giant keyword OR. The initial rules below are a reproducible starting policy; their thresholds are product logic, not calibrated probabilities. Version them as `rules-v1`; tune only from recorded evaluation errors and increment the version.

### 10.1 Matching primitive

Normalize text using Unicode NFKC, normalize Unicode dashes to `-`, collapse whitespace, and preserve a separate case-sensitive representation. Retain title and abstract as separate fields. Regex definitions must be static configuration, compiled once, with no user-supplied regex execution. Record matched normalized-field snippets and offsets, explicitly labeled as normalized offsets; do not pretend these positions map exactly onto original Unicode text.

Each rule contains:

```yaml
id: PS_DIRECT_SNARK
version: 1
area: proof-systems
topic: succinct-proofs
field: title_or_abstract
case_sensitive: false
pattern: '\b(?:zk[- ]?)?snarks?\b'
evidence_group: ps_direct
```

The engine returns `decision`, `areas`, `topics`, `contributions`, `matched_rules`, `review_reasons`, `rules_version`, and `review_provenance="automatic"`. Matching an area does not imply every topic under that area.

### 10.2 Evidence groups and starter signals

| Group | Signals to encode with word boundaries and reasonable hyphen/spacing variants |
|---|---|
| `ps_direct` | zero-knowledge proof/argument; proof/argument of knowledge; SNARK/SNARG/STARK; polynomial commitment; interactive oracle proof; probabilistically checkable proof; Sigma protocol; MPC-in-the-head; VOLE-in-the-head; recursively composed proof; incrementally verifiable computation |
| `ps_component` | sumcheck/sum-check; GKR; FRI; low-degree testing; inner-product argument; lookup argument; accumulation scheme; folding scheme; arithmetization; R1CS; Plonkish; zkVM |
| `crypto_context` | cryptographic/cryptography; zero knowledge; prover and verifier; cryptographic protocol; encryption; signature; key exchange; authentication protocol; secure multiparty computation; polynomial commitment; non-interactive argument |
| `security_method` | security proof; security reduction; provable security; simulation-based security; game-based security; universal composability; computational soundness; random oracle model; quantum random oracle; tight reduction; cryptanalysis; forgery attack; key-recovery attack |
| `protocol_object` | KEM/key encapsulation; encryption; digital signature; authenticated key exchange; authentication protocol; oblivious transfer; multiparty computation/MPC in cryptographic context; privacy protocol; commitment scheme |
| `formal_method` | formal verification; mechanized proof; machine-checked proof; verified implementation; symbolic protocol analysis; computational protocol verification; proof assistant |
| `formal_tool` | Tamarin; ProVerif; CryptoVerif; EasyCrypt; Coq/Rocq; Isabelle; F*; Lean when identified as the tool |
| `ambiguous_only` | proof; argument; AIR; PCS; IPA; Nova; Halo; Spartan; folding; security; verification; theorem |

Do not treat uppercase “STARK” as a universal semantic guarantee: explicitly non-cryptographic context or incidental survey references may still require review. Bare acronyms IP, PCP, IOP, ZK, MPC, UC, or RO require their contextual group. Expand starter rules based on evidence from real source records rather than blindly adding every scheme name.

### 10.3 Decision procedure

For each source record independently:

1. Apply an explicit record/work curation override if present.
2. `proof-systems` is supported by a `ps_direct` match in the title; or by a `ps_direct` abstract match with an independent `crypto_context`/`ps_component` cue; or by `ps_component` plus `crypto_context` with at least one of those cues in the title.
3. `security-proofs` is supported by `security_method` plus `protocol_object`/`crypto_context`, with at least one relevant cue in the title. Also permit title-level “cryptanalysis of <named scheme>” when the scheme is in a versioned cryptographic-name allowlist with documented provenance.
4. `formal-verification` is supported by `formal_method` or `formal_tool` together with `crypto_context`/`protocol_object`; require a formal cue in the title or at least two distinct formal cues in the abstract.
5. If one or more areas are supported and there is no contradictory context, `include` with all supported labels.
6. If there are relevant weak cues but insufficient context, missing metadata that prevents a decision, or conflicting cues, `review` with reasons.
7. If there are no relevant supported/weak cues, `exclude`. Generic tokens in `ambiguous_only` alone do not force review of every mathematics paper.

“Independent cue” means evidence from a different phrase span and conceptual group, not the same substring counted twice. For example, “zero-knowledge proof” cannot count simultaneously as both the sole direct cue and its own corroborating crypto context. Title-only historical records may be included by unambiguous direct phrases or explicit curation. Others remain reviewable rather than disappearing.

Contradictory contexts (astronomy “Nova,” aerodynamic “air,” protein folding) suppress ambiguous-name matches only. They are not blanket negative keywords capable of excluding a real cryptographic paper mentioning another discipline.

Contribution mapping: title/abstract evidence for attack/cryptanalysis -> `analysis-attack`; formal proof construction/security theorem -> `construction-theory`; accelerator/implementation/compiler/benchmark -> `implementation-optimization`; deployed application/task use -> `application`; title “survey,” “SoK,” or “tutorial” -> `survey-tutorial`. Multiple types are allowed. If evidence is insufficient, leave the array empty and display “Research paper”; do not fabricate a type.

Work-level aggregation unions supported area/topic labels from its current valid source records. `included` if any current source is included, otherwise `review` if any is review, otherwise `excluded`, subject to the latest explicit work override. Curated foundational status is an explicit curation field; classification cannot infer it from age or citation counts.

### 10.4 Evaluation procedure

Create a seeded, stratified 300–500-record labeling sample: all three areas, multiple eras, at least two provider classes, title-only records, and ambiguous negatives. Keep overlapping source versions of the same work in the same split to prevent leakage. Assign a deterministic split using canonical work ID and a fixed seed, and store it.

Evaluation must report automatic inclusion precision, recall with review treated as not automatically included, review workload, and per-area/era results. Report an additional assisted recall measure separately if review cases are counted. Do not tune on the held-out split and then continue calling it held out. Proposed targets: 90% precision and 95% recall on independently labeled in-scope records; these are not claims that `rules-v1` reaches them.

Agent-assigned labels are `agent-provisional`. Human-reviewed labels are `human-reviewed` only when such review occurred. No independent labels means no claim of independent benchmark performance. Keep broad-stream retrieval audits distinct from classifier accuracy: missed candidates cannot be detected by testing only selected candidates.

## 11. Identity, canonicalization, revisions, and curation

### 11.1 Candidate resolution

The normalized source key always wins for source-record identity. For a new source record:

1. Check explicit curator assignment.
2. Resolve explicit cross-source IDs in metadata. Verify the referenced record exists and is compatible.
3. Find exact DOI candidates. If identifiers conflict or multiple works claim the DOI, queue a review.
4. Find exact normalized-title candidates. Title normalization: NFKC, case-fold, whitespace collapse, normalize punctuation; retain alphanumeric tokens and meaningful mathematical distinctions. Never strip every symbol and assume the result proves identity.
5. A title+author candidate can auto-attach only if there is exactly one candidate, title has at least five meaningful tokens, no conflicting DOI/publication evidence, and ordered normalized author display-name lists match exactly. This deliberately conservative policy can miss duplicates; record those as reviews instead of weakening it silently.
6. Similarity fallback for review only: title-token Jaccard >= 0.85 with at least one compatible author and at least five title tokens. Short/generic titles require explicit identifiers or review.
7. No supported match: create a new work when the record is included or reviewable. Excluded records may retain null `work_id` until reclassification includes/reviews them.

Explicit identifiers are strong evidence, but incompatible titles/authors/record type still create review. A Crossref DOI for a conference proceedings volume must not merge all constituent papers. When two already-published works need merging, create a curator review rather than silently choosing a winner in a background job.

### 11.2 Canonical fields

Store per-field provenance `{source_record_id, observation_id, field}`. Precedence:

- Explicit curated field override wins.
- Otherwise prefer a usable current preprint title/abstract from ePrint, then arXiv; within a provider use its selected current observation.
- If no preprint field exists, use curated verified bibliography, then Crossref, then DBLP for bibliographic fields. Curated corrections still win globally.
- Author order comes from the selected title's source when available; do not stitch author fragments from different providers into one invented list.
- Venue/DOI information comes from publication records; it need not come from the title's source.
- Source links are a deduplicated union with provenance; keep all valid source destinations.
- The original publication is the supported source date with the earliest interval lower bound. Where bounds tie, prefer a more precise date only when it cannot contradict an earlier possible manifestation; preserve unresolved conflicts in metadata issues. Do not sharpen a year-only early record using a later journal issue date.

An absent abstract never becomes an AI summary. Display “Abstract unavailable” and retain the record.

### 11.3 Events and feed policy

Event kinds: `publication`, `source_added`, `source_revision`, `metadata_updated`, `withdrawn`, `restored`, `curation`. Maintain one work-level publication event keyed by the work's identity; its supported date can be corrected. Other events have stable keys derived from their source observation/curation command.

- First observation during bootstrap: store metadata/history but do not emit a current Updated-feed event merely because ingestion happened now.
- A genuinely new work with a supported day-precision publication in the recent window can appear in New, regardless of when first discovered; old work cannot.
- New arXiv/ePrint cross-post attached to an existing work: `source_added`, not a new publication and not an Updated-feed entry by default.
- A changed provider version after an earlier known observation: `source_revision`; use the supported source update date, not first publication.
- Metadata changed without a numbered source revision: `metadata_updated`, visibly labeled as such. If no reliable source date exists, use observed change date with `date_basis="observed"`; only after a baseline observation, never on bootstrap.
- Reclassification, a provider bookkeeping timestamp, or raw whitespace changes: no reader-visible paper update.
- Withdrawals/restorations: explicit status events with provenance; absence from a recent response is not withdrawal.

New/Updated lists show a canonical work once. Updated uses the latest qualifying event and links to the full event history. Use separate feeds for new publications and updates, stable entry IDs, and a maximum of the latest 100 eligible entries in each. A polling reader must not receive hundreds of historical discoveries as new publication events.

### 11.4 Curator commands

Use YAML input with `command_id`, `action`, `reason`, `reviewer_label`, `review_kind`, `expected_canonical_revision`, and action parameters. Supported actions: include, exclude, set_labels, set_fields, mark_foundational, merge, split, relate, revert.

All commands validate first, acquire the writer lock, and run transactionally. Reapplying the same command/hash is a no-op. Reusing a command ID with different content is an error. A stale expected revision fails with a readable conflict report; do not overwrite intervening changes.

Merge requires explicit survivor ID and absorbed IDs. Move source assignments/publications as specified, retain alias relationships through `merged_into`, recompute canonical fields/events, and save the before/after source assignments. Resolve aliases to a live survivor with cycle detection. Static aliases use a clear “Moved to…” link, canonical URL, and redirect markup where supported; do not claim an HTTP 301 on a host that only serves static HTML.

Split requires explicit source-record/publication assignments; title heuristics must not decide the split. Add separation constraints and source assignment overrides so automatic resolution cannot undo it. Preserve valid public IDs; create a new work ID only where needed. If a source has no viable assignment, reject the command rather than losing it.

Revert is a new audited command, not deletion of history. Require current assignments/revisions to match the original after-state or produce a conflict for review. Preserve later independent observations; do not restore an old snapshot over unrelated new source metadata.

## 12. Exact date and filter semantics

Use an injected UTC clock in pipeline tests and a `todayUTC()` wrapper in frontend tests. Never scatter `datetime.now()` or `new Date()` through business logic.

For a precise publication date `d` and UTC calendar date `today`, `age = (today-d).days`. Last 7 days means `0 <= age < 7`; Last 30 means `<30`; Last 365 means `<365`. Future dates do not enter these views. Quarantine suspicious future source dates for review; do not clamp their age to zero. Unknown and year/month-only dates are not eligible for rolling-day views.

If showing three simultaneous buckets: `0..6`, `7..29`, and `30..364`, with distinct labels. The normal dashboard uses a single selected window, so overlapping preset definitions are fine.

Archive year-range filters include a partial date if its interval overlaps the requested calendar-year interval. For a custom precise date range, use interval-overlap semantics for partial dates and show “Includes approximate dates.” Provide an `exact_dates=1` option for readers needing day-precision results only. Unknown dates appear only in unconstrained date searches or an explicit Unknown date selection.

Deterministic display sort:

1. Newest: known dates by interval lower bound descending; ties by precision (day, month, year), normalized title, stable UUID; unknown dates last.
2. Oldest: known dates by interval lower bound ascending; same ties; unknown last.
3. Updated: latest qualifying event date descending, with unknown/no event last.
4. Relevance: Pagefind order when textual query exists; otherwise newest.

Explain approximate historical dates in the UI; a sort lower bound is never exported as a fabricated exact publication date.

Test calendar fixed at **2026-11-03 UTC**:

| Input | Expected |
|---|---|
| 2026-11-03 | Last 7, Last 30, Last 365 |
| 2026-10-28 | Last 7 (age 6) |
| 2026-10-27 | Not Last 7; Last 30 |
| 2026-10-05 | Last 30 (age 29) |
| 2026-10-04 | Not Last 30; Last 365 |
| 2026-11-04 | No recent window; future-date issue |
| year-only 1985 | Archive 1985, no precise recent window |
| month-only 2026-11 | Archive 2026; not Last 7 |
| unknown | Unconstrained archive/search only; last in chronological sorts |
| source first observed today, published 1985 | Archive/foundations, not New |

URL contract:

```text
/search/?q=folding&area=proof-systems&area=security-proofs
  &topic=recursion&source=eprint&source=arxiv
  &kind=construction-theory&year_from=1980&year_to=2026
  &code=1&sort=newest&page=2
```

Use repeated keys for multi-selects. Recognized keys: `q`, `area`, `topic`, `source`, `venue`, `kind`, `status`, `window`, `year_from`, `year_to`, `date_from`, `date_to`, `exact_dates`, `code`, `view`, `sort`, `page`. Valid views: `new`, `updated`, `archive`, `foundations`. Default main page: New, Last 30 days, newest, 25 per page. Default archive/search: all dates; query -> relevance, otherwise newest. Status defaults to active; the advanced status selector can include withdrawn/unavailable records in archive/search. New/Updated remain active-only by default.

OR within a facet, AND between facets. Filters change -> page resets to 1. Parse integers strictly; reject impossible dates; swap no reversed ranges silently—show inline validation and do not execute an invalid search. Ignore unknown keys safely. Deduplicate/sort multi-values for serialization. New/Updated view imposes its required event/date constraints even when other filters are cleared.

## 13. Release files and frontend contract

The pipeline exports into a new directory and validates it before it can be used by Astro. Never export directly over the last successful release. Directory:

```text
<release-id>/
  manifest.json
  schema/public-paper.schema.json
  papers/<work-id>.json
  lists/new/<page>.json
  lists/updated/<page>.json
  lists/archive/<year>/<page>.json
  lists/foundations/<page>.json
  topics/<topic>/<page>.json
  coverage.json
  status.json
  search-facets.json
  feeds/new.atom
  feeds/updated.atom
  citations/<work-id>.bib
  aliases.json
  checksums.json
```

`manifest.json` fields: `schema_version:1`, `release_id`, `dataset_mode` (`live/fixture`), `generated_at`, `catalog_snapshot_at`, `code_revision`, `rules_version`, `source_config_hash`, `included_work_count`, `source_record_counts`, `sources` (per-source success/attempt/checkpoint/coverage), `available_years`, `topic_counts`, `coverage_status`, `page_size:25`, `public_base_path`. Counts must be computed from this snapshot. A build without Git can use `code_revision:"unknown-local"`; production requires a real revision.

List files contain `release_id`, `total`, `page`, `page_size`, `items` (small card projections), and optional `as_of_date`. Every list count counts canonical works once. Empty lists still have valid metadata and `items:[]`. Out-of-range pages return an intentional empty/clamped UI state without pretending a server route exists.

Public paper contract:

```typescript
type Area = "proof-systems" | "security-proofs" | "formal-verification";
type DatePrecision = "day" | "month" | "year" | "unknown";

interface PublicPaper {
  schema_version: 1;
  release_id: string;
  id: string;
  title: string;
  authors: { display_name: string; orcid: string | null }[];
  abstract: string | null;
  published: {
    year: number | null; month: number | null; day: number | null;
    precision: DatePrecision;
  };
  areas: Area[];
  topics: string[];
  contributions: string[];
  foundational: boolean;
  status: "active" | "withdrawn" | "unavailable";
  metadata_issues: string[];
  sources: {
    provider: "eprint" | "arxiv" | "dblp" | "crossref" | "curated";
    source_id: string;
    landing_url: string | null;
    pdf_url: string | null;
    version: string | null;
    status: "active" | "withdrawn" | "deleted";
  }[];
  publications: {
    venue_key: string | null; venue_name: string | null;
    year: number | null; doi: string | null;
  }[];
  code_links: { url: string; origin: string; verified_official: boolean }[];
  latest_update: {
    kind: "source_revision" | "metadata_updated" | "withdrawn" | "restored";
    date: string; date_basis: "source" | "observed";
    source_record_id: string | null;
  } | null;
  inclusion: {
    review_status: "automatic" | "agent-provisional" | "human-reviewed";
    explanation: string;
    evidence: { field: string; snippet: string; rule_id: string | null }[];
  };
  related_work_ids: string[];
  canonical_path: string;
  bibtex_path: string;
}
```

Generate JSON Schema from the Python model and test matching frontend types/runtime validation. Use a small runtime validator for untrusted JSON loads, preferably the exported schema through a compatible validator; do not maintain two unrelated hand-written schemas. Public card projection omits full abstract/evidence/history; detail loads can retrieve them.

Canonical status: active if an authoritative current source still reports active; withdrawn if there is no active source and explicit withdrawal evidence exists; unavailable if all known source records are deleted/unavailable. Show source-level withdrawal indicators even when another source remains active. Keep withdrawn/unavailable detail pages with clear labels; exclude them from default New/Updated/search results unless the reader enables the advanced status filter. Do not hide them by erasing history.

Version discipline: every public payload carries the release ID. The frontend rejects an unexpected release ID and offers Reload instead of mixing datasets. Server-generated canonical pages and Pagefind indexes must come from the same export. Schema changes increment the public schema version and require frontend support before deployment.

## 14. Concrete Pagefind integration

Use one index entry per canonical, non-merged public work. Do not index navigation, About, source-status pages, aliases, or repeated archive cards as extra paper results.

Build the index after Astro by calling Pagefind's Node API on only the generated canonical paper HTML files. Each canonical paper page has one indexed article body with title, authors, abstract, topic labels, normalized identifiers, and venue. Apply title weight 3 and author weight 2; body text weight 1. Escape all attributes and text. Metadata includes work ID and relative path to that release's public JSON. These weighting numbers are our initial policy, not claims about Pagefind defaults. [Weighting documentation](https://pagefind.app/docs/weighting/).

Generate these facets from canonical data:

| Facet | Values |
|---|---|
| `area`, `topic`, `source`, `venue`, `kind` | Arrays of stable IDs, not display labels |
| `code` | `yes` or `no` |
| `status` | active, withdrawn, unavailable |
| `foundational` | yes or no |
| `publication_period` | Exactly one date token from the policy below |
| `update_period` | Latest qualifying update-day token, or unknown |
| `has_update` | yes or no |
| `identifier` | Normalized qualified source IDs and DOIs |

Date token format: `d:YYYY-MM-DD`, `m:YYYY-MM`, `y:YYYY`, or `unknown`. Persist the sorted set of existing tokens plus their interval bounds/precision in `search-facets.json`. There is one token per distinct date value, not per paper. At search time, choose tokens satisfying section 12's policy; pass those tokens as an OR filter. This implements exact date ranges and historical partial-date behavior without assuming Pagefind supplies numeric range operators.

For rolling windows, select only day-precision publication tokens in the window. Updated view filters update tokens instead, requires `has_update=yes`, and defaults to updated sort. Year/date range constraints apply to publication date unless the UI explicitly labels an “Updated between” field. Foundations applies `foundational=yes` without a default recent window.

**Important API behavior:** Pagefind requires an explicit `any` condition for OR within a facet. A plain array uses AND. Blank-query filtering uses `null`. [Filtering API](https://pagefind.app/docs/js-api-filtering/).

Our adapter translation:

```typescript
const filters = {
  status: { any: selectedStatuses.length ? selectedStatuses : ["active"] },
  ...(areas.length ? { area: { any: areas } } : {}),
  ...(sources.length ? { source: { any: sources } } : {}),
  ...(topics.length ? { topic: { any: topics } } : {}),
  ...(periodsConstrained ? { publication_period: { any: allowedPeriods } } : {}),
};
// Before calling Pagefind: a constrained date set with zero matching tokens
// returns zero locally. Do not pass an empty array and hope it means no match.
```

Apply the same translation to venue/kind/status. Omit a facet only when unconstrained. An invalid or valid-but-empty constraint must not accidentally become “all papers.” Avoid claiming per-option dynamic counts until their semantics are tested; the main result total is mandatory.

Sorting policy: the exporter computes three stable rank integers for every paper: `rank_newest`, `rank_oldest`, `rank_updated`, from section 12's deterministic sort. Each rank is unique within the release and includes unknown-date papers at the end. Pagefind stores all three as sort attributes. Request one sort key ascending; no multi-key sort or numeric overflow trick is needed. Relevance omits explicit sorting. [Sorting API](https://pagefind.app/docs/js-api-sorting/).

Exact identifier search: if `q` parses unambiguously as a complete supported source ID/URL or DOI, normalize it and apply the `identifier` facet with a null text query. Unknown identifier -> zero matches with the original query displayed. Otherwise use normal text search. Do not classify ordinary words as identifiers.

`SearchService` contract:

```typescript
interface SearchRequest {
  query: string;
  filters: ParsedFilterState;
  sort: "newest" | "oldest" | "updated" | "relevance";
  page: number;
  pageSize: 25;
  todayUTC: string;
}
interface SearchResult {
  releaseId: string;
  total: number;
  page: number;
  pageSize: 25;
  items: PaperCardData[];
}
interface SearchService {
  search(request: SearchRequest): Promise<SearchResult>;
}
```

The adapter filters before slicing results, obtains the correct total, and hydrates only the requested 25 items. Do not hydrate all matches and then paginate. Fetch detail JSON only where needed, using bounded concurrency (initially 6) and a small in-memory cache keyed by release ID + work ID. An older in-flight query must not overwrite a newer result: use a monotonically increasing query sequence number. Debounce free-text typing 200 ms; filter clicks apply immediately; Back/Forward rehydrates state without another history entry.

For no-JavaScript reading, pre-render the initial page, archive-year pagination, topic pages, foundations, and detail pages. Dynamic search may require JavaScript, with a clear fallback message and links to the static archive. Do not require Pagefind for reading a paper.

Build index output using the documented Node API and fail on reported indexing errors. Do not invent optional arguments or assume `npm run dev` automatically serves a production search index. Provide a `web-preview` command that builds and serves the real static output. [Node indexing API](https://pagefind.app/docs/node-api/).

## 15. UI specification with component responsibilities

Use these components:

| Component | Responsibility |
|---|---|
| `SiteHeader.astro` | Working title, one-sentence scope, About/Coverage/Status/RSS links |
| `FreshnessBanner.astro` | Last successful source refreshes, partial/stale state, preview label |
| `SearchPanel.astro` + controller | Query, area tabs, advanced filter controls, URL state |
| `PaperCard.astro` | Compact title/authors/date/tags/source links and expandable abstract |
| `PaperList.astro` | Result count, sort, ordered cards, accessible loading/empty state |
| `Pagination.astro` | Previous/next and current page; normal anchor links for static pages |
| `PaperDetail.astro` | Full metadata, sources, citations, evidence, related works, update history |
| `CoverageTable.astro` | Source/venue/year progress and limitations without invented percentages |
| `CitationButton` | Copy/download BibTeX; visible success/failure feedback |

Astro components render on the build side. For dynamic search results use a small TypeScript `renderPaperCard()` DOM renderer with the same card DTO, CSS classes, text fields, and accessibility structure; do not attempt to run `.astro` components in the browser. Keep card content rules in shared formatting helpers and test parity on one representative card. Generate Astro detail/archive/topic routes through `getStaticPaths()` from the validated export. The client enhances or replaces only the results region after a successful search; pre-rendered content remains usable if initialization fails.

Layout:

```text
Cryptographic Proofs Research Index          About · Coverage · Status · RSS
Proof systems, security proofs, and formal verification
Updated: <actual date>                 <source freshness/preview indicator>

[ Search titles, authors, abstracts, or paper IDs                         ]
[All] [Proof systems] [Security proofs] [Formal verification]
[New] [Updated] [Archive] [Foundations]       [Filters]
<active filter chips with remove buttons>

<N papers>                           <date window>  <sort>
------------------------------------------------------------------------
Paper title
Authors · original publication date · venue if known
<area/topic badges> <contribution label>
ePrint · arXiv · Publisher · PDF · Code · Cite         [Show abstract]
------------------------------------------------------------------------
...
[Previous]                    Page 2 of 8                       [Next]
```

CSS tokens: background `#f7f8fb`, surface `#ffffff`, text `#172033`, muted `#526174`, border `#d9dfeb`, accent `#2447a8`; test contrast rather than assuming it. Proof-system badge blue, security badge purple, formal-verification badge teal, each with text labels. Use system sans-serif for body and system monospace for source IDs. Minimum body size 16px, metadata 14px, line height around 1.5. Main width 1040px, page padding 24px desktop/16px mobile, card gap 12px, card padding 16px, border radius 8px. Small deviations for actual visual fit are fine; do not redesign the information architecture.

At <=720px, stack controls and move advanced filters into an accessible disclosure/drawer with labeled inputs, focus handling, and an Apply button. Preserve selected filters on close. Avoid a drawer dependency if a native `<details>` panel is sufficient. Titles wrap; source links wrap; no horizontal scrolling for cards.

Title links go to canonical detail pages. Sources and PDFs are explicit links with descriptive labels. Extracted code links say “Code link” with provenance in details; only verified author-provided links may say “Official code.” Publication status badges must not imply peer review unless publication evidence exists.

Math rendering uses trust-disabled KaTeX. On unsupported syntax display escaped original notation; never remove a title because math rendering failed. Render excerpts as plain text in v1; do not inject arbitrary provider or search-highlight HTML.

State copy:

- Empty: “No papers match these filters.” Include Clear filters.
- Search index failure: “Search could not load. Retry or browse the archive.”
- Partial source freshness: “Some sources have not refreshed successfully. Previously indexed papers remain available.”
- Stale dataset: “This index was last refreshed on <date>.” Show elapsed age.
- Abstract absent: “Abstract unavailable from the indexed sources.”
- Approximate date: “1985 · publication year only.”
- Fixture mode: “Preview dataset — not the live research index.”

Announce result changes through an appropriate polite live region, preserve input focus, and use buttons/inputs instead of clickable spans. Topic color is supplementary, not the only signal.

## 16. Publication, health, backup, and recovery

### 16.1 Release transaction boundary

Database state is durable independently of publication. A validated export is immutable. Astro consumes exactly one export; a failed build does not mutate that export or the deployed release. A deployment points to a completed artifact only after checks succeed.

Store immutable release assets under a versioned path such as `/assets/releases/<release-id>/`. Copy the current release and, within hosting limits, the previous two releases' data/search assets into a deployment so already-open pages can finish requests. A missing old asset produces a Reload message; never silently load an incompatible new asset into an old page. Canonical paper URLs remain stable and point to the current release's data.

Respect `PUBLIC_BASE_PATH` in every asset/feed/canonical URL; test both `/` and `/proof-papers/`. Static deployment must work at a repository subpath. Do not hard-code root-relative paths in every component.

### 16.2 Workflows

`checks.yml`: install locked dependencies, provision disposable PostgreSQL, migrate, run unit/integration/type checks, create fixture export, validate it, and build the real web/search output. No routine live-provider harvesting and no production credentials in pull-request jobs.

`refresh.yml`: daily at 06:17 UTC plus manual trigger; run bounded syncs, classification, catalog validation, coverage, release export/build, and authorized deployment. Process each source with its own failure summary. A source failure can leave that source stale while a successfully completed source advances; do not replace failed-source data with empty output. A pending partial promotion blocks a new data snapshot until repaired. Record useful diagnostics even when later steps cannot publish.

`backfill.yml`: manual source/stream/run selectors and maximum job time; do not pass user text directly into shell commands. Use validated choices or structured arguments. Share provider/catalog locks with refresh. Each job commits progress well before the workflow timeout. Resume until the declared streams are complete.

`deploy.yml`: build from an identified validated export or deploy an already-built artifact. Use the hosting platform's supported deployment action/API. Do not rely on a generated commit triggering another workflow.

Before enabling live jobs, validate production provider connectivity, database/storage access, least-privilege credentials, and current action/dependency versions. Avoid speculative spending; local implementation does not require buying a domain or provisioning a paid service.

### 16.3 Public and maintainer health

Public status shows data as known at the last published release: last attempt, last success, coverage, source errors in sanitized language, and dataset as-of. Client-side age calculation can show that a static release has become stale even when no later deployment succeeds. Do not claim that an old static status page knows the current provider failure cause.

Maintainer diagnostics retain current run results outside the last successful frontend build (workflow summaries and structured run records). If a production monitor is configured, notify only through an authorized channel on repeated failure or >48h stale source state, not on every unchanged run. A standalone public health endpoint is optional and must be clearly separated from catalog-release freshness if added.

### 16.4 Backup and restore

Nightly database backups plus canonical JSONL exports and release manifests go to versioned storage. Local backend uses a directory outside the source tree's tracked files; production uses private object storage. Retain source raw evidence for all known problematic runs and adopt an explicit retention policy for routine raw data. Do not garbage-collect data needed to replay pending runs or audit curation.

A restore drill creates a new disposable database, applies/restores the correct schema, restores the catalog, validates FK/count/hash invariants, and regenerates an export. Compare semantic catalog hashes while excluding expected new generation timestamps/release IDs. Never test restoration by overwriting a user's live database.

Rollback deploys a previously validated static artifact. Database rollback is a separate operation; do not automatically downgrade the database merely to revert the website. Forward-compatible release schemas and preserved artifacts make reader-facing rollback straightforward.

## 17. Initial taxonomy IDs

Implement these IDs so pipeline, URLs, curation, search, and UI share the same vocabulary. Display names may change without changing IDs. A topic can appear under multiple primary areas when evidence supports both.

| Topic ID | Display name | Normal primary area(s) |
|---|---|---|
| `zero-knowledge` | Zero knowledge and knowledge proofs | proof-systems |
| `succinct-proofs` | Succinct proofs and arguments | proof-systems |
| `interactive-foundations` | Interactive proofs, PCPs, and IOPs | proof-systems |
| `commitments` | Commitments and inner-product arguments | proof-systems |
| `arithmetization` | Arithmetization and lookups | proof-systems |
| `recursion` | Recursion, folding, and accumulation | proof-systems |
| `alternative-constructions` | MPC/VOLE-in-the-head and other constructions | proof-systems |
| `proving-systems` | zkVMs, compilers, and proving infrastructure | proof-systems |
| `verifiable-computation` | Verifiable computation and applications | proof-systems |
| `security-definitions` | Security definitions and models | security-proofs |
| `security-reductions` | Security proofs and reductions | security-proofs |
| `composability` | Composition and universal composability | security-proofs |
| `protocol-analysis` | Cryptographic protocol analysis | security-proofs |
| `cryptanalysis` | Attacks, counterexamples, and proof gaps | security-proofs |
| `quantum-security` | Quantum security analysis | security-proofs; proof-systems when applicable |
| `formal-protocol-verification` | Formal protocol verification | formal-verification |
| `mechanized-proofs` | Mechanized cryptographic proofs | formal-verification |
| `verified-implementations` | Verified cryptographic implementations | formal-verification |
| `computational-soundness` | Computational soundness | formal-verification; security-proofs |
| `privacy-applications` | Privacy and authentication applications | evidence-dependent |

Contribution IDs are exactly `construction-theory`, `analysis-attack`, `implementation-optimization`, `application`, `survey-tutorial`. The taxonomy validator rejects unknown IDs unless they are added through a versioned configuration change. Do not use display strings as database/API identifiers.

## 18. Test fixtures and acceptance scenarios

Create small, purposeful fixtures rather than huge snapshots. The fixtures below are **synthetic test cases**, not bibliographic claims. Keep them under `tests/fixtures/synthetic`; fixture-mode exports must never pass the production-live deployment guard.

### 18.1 Classification and identity dataset

| Fixture | Synthetic content / relationship | Expected result |
|---|---|---|
| F01 | “Zero-knowledge proofs for private arithmetic computations”; crypto abstract; authors A/B; ePrint record | Included, proof-systems, zero-knowledge |
| F02 | arXiv cross-post of F01 with an explicit ePrint identifier and same authors | Same canonical work as F01; both source filters match one result |
| F03 | Version 2 of F02 after an earlier baseline, changed abstract and supported update date | One work, one supported revision event, unchanged original publication |
| F04 | “Security proofs for key encapsulation mechanisms”; reduction and encryption context | Included, security-proofs |
| F05 | “Formal verification of authentication protocols with Tamarin”; cryptographic context | Included, formal-verification; other areas only if their rules also match |
| F06 | “Nova observations in nearby galaxies”; astronomy abstract | Excluded |
| F07 | “Lean proofs of elementary number theoretic identities”; no crypto context | Excluded |
| F08 | “A generic verification approach”; missing abstract; cryptographic venue evidence only | Review; venue alone is not enough for automatic inclusion |
| F09 | Curated historical ZK record, year 1985, no abstract, foundational=true, verified evidence field in fixture | Included by curation; archive/foundations; no invented day/PDF |
| F10 | Similar title to F01 but different authors and explicit different publication evidence | Separate work or identity review; no automatic merge |
| F11 | Metadata containing literal `<script>` text and a `javascript:` link | Text safely displayed; unsafe link rejected; no script execution |
| F12 | Included work with no known publication date | Searchable; unknown-date handling; excluded from recent windows |
| F13 | Work carrying both proof-systems and security-proofs labels | Selecting both areas with OR returns it once |
| F14 | Older paper first harvested today | Archive; no current publication/update feed spam |
| F15 | Formal tool mentioned incidentally in unrelated programming-language text | No automatic formal-crypto inclusion; exclude/review according to actual crypto evidence |
| F16 | Withdrawn source record with a still-active counterpart | Canonical work retains active counterpart and displays source withdrawal information |

Freeze source/run timestamps in fixtures. Build F03 by first importing F02, then applying the later response. Do not test revision logic by importing a lone v2 record and assuming that creates an observed revision transition.

### 18.2 Required behavioral tests

Give tests descriptive names such as these; they specify behavior, not a particular implementation:

```text
test_oai_no_records_match_is_successful_empty_window
test_oai_protocol_error_does_not_advance_watermark
test_repeated_resumption_token_fails_completeness_check
test_expired_token_replays_window_without_duplicate_works
test_partial_page_write_resumes_from_last_durable_cursor
test_partial_promotion_blocks_export_until_repaired
test_rerun_of_identical_fixture_does_not_create_update_event
test_older_backfill_cannot_replace_newer_current_observation
test_undated_A_B_A_reversion_is_retained_once
test_arxiv_atom_and_oai_share_source_identity
test_old_arxiv_identifier_preserves_archive_prefix
test_title_author_match_requires_unique_candidate_and_no_conflicts
test_shared_proceedings_doi_does_not_merge_unrelated_papers
test_manual_split_survives_reclassification_and_resolution
test_stale_curation_revision_rejects_write
test_merge_aliases_are_acyclic_and_resolve_to_live_work
test_revert_preserves_later_independent_source_observations
test_year_only_date_is_not_fabricated_as_january_first
test_recent_window_uses_zero_through_six_days
test_future_date_does_not_enter_recent_results
test_missing_abstract_is_not_automatic_exclusion
test_security_proof_and_formal_crypto_examples_are_in_scope
test_source_added_does_not_reset_original_publication
test_new_and_updated_feeds_have_stable_unique_ids
test_facet_OR_with_cross_facet_AND
test_constrained_empty_date_token_set_returns_zero
test_partial_date_range_uses_documented_overlap_semantics
test_unknown_dates_sort_last_in_both_directions
test_search_hydrates_only_requested_page
test_late_search_response_cannot_replace_current_results
test_url_state_round_trip_and_browser_back
test_non_root_base_path_resolves_assets_feeds_and_links
test_fixture_release_is_rejected_by_live_deploy_guard
test_public_export_excludes_internal_secrets_and_error_traces
test_bibtex_escapes_special_characters_and_preserves_author_order
test_untrusted_title_and_link_are_safe
test_release_mismatch_requests_reload_not_silent_merge
test_restore_rebuild_preserves_canonical_ids_and_semantic_hash
```

For parser fixtures, save real source responses when access is available, retaining provenance and sanitizing only sensitive headers (normally none for public metadata). Separately construct malformed XML/protocol-edge fixtures. Do not label synthetic XML as captured live evidence.

Database integration tests must use PostgreSQL and actual transactions/migrations, not only mocked repository methods or SQLite. Tests run in a disposable database with a clear name and never against `DATABASE_URL` for a live deployment. Inject failure at selected page/promotion boundaries to prove recovery.

Browser checks: 1280px desktop and 390px mobile layouts; keyboard-only filters/abstract disclosure/citations; loading/no-results/stale/partial states; real production Pagefind assets; no JavaScript error during search/pagination; working canonical/source/citation links; no horizontal overflow. If browser automation is unavailable, keep tests ready and report that limit rather than claiming screenshots or accessibility verification.

## 19. Task schedule for Sol

Work in this order. Each task has a narrow deliverable and a gate; use the gate as the stopping point for that implementation increment, then continue to the next unblocked task. A gate is not a new user approval request. Do not repeat passed checks without a code change or a new failure that justifies it.

| Task | Read | Implement | Gate / evidence |
|---|---|---|---|
| T00 Workspace and dependency baseline | 0–4 | Inspect existing files/instructions, create project directory if needed, record runtime choices and task status | Existing user files preserved; package-manager/runtime checks recorded; no application-complete claim |
| T01 Packaging and local infrastructure | 2–4 | pyproject, uv lock, minimal CLI, settings, Compose, env example, Makefile | `doctor`, CLI help, local database connectivity; package imports without network calls |
| T02 Shared schemas and pure normalization | 5, 12, 17 | Pydantic models, date/identifier/author/text/link normalization, injected clock | Unit tests for date boundaries, old IDs, missing fields, safe links |
| T03 Database migrations and repository | 6 | Tables, constraints, indexes, transaction helpers, test database setup | Migrate empty PostgreSQL; FK/uniqueness tests; clean rollback of failing transaction |
| T04 Blob storage and HTTP | 7 | Local/S3-compatible BlobStore, content hashes, HTTP limits, rate locks | Local put/get/hash tests; simulated HTTP retry/status tests; no secret leakage |
| T05 Source contracts and parsers | 7, 9 | Bounded live verification where available, fixtures, ePrint/arXiv parser implementations | Mapping document distinguishes live facts from assumptions; parser error cases pass |
| T06 Resumable ingestion runner | 8 | Run/page staging, durable cursors, promotion batches, watermark transitions | Injected interruption/replay/token-expiry tests pass; incomplete runs cannot publish |
| T07 Classification | 10, 17 | Versioned rule loader, evidence groups, decision engine, per-source classifications | F01/F04/F05 included; F06/F07 excluded; missing-abstract/review policy works |
| T08 Identity and canonical work projection | 11.1–11.2 | Conservative candidates, attachments, canonical field provenance, public IDs | F01/F02 merge; F10 does not; identical rerun preserves IDs |
| T09 Events and curation | 11.3–11.4 | Revision/publication events, YAML command validation/apply/revert, aliases and split constraints | F03 update semantics; manual merge/split/revert tests and stale-write conflict checks |
| T10 Historical adapters and enrichment | 9 | DBLP streaming import, Crossref enrichment, curated import, venue registry | Small real/synthetic bounded import tests; dump restart/correction behavior; unknown totals preserved |
| T11 Release export and schema contract | 13 | Snapshot lock/projection, public files, schema export, checksums, validators | Valid fixture release; no internal secret fields; IDs/counts/hashes consistent |
| T12 Static reading UI | 15 | Header, cards, paper detail, archive pagination, topics, foundations, responsive styles | Working preview from T11; all primary pages usable without dynamic search |
| T13 Search index and controller | 12, 14 | Canonical-only Pagefind index, rank keys/date tokens, URL/filter/query adapter | Real index tests for OR/AND, dates, source dedup, unknown dates, exact ID, pagination |
| T14 Reader utility pages and citations | 13, 15 | BibTeX, Atom feeds, About, coverage/status, inclusion evidence, math fallback | Export parsing/escaping; stable feed IDs; accurate absent/stale/withdrawn states |
| T15 Automated CI and recovery | 16, 18 | checks/refresh/backfill/deploy workflows, backup/restore commands | CI-equivalent local checks pass; restore drill preserves semantic catalog |
| T16 End-to-end real sample | 5–16 as needed | Run bounded real preprint + older bibliography through the complete path | Real-source records visible with provenance; identify any provider blocker honestly |
| T17 Full historical import | 8–9, 16 | Run resumable declared-source imports, DOI enrichment, foundational collection; update coverage ledger | Available declared snapshots complete or explicitly blocked/partial; no arbitrary age truncation |
| T18 Quality evaluation and tuning | 10.4 | Stratified labels, independent-review distinction, precision/recall/retrieval audits, error-driven rule changes | Reproducible report with actual denominators, review workload, and limitations |
| T19 Final performance and browser checks | 12–16, 18 | Full-corpus build/search benchmarks, browser behavior, mobile/keyboard verification | Measured budgets and screenshots where available; failures fixed or clearly unresolved |
| T20 Authorized deployment and handoff | 16, 21–22 | Select configured deploy adapter, publish when authorized, verify result, finish runbook/status | Correct dataset/release live or explicit local-only handoff; no false claims of unrun backfill/review/deployment |

T12 can proceed from fixtures while live-source verification is blocked; T17 can run in resumable slices alongside independent documentation/UI work without concurrent catalog writes. Preserve provider throttles. Do not let this flexibility bypass the correctness gates for promotion, export, or publication.

Use this status-file structure:

```markdown
# Implementation status
Current task: T06
Last completed task: T05
Confirmed decisions: broad scope; public/no accounts; full history

| Task | State | Evidence | Remaining |
|---|---|---|---|
| T05 | partial | Parser fixtures pass; arXiv sample retrieved | ePrint live endpoint blocked |

## Current interfaces
- Public schema version: 1
- Rules version: rules-v1
- Database migration head: <actual revision>

## Next actions
1. Implement interrupted-page replay test.
2. Complete promotion runner.

## External blockers
- <specific service/action, observed failure, independent work still possible>
```

Do not copy the example's status as a real result. `done` means the task's gate passed; `partial` means work remains. Record exact commands and concise outputs in a verification report, not a fabricated progress percentage.

## 20. Troubleshooting decisions already made

| Symptom | Correct next action | Do not do |
|---|---|---|
| ePrint 403 | Record bounded failed verification, inspect current official guidance, continue parser/fixture/other-source work | Repeated aggressive retries, bypass access controls, claim successful live ingestion |
| OAI HTTP 200 but zero records | Inspect protocol errors and terminal markers; distinguish `noRecordsMatch` | Overwrite old catalog with an empty dataset |
| OAI token expired | Restart the safe incomplete window and replay idempotently | Advance watermark past unprocessed pages |
| Database unavailable locally | Check existing service/container availability; document missing prerequisite | Substitute SQLite and claim production transaction tests passed |
| Search works in dev but not static preview | Verify actual generated index path, base path, and matching release assets | Ship a UI backed by a development-only mock search |
| Selecting two sources returns almost nothing | Check explicit OR translation inside source facet | Treat plain Pagefind arrays as OR |
| Year-only historical papers disappear | Check date-precision semantics and sort attributes on every record | Invent exact dates to make the filter pass |
| Backfill times out | Resume bounded staged work; preserve source snapshot identity | Add an arbitrary paper/year cap and call history complete |
| Related papers merge incorrectly | Use review/split and persist separation overrides; tighten evidence | Add a permanent special-case title regex with no evidence |
| Classifier review backlog is large | Report workload, improve evidence-based rules, preserve uncertain records | Relabel all review cases as excluded to inflate precision |
| Pagefind performance fails measured full-corpus budget | Profile payload/index costs; document evidence; use read-only PostgreSQL search if required | Remove historical data or add an unneeded vector database |
| Production credentials absent | Finish runnable local project, release artifact, configuration/runbook | Invent secrets, buy services, or claim deployed status |
| Independent human labels absent | Mark agent labels provisional and provide reviewable evaluation files | Claim human-validated precision/recall |

## 21. Completion checklist

Check each item against evidence. The project is not “fully complete” if a material item remains partial.

- [ ] User-confirmed broad scope survives in taxonomy, fixtures, filters, and actual records.
- [ ] No required reader account or paid AI API dependency.
- [ ] All source adapters are implemented; live-tested status is recorded per adapter.
- [ ] Current and historical source records retain provenance and partial dates.
- [ ] Full available declared-source imports ran; coverage distinguishes incomplete/unavailable sources.
- [ ] Verified foundational collection exists across the three areas.
- [ ] Repeated/interrupted ingestion preserves records, versions, and checkpoints.
- [ ] Identity resolution is conservative; corrections and splits survive later automatic runs.
- [ ] Search works against the production index with tested filters, dates, sorting, and URLs.
- [ ] Detail/archive/foundations pages, citations, feeds, coverage, and source status work.
- [ ] Static pages and assets work at root and repository subpaths.
- [ ] Fixture data cannot masquerade as the live corpus.
- [ ] Catalog export/build/deployment are validated and recoverable.
- [ ] Backup restore/rebuild was executed against a disposable target.
- [ ] Real counts, evaluation provenance, and performance measurements are reported accurately.
- [ ] Browser checks ran where available; unavailable verification is disclosed.
- [ ] Remaining deployment/provider/editorial blockers are named precisely.

A staged rollout is allowed: a clearly labeled real-data preview can be useful before history is fully imported. It must display coverage and not claim that full historical work is finished. Do not silently redefine “complete” to mean “scripts exist.”

## 22. Final handoff format

Give the user a concise outcome statement and links/paths to:

1. Running local preview and authorized deployed URL, if any.
2. Repository and setup README.
3. Current release manifest and actual paper/source/area/era counts.
4. Test/evaluation/coverage reports, with provisional versus independently reviewed labels distinguished.
5. Operational runbook for sync, backfill, curation, backup, and rollback.
6. Remaining work and the exact next executable task where an external blocker remains.

Do not merely paste an implementation plan back to the user. The implementation run must produce and verify the software.

## 23. External facts and documentation boundary

Most of this document is **our engineering specification**, not a claim that a library already implements the application behavior. Verify library method signatures against installed versions; preserve the behavior contracts when adapting syntax.

Known unresolved source fact: direct ePrint XML inspection returned HTTP 403 in the planning environment. Its original/update/version fields must be verified against an accessible response before claiming live-source correctness. No prompt can remove this external prerequisite.

Official references:

- [ePrint harvesting](https://eprint.iacr.org/rss/)
- [arXiv Atom API](https://info.arxiv.org/help/api/user-manual.html), [OAI-PMH](https://info.arxiv.org/help/oa/index.html), [access guidance](https://info.arxiv.org/help/api/tou.html)
- [DBLP bulk data](https://dblp.org/faq/How+can+I+download+the+whole+dblp+dataset.html), [search API](https://dblp.org/faq/How+to+use+the+dblp+search+API.html)
- [Crossref API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [Astro](https://docs.astro.build/), [Pagefind](https://pagefind.app/docs/), [PostgreSQL](https://www.postgresql.org/docs/current/)
- [GitHub workflow scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

The handoff structure uses explicit execution scope, bounded verification, and persistent progress records, consistent with [official OpenAI prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6.1-sol). It does not depend on a particular Sol version, API configuration, or an unverified claim about comparative model capability. Use the Sol model selected by the user; do not switch it automatically.
