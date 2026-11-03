// Main page behaviour: the source toggle over the static lists, and text search.
import { todayUTC } from "./dates";
import { SEARCH_FAILED } from "./format";
import { renderPaperLine } from "./render";
import {
  LatestOnly, PagefindSearchService, ReleaseMismatchError, emptyState, parseState, serializeState,
  type PagefindApi, type ParsedFilterState, type Vocabulary,
} from "./search";

const VOCAB: Vocabulary = {
  area: ["formal-verification", "proof-systems", "security-proofs"],
  topic: [], source: ["arxiv", "crossref", "curated", "dblp", "eprint"], venue: [], kind: [],
};

export function startHome(doc: Document = document, win: Window = window): void {
  const controls = doc.querySelector<HTMLElement>("[data-controls]");
  const input = doc.querySelector<HTMLInputElement>("#q");
  const recent = doc.querySelector<HTMLElement>("[data-recent]");
  const results = doc.querySelector<HTMLElement>("[data-results]");
  const list = doc.querySelector<HTMLUListElement>("[data-result-list]");
  const count = doc.querySelector<HTMLElement>("[data-count]");
  if (!controls || !input || !recent || !results || !list || !count) return;
  controls.hidden = false; // toggles and search need JavaScript; the lists do not.

  const releaseId = doc.querySelector<HTMLMetaElement>('meta[name="proof-index-release"]')!.content;
  const base = doc.querySelector<HTMLMetaElement>('meta[name="proof-index-base"]')!.content;
  let pagefind: Promise<PagefindApi> | null = null;
  const service = new PagefindSearchService({
    releaseId,
    facets: { release_id: releaseId, publication_tokens: [], update_tokens: [], values: {} },
    loadPagefind: () => {
      pagefind ??= import(/* @vite-ignore */ `${base}assets/releases/${encodeURIComponent(releaseId)}/pagefind/pagefind.js`)
        .then(async (pf) => { await pf.options({ baseUrl: base }); return pf as PagefindApi; });
      return pagefind;
    },
  });
  const latest = new LatestOnly();

  const read = (): ParsedFilterState => {
    const { state } = parseState(new URLSearchParams(win.location.search), VOCAB);
    return { ...emptyState(), q: state.q, source: state.source.slice(0, 1) };
  };
  let state = read();

  function sync() {
    if (doc.activeElement !== input) input!.value = state.q;
    for (const b of doc.querySelectorAll<HTMLButtonElement>("button[data-source]")) {
      b.setAttribute("aria-pressed", String((state.source[0] ?? "") === b.dataset.source));
    }
    const source = state.source[0];
    for (const section of recent!.querySelectorAll<HTMLElement>("[data-window]")) {
      let visible = 0;
      for (const li of section.querySelectorAll<HTMLLIElement>("li.paper")) {
        const show = !source || (li.dataset.sources ?? "").split(" ").includes(source);
        li.hidden = !show;
        if (show) visible += 1;
      }
      section.querySelector<HTMLElement>("[data-empty]")!.hidden = visible > 0;
    }
    recent!.hidden = state.q !== "";
    results!.hidden = state.q === "";
  }

  async function search() {
    if (!state.q) return;
    const token = latest.begin();
    list!.replaceChildren();
    count!.textContent = "searching…";
    try {
      const result = await service.search({ state, todayUTC: todayUTC() }, (cards, total) => {
        for (const card of cards) list!.append(renderPaperLine(doc, card));
        const shown = list!.children.length;
        count!.textContent = shown < total
          ? `${total} papers (loading ${shown}…)`
          : `${total} ${total === 1 ? "paper" : "papers"}`;
      }, token.current);
      if (!token.current()) return;
      if (result.total === 0) {
        count!.textContent = result.identifier ? `no paper with identifier “${state.q}”` : "no papers match";
      }
    } catch (error) {
      if (!token.current()) return;
      list!.replaceChildren();
      count!.textContent = error instanceof ReleaseMismatchError ? error.message : SEARCH_FAILED;
    }
  }

  function update(change: Partial<ParsedFilterState>, push: boolean) {
    state = { ...state, ...change };
    const qs = serializeState(state);
    const url = `${win.location.pathname}${qs ? `?${qs}` : ""}`;
    if (push) win.history.pushState(null, "", url);
    else win.history.replaceState(null, "", url);
    sync();
    void search();
  }

  for (const b of doc.querySelectorAll<HTMLButtonElement>("button[data-source]")) {
    b.addEventListener("click", () => update({ source: b.dataset.source ? [b.dataset.source] : [] }, true));
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => update({ q: input.value.trim() }, false), 200);
  });
  win.addEventListener("popstate", () => { state = read(); sync(); void search(); });
  sync();
  void search();
}
