// Search page controller: URL state <-> SearchService <-> results region.
import type { Taxonomy } from "./contracts";
import { todayUTC } from "./dates";
import { EMPTY_RESULTS, SEARCH_FAILED } from "./format";
import { renderPaperCard } from "./render";
import {
  LatestOnly, PagefindSearchService, ReleaseMismatchError, parseState, serializeState, withChange,
  type ParsedFilterState, type SearchFacets, type Vocabulary, type PagefindApi,
} from "./search";
import { asPaper } from "./validate";

interface Boot { releaseId: string; base: string; taxonomy: Taxonomy; facets: SearchFacets }

export function startSearch(boot: Boot, doc: Document = document, win: Window = window): void {
  const form = doc.querySelector<HTMLFormElement>("[data-search-form]");
  const region = doc.querySelector<HTMLElement>("[data-search-results]");
  const count = doc.querySelector<HTMLElement>("[data-result-count]");
  if (!form || !region || !count) return;
  const vocab: Vocabulary = {
    area: boot.taxonomy.areas.map((a) => a.id),
    topic: boot.taxonomy.topics.map((t) => t.id),
    source: boot.facets.values.source ?? [],
    venue: boot.facets.values.venue ?? [],
    kind: boot.taxonomy.contributions.map((c) => c.id),
  };
  const asset = (rel: string) => `${boot.base}assets/releases/${encodeURIComponent(boot.releaseId)}/${rel}`;
  let pagefind: Promise<PagefindApi> | null = null;
  const service = new PagefindSearchService({
    releaseId: boot.releaseId,
    base: boot.base,
    facets: boot.facets,
    validatePaper: asPaper,
    fetchPaper: async (url) => {
      const res = await fetch(url);
      if (!res.ok) throw new ReleaseMismatchError("Search data for this release is no longer available. Reload the page.");
      return res.json();
    },
    loadPagefind: () => {
      pagefind ??= import(/* @vite-ignore */ asset("pagefind/pagefind.js")).then(async (pf) => {
        await pf.options({ baseUrl: boot.base });
        return pf as PagefindApi;
      });
      return pagefind;
    },
  });
  const latest = new LatestOnly();
  const list = doc.createElement("div");
  region.append(list);

  function syncTabs(state: ParsedFilterState) {
    for (const tab of doc.querySelectorAll<HTMLAnchorElement>("a[data-area]")) {
      const id = tab.dataset.area ?? "";
      const active = id === "" ? state.area.length === 0 : state.area.length === 1 && state.area[0] === id;
      if (active) tab.setAttribute("aria-current", "true");
      else tab.removeAttribute("aria-current");
    }
  }

  function fillForm(state: ParsedFilterState) {
    syncTabs(state);
    const q = form!.querySelector<HTMLInputElement>("input[name=q]");
    if (q && doc.activeElement !== q) q.value = state.q;
    for (const input of form!.querySelectorAll<HTMLInputElement>("input[type=checkbox]")) {
      const key = input.name as keyof ParsedFilterState;
      const value = state[key];
      input.checked = Array.isArray(value) ? value.includes(input.value) : value === true;
    }
    for (const select of form!.querySelectorAll<HTMLSelectElement>("select")) {
      const value = state[select.name as keyof ParsedFilterState];
      for (const option of select.options) {
        option.selected = Array.isArray(value) ? value.includes(option.value) : String(value ?? "") === option.value;
      }
    }
    for (const input of form!.querySelectorAll<HTMLInputElement>("input[type=number], input[type=date]")) {
      const value = state[input.name as keyof ParsedFilterState];
      input.value = value === null || value === undefined ? "" : String(value);
    }
  }

  function readForm(prev: ParsedFilterState): ParsedFilterState {
    const params = new URLSearchParams(new FormData(form!) as unknown as Record<string, string>);
    if (prev.view) params.set("view", prev.view);
    return parseState(params, vocab).state;
  }

  function message(text: string, action?: { label: string; href: string }) {
    list.replaceChildren();
    const box = doc.createElement("div");
    box.className = "empty";
    const p = doc.createElement("p");
    p.textContent = text;
    box.append(p);
    if (action) {
      const a = doc.createElement("a");
      a.href = action.href;
      a.textContent = action.label;
      box.append(a);
    }
    list.append(box);
  }

  function pager(state: ParsedFilterState, pageCount: number) {
    if (pageCount <= 1) return null;
    const nav = doc.createElement("nav");
    nav.className = "pagination";
    nav.setAttribute("aria-label", "Pages");
    const mk = (label: string, page: number | null) => {
      if (page === null) {
        const s = doc.createElement("span");
        s.className = "disabled";
        s.textContent = label;
        return s;
      }
      const a = doc.createElement("a");
      a.href = `?${serializeState({ ...state, page })}`;
      a.textContent = label;
      a.addEventListener("click", (e) => { e.preventDefault(); navigate({ ...state, page }, true); });
      return a;
    };
    const cur = doc.createElement("span");
    cur.textContent = `Page ${state.page} of ${pageCount}`;
    nav.append(mk("Previous", state.page > 1 ? state.page - 1 : null), cur,
      mk("Next", state.page < pageCount ? state.page + 1 : null));
    return nav;
  }

  async function run(state: ParsedFilterState) {
    const { errors } = parseState(new URLSearchParams(win.location.search), vocab);
    if (errors.length) {
      count!.textContent = "";
      message(`Fix the search filters: ${errors.join("; ")}.`);
      return;
    }
    count!.textContent = "Searching…";
    const ok = await latest.run(
      () => service.search({
        query: state.q, filters: state, sort: state.sort ?? (state.q ? "relevance" : "newest"),
        page: state.page, pageSize: 25, todayUTC: todayUTC(),
      }).catch((error: unknown) => error as Error),
      (result) => {
        if (result instanceof ReleaseMismatchError) {
          count!.textContent = "";
          message(result.message, { label: "Reload", href: win.location.href });
          return;
        }
        if (result instanceof Error) {
          count!.textContent = "";
          message(SEARCH_FAILED, { label: "Browse the archive", href: `${boot.base}archive/` });
          return;
        }
        const label = `${result.total} ${result.total === 1 ? "paper" : "papers"}`;
        count!.textContent = result.approximateDates ? `${label} · Includes approximate dates` : label;
        if (result.total === 0) {
          message(result.identifier ? `No paper with identifier “${state.q}”.` : EMPTY_RESULTS,
            { label: "Clear filters", href: `${boot.base}search/` });
          return;
        }
        if (state.page > result.pageCount) {
          message(`Page ${state.page} is past the last page (${result.pageCount}).`,
            { label: "Go to the first page", href: `?${serializeState({ ...state, page: 1 })}` });
          return;
        }
        const ol = doc.createElement("ol");
        ol.className = "paper-list";
        for (const card of result.items) {
          const li = doc.createElement("li");
          const paper = service.cached(card.id);
          li.append(renderPaperCard(doc, card, boot.taxonomy, paper?.abstract ?? null));
          ol.append(li);
        }
        const nav = pager(state, result.pageCount);
        list.replaceChildren(ol, ...(nav ? [nav] : []));
      },
    );
    return ok;
  }

  function navigate(state: ParsedFilterState, push: boolean) {
    syncTabs(state);
    const url = `${win.location.pathname}${serializeState(state) ? `?${serializeState(state)}` : ""}`;
    if (push) win.history.pushState(null, "", url);
    else win.history.replaceState(null, "", url);
    void run(state);
  }

  let current = parseState(new URLSearchParams(win.location.search), vocab).state;
  fillForm(current);
  void run(current);
  let timer: ReturnType<typeof setTimeout> | undefined;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    current = withChange(current, readForm(current));
    navigate(current, true);
  });
  form.addEventListener("input", (e) => {
    const target = e.target as HTMLInputElement;
    if (target.name !== "q") return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      current = withChange(current, { q: target.value.trim() });
      navigate(current, false);
    }, 200);
  });
  form.addEventListener("change", (e) => {
    const target = e.target as HTMLInputElement;
    if (target.name === "q") return;
    current = withChange(current, readForm(current));
    navigate(current, true);
  });
  win.addEventListener("popstate", () => {
    current = parseState(new URLSearchParams(win.location.search), vocab).state;
    fillForm(current);
    void run(current);
  });
}
