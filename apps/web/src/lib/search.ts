// SearchService over Pagefind (spec sections 12 and 14).
//
// Pagefind semantics verified against the installed 1.5.2 build:
//   * {any: [...]} is OR within a facet; a plain array is AND.
//   * Facets combine with AND.
//   * A blank query must be passed as null.
//   * {any: []} matches EVERYTHING, so a constrained but empty set returns zero
//     locally and is never sent to Pagefind.
import type { PaperCardData, PublicPaper } from "./contracts";
import { ageDays } from "./dates";

export const PAGE_SIZE = 25 as const;
export const MULTI_KEYS = ["area", "topic", "source", "venue", "kind", "status"] as const;
export const VIEWS = ["new", "updated", "archive", "foundations"] as const;
export const SORTS = ["newest", "oldest", "updated", "relevance"] as const;
export const WINDOWS = ["7", "30", "365", "unknown"] as const;
const STATUSES = ["active", "withdrawn", "unavailable"];

type MultiKey = (typeof MULTI_KEYS)[number];
export type View = (typeof VIEWS)[number];
export type Sort = (typeof SORTS)[number];

export interface ParsedFilterState {
  q: string;
  area: string[];
  topic: string[];
  source: string[];
  venue: string[];
  kind: string[];
  status: string[];
  window: (typeof WINDOWS)[number] | null;
  year_from: number | null;
  year_to: number | null;
  date_from: string | null;
  date_to: string | null;
  exact_dates: boolean;
  code: boolean;
  view: View | null;
  sort: Sort | null;
  page: number;
}

export interface Vocabulary {
  area: string[];
  topic: string[];
  source: string[];
  venue: string[];
  kind: string[];
}

export interface DateToken { token: string; precision: string; start: string | null; end: string | null }

export interface SearchFacets {
  release_id: string;
  publication_tokens: DateToken[];
  update_tokens: DateToken[];
  values: Record<string, string[]>;
}

export function emptyState(): ParsedFilterState {
  return {
    q: "", area: [], topic: [], source: [], venue: [], kind: [], status: [], window: null,
    year_from: null, year_to: null, date_from: null, date_to: null, exact_dates: false,
    code: false, view: null, sort: null, page: 1,
  };
}

const INT = /^[0-9]{1,4}$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function validDate(value: string): boolean {
  const m = DATE.exec(value);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return y >= 1 && dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
}

/** Parse URL state strictly. Unknown keys are ignored; invalid values are errors. */
export function parseState(params: URLSearchParams, vocab: Vocabulary): { state: ParsedFilterState; errors: string[] } {
  const state = emptyState();
  const errors: string[] = [];
  state.q = (params.get("q") ?? "").trim().slice(0, 300);
  for (const key of MULTI_KEYS) {
    const values = [...new Set(params.getAll(key).map((v) => v.trim()).filter(Boolean))].sort();
    const allowed = key === "status" ? STATUSES : vocab[key as Exclude<MultiKey, "status">];
    const bad = values.filter((v) => !allowed.includes(v));
    if (bad.length) errors.push(`Unknown ${key}: ${bad.join(", ")}`);
    state[key] = values.filter((v) => allowed.includes(v));
  }
  const window = params.get("window");
  if (window) {
    if ((WINDOWS as readonly string[]).includes(window)) state.window = window as ParsedFilterState["window"];
    else errors.push("Date window must be 7, 30, 365, or unknown");
  }
  for (const key of ["year_from", "year_to"] as const) {
    const raw = params.get(key);
    if (raw === null || raw === "") continue;
    if (!INT.test(raw) || Number(raw) < 1) errors.push(`${key === "year_from" ? "From" : "To"} year must be a whole year between 1 and 9999`);
    else state[key] = Number(raw);
  }
  if (state.year_from !== null && state.year_to !== null && state.year_from > state.year_to) {
    errors.push("From year must not be after To year");
  }
  for (const key of ["date_from", "date_to"] as const) {
    const raw = params.get(key);
    if (raw === null || raw === "") continue;
    if (!validDate(raw)) errors.push(`${key === "date_from" ? "From" : "To"} date is not a real calendar date`);
    else state[key] = raw;
  }
  if (state.date_from && state.date_to && state.date_from > state.date_to) {
    errors.push("From date must not be after To date");
  }
  state.exact_dates = params.get("exact_dates") === "1";
  state.code = params.get("code") === "1";
  const view = params.get("view");
  if (view) {
    if ((VIEWS as readonly string[]).includes(view)) state.view = view as View;
    else errors.push("Unknown view");
  }
  const sort = params.get("sort");
  if (sort) {
    if ((SORTS as readonly string[]).includes(sort)) state.sort = sort as Sort;
    else errors.push("Unknown sort");
  }
  const page = params.get("page");
  if (page !== null && page !== "") {
    if (!/^[0-9]{1,6}$/.test(page) || Number(page) < 1) errors.push("Page must be a positive whole number");
    else state.page = Number(page);
  }
  return { state, errors };
}

