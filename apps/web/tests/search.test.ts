import { describe, expect, it, vi } from "vitest";
import {
  LatestOnly, PagefindSearchService, ReleaseMismatchError, emptyState, parseIdentifierQuery,
  parseState, serializeState, translate, withChange, type PagefindApi, type SearchFacets, type Vocabulary,
} from "../src/lib/search";
import type { PublicPaper } from "../src/lib/contracts";

const vocab: Vocabulary = {
  area: ["formal-verification", "proof-systems", "security-proofs"],
  topic: ["recursion", "zero-knowledge"], source: ["arxiv", "eprint"], venue: [],
  kind: ["construction-theory"],
};
const TODAY = "2026-11-03";
const tok = (token: string, precision: string, start: string | null, end: string | null) => ({ token, precision, start, end });
const facets: SearchFacets = {
  release_id: "r1",
  publication_tokens: [
    tok("d:2026-11-03", "day", "2026-11-03", "2026-11-03"),
    tok("d:2026-10-28", "day", "2026-10-28", "2026-10-28"),
    tok("d:2026-10-27", "day", "2026-10-27", "2026-10-27"),
    tok("d:2026-10-05", "day", "2026-10-05", "2026-10-05"),
    tok("d:2026-10-04", "day", "2026-10-04", "2026-10-04"),
    tok("d:2026-11-04", "day", "2026-11-04", "2026-11-04"),
    tok("m:2026-11", "month", "2026-11-01", "2026-11-30"),
    tok("y:1985", "year", "1985-01-01", "1985-12-31"),
    tok("unknown", "unknown", null, null),
  ],
  update_tokens: [tok("d:2026-11-01", "day", "2026-11-01", "2026-11-01"), tok("d:2025-01-01", "day", "2025-01-01", "2025-01-01")],
  values: { source: ["arxiv", "eprint"], venue: [] },
};
const state = (q: string) => parseState(new URLSearchParams(q), vocab);
const plan = (q: string) => translate(state(q).state, facets, TODAY);

describe("URL state", () => {
  it("test_url_state_round_trip: repeated keys, sorted and deduplicated", () => {
    const { state: s, errors } = state(
      "q=folding&area=security-proofs&area=proof-systems&area=proof-systems&topic=recursion&source=eprint&source=arxiv&kind=construction-theory&year_from=1980&year_to=2026&code=1&sort=newest&page=2&junk=1");
    expect(errors).toEqual([]);
    expect(serializeState(s)).toBe(
      "q=folding&area=proof-systems&area=security-proofs&topic=recursion&source=arxiv&source=eprint&kind=construction-theory&year_from=1980&year_to=2026&code=1&sort=newest&page=2");
    expect(serializeState(state(serializeState(s)).state)).toBe(serializeState(s));
  });

  it("rejects impossible input instead of swapping or clamping", () => {
    expect(state("year_from=2026&year_to=1980").errors).toContain("From year must not be after To year");
    expect(state("date_from=2026-02-30").errors[0]).toMatch(/not a real calendar date/);
    expect(state("year_from=19x5").errors[0]).toMatch(/whole year/);
    expect(state("page=0").errors[0]).toMatch(/positive/);
    expect(state("area=cryptography").errors[0]).toMatch(/Unknown area/);
    expect(state("window=14").errors[0]).toMatch(/7, 30, 365/);
  });

  it("filter changes reset the page", () => {
    const s = { ...emptyState(), page: 4 };
    expect(withChange(s, { area: ["proof-systems"] }).page).toBe(1);
    expect(withChange(s, { page: 5 }).page).toBe(5);
  });
});

describe("filter translation", () => {
  it("test_facet_OR_with_cross_facet_AND", () => {
    const p = plan("area=proof-systems&area=security-proofs&source=eprint&source=arxiv");
    expect(p.filters.area).toEqual({ any: ["proof-systems", "security-proofs"] });
    expect(p.filters.source).toEqual({ any: ["arxiv", "eprint"] });
    expect(p.filters.status).toEqual({ any: ["active"] });
    expect(p.filters.topic).toBeUndefined();
  });

  it("test_recent_window_uses_zero_through_six_days and excludes future dates", () => {
    expect(plan("window=7").filters.publication_period!.any.sort()).toEqual(["d:2026-10-28", "d:2026-11-03"]);
    expect(plan("window=30").filters.publication_period!.any).not.toContain("d:2026-10-04");
    expect(plan("window=30").filters.publication_period!.any).toContain("d:2026-10-05");
    expect(plan("window=365").filters.publication_period!.any).not.toContain("d:2026-11-04");
    expect(plan("window=365").filters.publication_period!.any).not.toContain("m:2026-11");
  });

  it("test_partial_date_range_uses_documented_overlap_semantics", () => {
    const p = plan("year_from=1985&year_to=1985");
    expect(p.filters.publication_period!.any).toEqual(["y:1985"]);
    const month = plan("date_from=2026-11-02&date_to=2026-11-02");
    expect(month.filters.publication_period!.any).toEqual(["m:2026-11"]);
    expect(month.approximateDates).toBe(true);
    expect(plan("date_from=2026-11-02&date_to=2026-11-02&exact_dates=1").empty).toBe(true);
  });

  it("test_constrained_empty_date_token_set_returns_zero", () => {
    const p = plan("year_from=1700&year_to=1701");
    expect(p.empty).toBe(true);
    expect(p.filters.publication_period).toBeUndefined();
  });

  it("unknown dates only without constraints or when selected", () => {
    expect(plan("").filters.publication_period).toBeUndefined();
    expect(plan("window=unknown").filters.publication_period!.any).toEqual(["unknown"]);
    expect(plan("year_from=1").filters.publication_period!.any).not.toContain("unknown");
  });

  it("views impose their own constraints", () => {
    const n = plan("view=new");
    expect(n.filters.publication_period!.any).toContain("d:2026-10-05");
    expect(n.sort).toEqual({ rank_newest: "asc" });
    const u = plan("view=updated");
    expect(u.filters.has_update).toEqual({ any: ["yes"] });
    expect(u.filters.update_period).toEqual({ any: ["d:2026-11-01"] });
    expect(u.sort).toEqual({ rank_updated: "asc" });
    expect(plan("view=foundations").filters.foundational).toEqual({ any: ["yes"] });
    expect(plan("q=folding").sort).toBeUndefined();
    expect(plan("sort=oldest").sort).toEqual({ rank_oldest: "asc" });
  });

  it("exact identifier search", () => {
    expect(parseIdentifierQuery("2020/0007")).toBe("eprint:2020/7");
    expect(parseIdentifierQuery("https://eprint.iacr.org/2020/007")).toBe("eprint:2020/7");
    expect(parseIdentifierQuery("arXiv:2001.00007v2")).toBe("arxiv:2001.00007");
    expect(parseIdentifierQuery("https://doi.org/10.9999/SYNTHETIC.0001")).toBe("doi:10.9999/synthetic.0001");
    expect(parseIdentifierQuery("arxiv:hep-th/9901001")).toBe("arxiv:hep-th/9901001");
    expect(parseIdentifierQuery("folding schemes")).toBeNull();
    expect(parseIdentifierQuery("zero/knowledge")).toBeNull();
    const p = plan("q=2020/007");
    expect(p.query).toBeNull();
    expect(p.filters.identifier).toEqual({ any: ["eprint:2020/7"] });
  });
});

