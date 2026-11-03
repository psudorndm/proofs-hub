# Source contracts

For each provider: endpoint, access record, parser version, and field mappings.
Every mapping is labeled **Live-verified** (checked against a captured live
response, with the capture referenced) or **Assumption** (from published
documentation or format knowledge, not yet checked live).

**As of 2026-11-03, no mapping is live-verified.** This build environment's egress
policy refuses connections to every provider host (proxy CONNECT 403). The bounded
verification reports are in `work/verification/` (not committed; regenerate with
`proof-index sources verify --source <eprint|arxiv> --bounded`). A 403 from the
sandbox proxy says nothing about how the providers themselves respond.

## Access log

| Date (UTC) | Provider | Endpoint | Command | Result |
|---|---|---|---|---|
| 2026-11-03 | eprint | `https://eprint.iacr.org/oai` (Identify, ListMetadataFormats, ListRecords `from=2026-11-01`) | `sources verify --source eprint --bounded` | Proxy refused (403); no provider response; exit 3 |
| 2026-11-03 | arxiv | `https://oaipmh.arxiv.org/oai` (Identify, ListMetadataFormats, ListSets, ListRecords `set=cs`), `https://export.arxiv.org/api/query` | `sources verify --source arxiv --bounded` | Proxy refused (403); no provider response; exit 3 |

## Shared OAI-PMH 2.0 protocol (`adapters/oai.py`)

Assumption (OAI-PMH 2.0 specification), exercised only with synthetic fixtures:

- Namespace URI `http://www.openarchives.org/OAI/2.0/`; prefixes are never assumed.
- Root must be `OAI-PMH` with a `responseDate`. HTML pages, wrong roots, truncated
  XML, a bare XML declaration, and any DOCTYPE are page-structure failures (exit 4)
  even when HTTP status is 200.
- `<error code="noRecordsMatch">` alone → successful empty terminal page.
- `badResumptionToken` → `ExpiredCursor`; the runner replays the window (T06).
- `badArgument`, `badVerb`, `cannotDisseminateFormat`, `idDoesNotExist`,
  `noMetadataFormats`, `noSetHierarchy`, and unknown codes → protocol failure (exit 3).
- `resumptionToken` text → next cursor; an empty/absent token → terminal page.
  `completeListSize` → `reported_total` only when the provider supplies it.
- `header/@status="deleted"` → `Tombstone` (no invented bibliographic fields).
- Header `datestamp` → `harvest_datestamp` (bookkeeping; excluded from content hash).
- Incremental `from` = last completed boundary minus `overlap_days` (2). `until` is
  not sent unless a stream sets one. Granularity is configured per stream
  (`YYYY-MM-DD`) until Identify confirms the provider's granularity.

## IACR ePrint (`adapters/eprint.py`, parser `eprint-oai-dc-v1`)

Endpoint: `https://eprint.iacr.org/oai`, `metadataPrefix=oai_dc`. Official
harvesting page: https://eprint.iacr.org/rss/ (not reachable from here).

| Normalized field | Path | Status |
|---|---|---|
| `source_id` | `header/identifier` (`oai:eprint.iacr.org:YYYY/NNNN`), fallback `dc:identifier` URL; normalized `2020/0007 → 2020/7` | Assumption |
| `title` | first `dc:title` | Assumption |
| `authors` | ordered `dc:creator` display strings (never split) | Assumption |
| `abstract` | first `dc:description` | Assumption |
| `published` | earliest parseable `dc:date` | **Assumption — high risk.** Whether `dc:date` holds submission, revision, or both is unverified |
| `source_modified_at` | latest `dc:date` when two or more distinct dates exist | **Assumption — high risk** |
| `source_version` | none (null) | Assumption: no explicit version evidence known |
| `doi` | any `dc:identifier`/`dc:relation` that normalizes as a DOI | Assumption |
| `links` | `dc:identifier`/`dc:relation` HTTP(S) URLs; `.pdf` → pdf, ePrint URL → landing, else other; unsafe URLs dropped | Assumption |
| `source_categories` | `dc:subject` values | Assumption |
| `status` | `active`, or tombstone when the header is deleted. No withdrawal signal is parsed | Assumption: ePrint withdrawal representation unknown |