/** Canonical serialization: fixed key order, sorted deduplicated multi-values, defaults omitted. */
export function serializeState(state: ParsedFilterState): string {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  for (const key of MULTI_KEYS) for (const value of [...new Set(state[key])].sort()) params.append(key, value);
  if (state.window) params.set("window", state.window);
  if (state.year_from !== null) params.set("year_from", String(state.year_from));
  if (state.year_to !== null) params.set("year_to", String(state.year_to));
  if (state.date_from) params.set("date_from", state.date_from);
  if (state.date_to) params.set("date_to", state.date_to);
  if (state.exact_dates) params.set("exact_dates", "1");
  if (state.code) params.set("code", "1");
  if (state.view) params.set("view", state.view);
  if (state.sort) params.set("sort", state.sort);
  if (state.page > 1) params.set("page", String(state.page));
  return params.toString();
}

/** Changing any filter resets the page to 1. */
export function withChange(state: ParsedFilterState, change: Partial<ParsedFilterState>): ParsedFilterState {
  const next = { ...state, ...change };
  if (!("page" in change)) next.page = 1;
  return next;
}

// --- exact identifier queries ---------------------------------------------------------------

const EPRINT = /^(?:https?:\/\/(?:www\.)?eprint\.iacr\.org\/|eprint:\s*|iacr:\s*)?(\d{4})\/(\d{1,6})(?:\.pdf)?\/?$/i;
const ARXIV_NEW = /^(?:https?:\/\/(?:www\.|export\.)?arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?\/?$/i;
const ARXIV_OLD = /^(?:https?:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)?([a-z][a-z-]*(?:\.[a-z]{2})?\/\d{7})(?:v\d+)?\/?$/i;
const DOI = /^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)?(10\.\d{4,9}\/\S+)$/i;
const DBLP = /^(?:https?:\/\/dblp\.(?:org|uni-trier\.de)\/rec\/|dblp:\s*)((?:conf|journals|books|series|reference|phd|tr)(?:\/[A-Za-z0-9_\-.+']+)+?)(?:\.html|\.xml|\.bib)?$/;

/** Normalize a query that is entirely one supported identifier; otherwise null. */
export function parseIdentifierQuery(raw: string): string | null {
  const q = raw.trim();
  if (!q || /\s/.test(q.replace(/^(eprint|iacr|arxiv|doi|dblp):\s+/i, "$1:"))) return null;
  let m = EPRINT.exec(q);
  if (m && Number(m[1]) >= 1996 && Number(m[2]) > 0) return `eprint:${Number(m[1])}/${Number(m[2])}`;
  m = ARXIV_NEW.exec(q);
  if (m) return `arxiv:${m[1]}`;
  m = ARXIV_OLD.exec(q);
  if (m && /arxiv/i.test(q)) {
    const [archive, number] = m[1]!.split("/");
    const [main, subj] = archive!.split(".");
    return `arxiv:${subj ? `${main!.toLowerCase()}.${subj.toUpperCase()}` : main!.toLowerCase()}/${number}`;
  }
  m = DOI.exec(q);
  if (m) return `doi:${m[1]!.toLowerCase()}`;
  m = DBLP.exec(q);
  if (m) return `dblp:${m[1]}`;
  return null;
}

// --- date token selection ---------------------------------------------------------------------

function overlapsRange(token: DateToken, start: string, end: string): boolean {
  return token.start !== null && token.end !== null && token.start <= end && token.end >= start;
}

export interface Plan {
  empty: boolean;
  query: string | null;
  filters: Record<string, { any: string[] }>;
  sort: Record<string, "asc"> | undefined;
  identifier: string | null;
  approximateDates: boolean;
}

export function effectiveView(state: ParsedFilterState): View {
  return state.view ?? "archive";
}

export function translate(state: ParsedFilterState, facets: SearchFacets, today: string): Plan {
  const view = effectiveView(state);
  const identifier = parseIdentifierQuery(state.q);
  const filters: Record<string, { any: string[] }> = {
    status: { any: state.status.length ? state.status : ["active"] },
  };
  for (const key of ["area", "topic", "source", "venue", "kind"] as const) {
    if (state[key].length) filters[key] = { any: state[key] };
  }
  if (state.code) filters.code = { any: ["yes"] };
  if (view === "foundations") filters.foundational = { any: ["yes"] };
  if (identifier) filters.identifier = { any: [identifier] };

  // Publication-date constraints (intersection of every active constraint).
  let allowed: DateToken[] | null = null;
  const restrict = (pred: (t: DateToken) => boolean) => {
    allowed = (allowed ?? facets.publication_tokens).filter(pred);
  };
  const windowDays = state.window && state.window !== "unknown" ? Number(state.window) : view === "new" ? 30 : null;
  if (state.window === "unknown") restrict((t) => t.token === "unknown");
  if (windowDays !== null && view !== "updated") {
    restrict((t) => t.precision === "day" && t.start !== null && (() => {
      const age = ageDays(t.start!, today);
      return age >= 0 && age < windowDays;
    })());
  }
  if (state.year_from !== null || state.year_to !== null) {
    const start = `${String(state.year_from ?? 1).padStart(4, "0")}-01-01`;
    const end = `${String(state.year_to ?? 9999).padStart(4, "0")}-12-31`;
    restrict((t) => overlapsRange(t, start, end));
  }
  if (state.date_from || state.date_to) {
    restrict((t) => overlapsRange(t, state.date_from ?? "0001-01-01", state.date_to ?? "9999-12-31"));
  }
  if (state.exact_dates) restrict((t) => t.precision === "day");
  let empty = false;
  const allowedTokens = allowed as DateToken[] | null;
  if (allowedTokens !== null) {
    if (allowedTokens.length === 0) empty = true;
    else filters.publication_period = { any: allowedTokens.map((t) => t.token) };
  }
  if (view === "updated") {
    filters.has_update = { any: ["yes"] };
    const days = windowDays ?? 30;
    const updates = facets.update_tokens.filter((t) => {
      const age = ageDays(t.start!, today);
      return age >= 0 && age < days;
    });
    if (updates.length === 0) empty = true;
    else filters.update_period = { any: updates.map((t) => t.token) };
  }
  const query = identifier ? null : state.q || null;
  let sort: Sort = state.sort ?? (query ? "relevance" : view === "updated" ? "updated" : "newest");
  if (sort === "relevance" && !query) sort = "newest";
  return {
    empty,
    query,
    filters,
    sort: sort === "relevance" ? undefined : { [`rank_${sort}`]: "asc" },
    identifier,
    approximateDates: allowedTokens !== null && allowedTokens.some((t) => t.precision !== "day"),
  };
}

// --- SearchService -------------------------------------------------------------------------------

export interface SearchRequest {
  query: string;
  filters: ParsedFilterState;
  sort: Sort;
  page: number;
  pageSize: 25;
  todayUTC: string;
}

export interface SearchResult {
  releaseId: string;
  total: number;
  page: number;
  pageSize: 25;
  items: PaperCardData[];
  pageCount: number;
  approximateDates: boolean;
  identifier: string | null;
}

export interface SearchService {
  search(request: SearchRequest): Promise<SearchResult>;
}

export interface PagefindResult { id: string; data(): Promise<{ url: string; meta: Record<string, string> }> }
export interface PagefindApi {
  search(query: string | null, options: { filters?: unknown; sort?: unknown }): Promise<{ results: PagefindResult[] }>;
}

export class ReleaseMismatchError extends Error {}

export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]!);
    }
  });
  await Promise.all(workers);
  return out;
}

