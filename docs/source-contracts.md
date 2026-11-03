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

## DBLP, Crossref, curated

Mappings are added with T10.