Spec section 23 open item: ePrint original/update/version semantics must be
verified against an accessible response before claiming live-source correctness.

## arXiv OAI-PMH (`adapters/arxiv_oai.py`, parser `arxiv-oai-arXiv-v1`)

Endpoint: `https://oaipmh.arxiv.org/oai`, `metadataPrefix=arXiv`, namespace
`http://arxiv.org/OAI/arXiv/`.

| Normalized field | Path | Status |
|---|---|---|
| `source_id` | `metadata/arXiv/id`, fallback header identifier; no `vN` in this format | Assumption |
| `title`, `abstract` | `arXiv/title`, `arXiv/abstract` (whitespace collapsed) | Assumption |
| `authors` | `arXiv/authors/author`: `forenames keyname suffix`; `given`=forenames, `family`=keyname | Assumption |
| `published` | `arXiv/created` (v1 date, day precision) | Assumption |
| `source_modified_at` | `arXiv/updated` (midnight UTC; absent for single-version papers) | Assumption |
| `source_version` | null — the `arXiv` format exposes no version numbers (`arXivRaw` does; not used in v1) | Assumption |
| `doi` | first DOI in `arXiv/doi`; extra DOIs → `identifiers.doi` | Assumption |
| `identifiers.eprint` | explicit ePrint URL or "ePrint (Archive/Report) YYYY/NNNN" in comments/abstract | Our rule (not a provider field) |
| `source_categories` | `arXiv/categories` (space-separated; includes cross-lists) | Assumption |
| `links` | none from this format; landing URLs are derived from the identifier at publication time (D008) | Our rule |

**Set resolution.** Spec section 9 requires the exact set mapping for `cs.CR`,
`cs.CC`, `cs.DS`, `cs.LO`, `cs.PL`. Not yet resolved live. Configured assumption:
the enclosing `cs` set with a local filter on the record's category list (primary
or cross-listed). `sources verify --source arxiv` runs `ListSets` and reports
`category_set_resolution` (an exact `cs:cs:CR`-style set if present, otherwise the
enclosing archive set). If exact sets exist, new `:v2` streams should use them.

## arXiv Atom API (`adapters/arxiv_atom.py`, parser `arxiv-atom-v1`)

Endpoint: `https://export.arxiv.org/api/query`. Namespaces: Atom
`http://www.w3.org/2005/Atom`, OpenSearch `http://a9.com/-/spec/opensearch/1.1/`,
arXiv `http://arxiv.org/schemas/atom`.

| Normalized field | Path | Status |
|---|---|---|
| `source_id`, `source_version` | `entry/id` URL → base id + `vN` | Assumption |
| `title`, `abstract` | `entry/title`, `entry/summary` | Assumption |
| `authors` | `entry/author/name` (display only) | Assumption |
| `published` | `entry/published` (UTC date) | Assumption |
| `source_modified_at` | `entry/updated` | Assumption |
| `doi` | `arxiv:doi` | Assumption |
| `links` | `link[@rel=alternate]` → landing, `link[@title=pdf]` → pdf, `link[@title=doi]` → publisher | Assumption |
| `source_categories` | `category/@term` | Assumption |
| errors | an entry whose id contains `arxiv.org/api/errors` → protocol failure | Assumption |
| paging | `opensearch:totalResults`, `startIndex`; cursor `{query_index, start}`; startIndex mismatch or an empty page before the reported total → page failure | Our rule |

Supplemental queries (`arxiv:adjacent-terms:supplemental:v1`) cover `cs.IT`,
`cs.AI`, `cs.LG`, `quant-ph`, searching both `ti:` and `abs:` for the configured
terms. A query reporting more than `max_results_per_query` (30000, the documented
reachable ceiling — assumption) fails explicitly instead of truncating; a
date-partitioned stream version is then required.