export function paperToCard(paper: PublicPaper, base: string): PaperCardData {
  return {
    id: paper.id,
    title: paper.title,
    authors: paper.authors.map((a) => a.display_name),
    published: paper.published,
    areas: paper.areas,
    topics: paper.topics,
    contributions: paper.contributions,
    foundational: paper.foundational,
    status: paper.status,
    sources: paper.sources,
    venue: paper.publications.find((p) => p.venue_name)?.venue_name ?? null,
    doi: paper.publications.find((p) => p.doi)?.doi ?? null,
    has_code: paper.code_links.length > 0,
    has_abstract: paper.abstract !== null,
    latest_update: paper.latest_update,
    canonical_path: paper.canonical_path,
    bibtex_path: paper.bibtex_path,
    json_path: `${base}assets/releases/${paper.release_id}/papers/${paper.id}.json`,
  };
}

export class PagefindSearchService implements SearchService {
  private cache = new Map<string, PublicPaper>();

  constructor(
    private readonly deps: {
      loadPagefind: () => Promise<PagefindApi>;
      fetchPaper: (url: string) => Promise<unknown>;
      validatePaper: (value: unknown) => PublicPaper;
      facets: SearchFacets;
      releaseId: string;
      base: string;
      concurrency?: number;
    },
  ) {}

