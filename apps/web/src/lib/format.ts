// One-line paper entry rules, shared by PaperLine.astro and the client renderer.
import type { PaperCardData, PublicSource, Taxonomy } from "./contracts";

const SOURCE_ORDER = ["eprint", "arxiv", "crossref", "dblp", "curated"];
const SOURCE_TAG: Record<string, string> = {
  eprint: "eprint", arxiv: "arxiv", crossref: "doi", dblp: "dblp", curated: "curated",
};

export interface SourceRef { tag: string; id: string; href: string | null }
export interface LineModel {
  id: string;
  title: string;
  authors: string;
  sources: SourceRef[];
  providers: string[];
}

export function displayId(source: PublicSource): string {
  if (source.provider === "eprint") {
    const [year, num] = source.source_id.split("/");
    return `${year}/${String(num ?? "").padStart(3, "0")}`;
  }
  if (source.provider === "curated") return "";
  return source.source_id;
}

export function authorsText(names: string[]): string {
  if (names.length === 0) return "Authors unavailable";
  if (names.length === 1) return names[0]!;
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export function lineModel(card: PaperCardData, _tax?: Taxonomy): LineModel {
  const sources = [...card.sources]
    .filter((s) => s.status === "active" || card.sources.every((x) => x.status !== "active"))
    .sort((a, b) => SOURCE_ORDER.indexOf(a.provider) - SOURCE_ORDER.indexOf(b.provider))
    .map((s) => ({ tag: SOURCE_TAG[s.provider] ?? s.provider, id: displayId(s), href: s.landing_url }));
  return {
    id: card.id,
    title: card.title.replace(/\.\s*$/, ""),
    authors: authorsText(card.authors),
    sources,
    providers: [...new Set(card.sources.map((s) => s.provider))],
  };
}

export const EMPTY_WINDOW = "No papers in this window.";
export const PREVIEW_LABEL = "Preview dataset — not the live research index.";
export const SEARCH_FAILED = "Search could not load. The lists below still work.";