Atom and OAI records share one source identity (`arxiv`, base id). The stream that
produced each observation is stored on the observation envelope (T06).

## DBLP dump (`adapters/dblp.py`, parser `dblp-xml-v1`)

Input: one official dump snapshot (`dblp.xml.gz` from https://dblp.org/xml/release/)
plus its `dblp.dtd`, passed as local files (`backfill --source dblp --dump --dtd`).
Not downloaded or processed in this environment (host blocked).

| Normalized field | Path | Status |
|---|---|---|
| record types | `article`, `inproceedings`, `incollection` kept; `proceedings`, `book`, `www`, theses, `data` counted as filtered | Assumption |
| `source_id` | `@key` (exact DBLP key) | Assumption |
| `title` | `title` with inline markup flattened; one trailing period removed | Assumption |
| `authors` | ordered `author`; `editor` never used; trailing 4-digit disambiguators removed | Assumption |
| `published` | `year` (year precision only) | Assumption |
| `source_modified_at` | `@mdate` | Assumption |
| `venue_key` / `venue_name` | key prefix (e.g. `conf/crypto`) / `journal` or `booktitle` | Assumption |
| `doi` | first `ee` resolving through doi.org | Assumption |
| `identifiers` | `ee` URLs to ePrint/arXiv → `eprint`/`arxiv` cross-references | Our rule |
| `status` | `@publtype="withdrawn"` → withdrawn | Assumption |

Candidate rule (spec section 9): a paper is staged when its venue prefix is a
`candidate` in `config/venues.yaml` **or** its title carries an explicit area-bearing
rule hit; otherwise it is counted as filtered. Ordinals count every top-level record
in document order; a resume re-streams and skips durable ordinals. A different dump
file (SHA-256) is a new snapshot; an unfinished run on the old dump is marked
`failed`/`superseded_snapshot`. DBLP supplies no total, so `expected_count` stays null.
Entities resolve only through the local `dblp.dtd`; other external references
resolve to nothing and the network is never used. `--huge-tree` lifts libxml2's
size limits if the official dump requires it (to be determined on a real dump).

## Crossref REST (`adapters/crossref.py`, parser `crossref-works-v1`)

Endpoint `https://api.crossref.org/works/{doi}`, one request per planned DOI.
`enrich-dois --max-records N` plans the DOIs of included/review works lacking a
Crossref record, stores the list on the run, and resumes that list. `--mailto` is
sent only when given explicitly.

| Normalized field | Path | Status |
|---|---|---|
| `source_id`, `doi` | `message.DOI` (must equal the requested DOI) | Assumption |
| `title` | `message.title[0]`, markup stripped | Assumption |
| `authors` | ordered `message.author` (`given`, `family`, `name`, `ORCID`); editors excluded | Assumption |
| `published` | earliest of `published-print`, `published-online`, `published`, `issued` | Spec rule |
| `source_modified_at` | `message.deposited.date-time` (ordering only, never a publication date) | Our rule |
| `venue_name` | `message.container-title[0]` | Assumption |
| `identifiers` | `relation` has-preprint/is-preprint-of/version DOIs and arXiv IDs | Assumption |
| `abstract` | not used (JATS abstract ignored in v1) | Our rule |
| 404 | item quarantine `doi_not_found`; run continues | Our rule |
| container types | `proceedings`, `journal`, `journal-issue`, ... → quarantine `container_doi` | Spec rule |

Crossref records never create works. They attach to the single work that already
holds the DOI when title/authors are compatible; otherwise an identity review
(`crossref_metadata_conflicts_with_work`) is queued and nothing is overwritten.

## Curated (`adapters/curated.py`, parser `curated-jsonl-v1`)

`curation/foundations.jsonl`, format of spec section 9. Validated in full before
staging (taxonomy IDs, safe evidence URLs, no `example.*` placeholders, unique slugs).
Source ID `curated:<slug>`; links are `origin: curated`. After promotion, an
`import_curated` curation command per entry sets inclusion, labels, and the
foundational flag with the entry's `review_kind`.