  cached(id: string): PublicPaper | undefined {
    return this.cache.get(`${this.deps.releaseId}:${id}`);
  }

  async paper(url: string, id: string): Promise<PublicPaper> {
    const key = `${this.deps.releaseId}:${id}`;
    const cached = this.cache.get(key);
    if (cached) return cached;
    const paper = this.deps.validatePaper(await this.deps.fetchPaper(url));
    if (paper.release_id !== this.deps.releaseId) {
      throw new ReleaseMismatchError("The index was updated while this page was open. Reload to continue.");
    }
    this.cache.set(key, paper);
    return paper;
  }

  async search(request: SearchRequest): Promise<SearchResult> {
    const state = { ...request.filters, sort: request.sort, q: request.query };
    const plan = translate(state, this.deps.facets, request.todayUTC);
    const base = {
      releaseId: this.deps.releaseId, page: request.page, pageSize: PAGE_SIZE,
      approximateDates: plan.approximateDates, identifier: plan.identifier,
    };
    if (plan.empty) return { ...base, total: 0, items: [], pageCount: 1 };
    const pagefind = await this.deps.loadPagefind();
    const response = await pagefind.search(plan.query, { filters: plan.filters, sort: plan.sort });
    const total = response.results.length;
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const slice = response.results.slice((request.page - 1) * PAGE_SIZE, request.page * PAGE_SIZE);
    const concurrency = this.deps.concurrency ?? 6;
    const fragments = await mapLimit(slice, concurrency, (r) => r.data());
    const papers = await mapLimit(fragments, concurrency, (f) => this.paper(f.meta.json!, f.meta.id!));
    return { ...base, total, pageCount, items: papers.map((p) => paperToCard(p, this.deps.base)) };
  }
}

/** Drop responses that finish after a newer query started. */
export class LatestOnly {
  private seq = 0;
  async run<T>(task: () => Promise<T>, apply: (value: T) => void): Promise<boolean> {
    const mine = ++this.seq;
    const value = await task();
    if (mine !== this.seq) return false;
    apply(value);
    return true;
  }
}