function fakePaper(id: string, release = "r1"): PublicPaper {
  return {
    schema_version: 1, release_id: release, id, title: `T ${id}`, authors: [], abstract: null,
    published: { year: 2020, month: null, day: null, precision: "year" }, areas: ["proof-systems"],
    topics: [], contributions: [], foundational: false, status: "active", metadata_issues: [],
    sources: [], publications: [], code_links: [], latest_update: null,
    inclusion: { review_status: "automatic", explanation: "", evidence: [] }, related_work_ids: [],
    canonical_path: `/papers/${id}/`, bibtex_path: `/citations/${id}.bib`, history: [],
  };
}

function fakePagefind(n: number, dataSpy: (id: string) => void): PagefindApi {
  return {
    search: vi.fn(async () => ({
      results: Array.from({ length: n }, (_, i) => ({
        id: `r${i}`,
        data: async () => { dataSpy(`w${i}`); return { url: `/papers/w${i}/`, meta: { id: `w${i}`, json: `/j/w${i}.json` } }; },
      })),
    })),
  };
}

describe("SearchService", () => {
  it("test_search_hydrates_only_requested_page", async () => {
    const hydrated: string[] = [];
    const fetched: string[] = [];
    const pf = fakePagefind(60, (id) => hydrated.push(id));
    const service = new PagefindSearchService({
      loadPagefind: async () => pf, facets, releaseId: "r1", base: "/",
      validatePaper: (v) => v as PublicPaper,
      fetchPaper: async (url) => { fetched.push(url); return fakePaper(url.slice(3, -5)); },
    });
    const result = await service.search({ query: "", filters: { ...emptyState(), page: 2 }, sort: "newest", page: 2, pageSize: 25, todayUTC: TODAY });
    expect(result.total).toBe(60);
    expect(result.pageCount).toBe(3);
    expect(result.items.map((i) => i.id)).toEqual(Array.from({ length: 25 }, (_, i) => `w${i + 25}`));
    expect(hydrated.length).toBe(25);
    expect(fetched.length).toBe(25);
    // Cached per release + work: a repeat search fetches nothing new.
    await service.search({ query: "", filters: { ...emptyState(), page: 2 }, sort: "newest", page: 2, pageSize: 25, todayUTC: TODAY });
    expect(fetched.length).toBe(25);
  });

  it("constrained empty sets never reach Pagefind", async () => {
    const pf = fakePagefind(5, () => {});
    const service = new PagefindSearchService({ loadPagefind: async () => pf, facets, releaseId: "r1", base: "/",
      validatePaper: (v) => v as PublicPaper, fetchPaper: async () => ({}) });
    const r = await service.search({ query: "", filters: state("year_from=1700&year_to=1701").state, sort: "newest", page: 1, pageSize: 25, todayUTC: TODAY });
    expect(r.total).toBe(0);
    expect(pf.search).not.toHaveBeenCalled();
  });

  it("test_release_mismatch_requests_reload_not_silent_merge", async () => {
    const service = new PagefindSearchService({ loadPagefind: async () => fakePagefind(1, () => {}), facets,
      releaseId: "r1", base: "/", validatePaper: (v) => v as PublicPaper,
      fetchPaper: async () => fakePaper("w0", "r2") });
    await expect(service.search({ query: "", filters: emptyState(), sort: "newest", page: 1, pageSize: 25, todayUTC: TODAY }))
      .rejects.toBeInstanceOf(ReleaseMismatchError);
  });

  it("test_late_search_response_cannot_replace_current_results", async () => {
    const latest = new LatestOnly();
    const applied: string[] = [];
    let releaseSlow: (v: string) => void = () => {};
    const slow = latest.run(() => new Promise<string>((r) => { releaseSlow = r; }), (v) => applied.push(v));
    const fast = latest.run(async () => "new", (v) => applied.push(v));
    await fast;
    releaseSlow("old");
    expect(await slow).toBe(false);
    expect(applied).toEqual(["new"]);
  });
});
