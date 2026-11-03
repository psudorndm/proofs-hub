// Public release contract (schema v1). Mirrors pipeline/src/proof_index/models/public.py.
// Runtime validation uses the JSON Schemas exported from the Python models
// (src/lib/schema/*.json); tests check these types stay aligned with them.

export type Area = "proof-systems" | "security-proofs" | "formal-verification";
export type DatePrecision = "day" | "month" | "year" | "unknown";
export type Provider = "eprint" | "arxiv" | "dblp" | "crossref" | "curated";
export type PaperStatus = "active" | "withdrawn" | "unavailable";

export interface PartialDate {
  year: number | null;
  month: number | null;
  day: number | null;
  precision: DatePrecision;
}

export interface PublicSource {
  provider: Provider;
  source_id: string;
  landing_url: string | null;
  pdf_url: string | null;
  version: string | null;
  status: "active" | "withdrawn" | "deleted";
}

export interface PublicUpdate {
  kind: "source_revision" | "metadata_updated" | "withdrawn" | "restored";
  date: string;
  date_basis: "source" | "observed";
  source_record_id: string | null;
}

export interface PublicPaper {
  schema_version: 1;
  release_id: string;
  id: string;
  title: string;
  authors: { display_name: string; orcid: string | null }[];
  abstract: string | null;
  published: PartialDate;
  areas: Area[];
  topics: string[];
  contributions: string[];
  foundational: boolean;
  status: PaperStatus;
  metadata_issues: string[];
  sources: PublicSource[];
  publications: { venue_key: string | null; venue_name: string | null; year: number | null; doi: string | null }[];
  code_links: { url: string; origin: string; verified_official: boolean }[];
  latest_update: PublicUpdate | null;
  inclusion: {
    review_status: "automatic" | "agent-provisional" | "human-reviewed";
    explanation: string;
    evidence: { field: string; snippet: string; rule_id: string | null }[];
  };
  related_work_ids: string[];
  canonical_path: string;
  bibtex_path: string;
  history: { kind: string; date: string | null; date_basis: "source" | "observed" | "none"; source: string | null }[];
}

export interface PaperCardData {
  id: string;
  title: string;
  authors: string[];
  published: PartialDate;
  areas: Area[];
  topics: string[];
  contributions: string[];
  foundational: boolean;
  status: PaperStatus;
  sources: PublicSource[];
  venue: string | null;
  doi: string | null;
  has_code: boolean;
  has_abstract: boolean;
  latest_update: PublicUpdate | null;
  canonical_path: string;
  bibtex_path: string;
  json_path: string;
}

export interface ListPage {
  release_id: string;
  list: string;
  total: number;
  page: number;
  page_count: number;
  page_size: 25;
  items: PaperCardData[];
  as_of_date: string | null;
  window_days: number | null;
}

export interface SourceSummary {
  provider: string;
  last_attempt_at: string | null;
  last_success_at: string | null;
  completed_watermark: string | null;
  last_error_code: string | null;
  coverage_state: string;
  streams: string[];
}

export interface Manifest {
  schema_version: 1;
  release_id: string;
  dataset_mode: "live" | "fixture";
  generated_at: string;
  catalog_snapshot_at: string;
  code_revision: string;
  rules_version: string;
  source_config_hash: string;
  included_work_count: number;
  source_record_counts: Record<string, number>;
  sources: SourceSummary[];
  available_years: string[];
  topic_counts: Record<string, number>;
  coverage_status: "complete" | "partial" | "not_started";
  page_size: 25;
  public_base_path: string;
  as_of_date: string;
  site_title: string;
  site_description: string;
  public_site_url: string;
}

export interface Taxonomy {
  version: string;
  areas: { id: Area; display_name: string }[];
  topics: { id: string; display_name: string; areas: Area[] }[];
  contributions: { id: string; display_name: string }[];
}

/** Keys of PublicPaper, checked against the exported JSON Schema in tests. */
export const PUBLIC_PAPER_KEYS = [
  "schema_version", "release_id", "id", "title", "authors", "abstract", "published", "areas",
  "topics", "contributions", "foundational", "status", "metadata_issues", "sources",
  "publications", "code_links", "latest_update", "inclusion", "related_work_ids",
  "canonical_path", "bibtex_path", "history",
] as const;

export const PAPER_CARD_KEYS = [
  "id", "title", "authors", "published", "areas", "topics", "contributions", "foundational",
  "status", "sources", "venue", "doi", "has_code", "has_abstract", "latest_update",
  "canonical_path", "bibtex_path", "json_path",
] as const;
