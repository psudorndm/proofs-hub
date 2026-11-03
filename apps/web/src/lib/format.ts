// Card content rules shared by PaperCard.astro and the client renderPaperCard().
import type { Area, PaperCardData, PublicSource, Taxonomy } from "./contracts";
import { formatPublished } from "./dates";

export const AREA_CLASS: Record<Area, string> = {
  "proof-systems": "badge-ps",
  "security-proofs": "badge-sp",
  "formal-verification": "badge-fv",
};

const PROVIDER_NAMES: Record<string, string> = {
  eprint: "ePrint",
  arxiv: "arXiv",
  dblp: "DBLP",
  crossref: "Crossref",
  curated: "Curated record",
};

export interface Badge { label: string; className: string }
export interface LinkItem { label: string; href: string; kind: "source" | "pdf" | "publisher" | "code" | "cite" }

export interface CardModel {
  id: string;
  title: string;
  href: string;
  authors: string;
  date: string;
  venue: string | null;
  badges: Badge[];
  contribution: string;
  links: LinkItem[];
  statusNote: string | null;
  foundational: boolean;
  approximateDate: boolean;
}

function displayEprint(id: string): string {
  const [year, num] = id.split("/");
  return `${year}/${String(num ?? "").padStart(3, "0")}`;
}

export function sourceLabel(source: PublicSource): string {
  const name = PROVIDER_NAMES[source.provider] ?? source.provider;
  if (source.provider === "eprint") return `${name} ${displayEprint(source.source_id)}`;
  if (source.provider === "arxiv") return `${name} ${source.source_id}${source.version ? source.version : ""}`;
  return name;
}

export function authorsText(names: string[], max = 10): string {
  if (names.length === 0) return "Authors unavailable";
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, max).join(", ")}, and ${names.length - max} more`;
}

export function labelFor(tax: Taxonomy, kind: "areas" | "topics" | "contributions", id: string): string {
  const entry = (tax[kind] as { id: string; display_name: string }[]).find((e) => e.id === id);
  return entry ? entry.display_name : id;
}

export function cardModel(card: PaperCardData, tax: Taxonomy): CardModel {
  const badges: Badge[] = [
    ...card.areas.map((a) => ({ label: labelFor(tax, "areas", a), className: `badge ${AREA_CLASS[a]}` })),
    ...card.topics.map((t) => ({ label: labelFor(tax, "topics", t), className: "badge badge-topic" })),
  ];
  const links: LinkItem[] = [];
  for (const source of card.sources) {
    if (source.landing_url) links.push({ label: sourceLabel(source), href: source.landing_url, kind: "source" });
  }
  if (card.doi) links.push({ label: "Publisher", href: `https://doi.org/${card.doi}`, kind: "publisher" });
  const pdf = card.sources.find((s) => s.pdf_url);
  if (pdf?.pdf_url) links.push({ label: "PDF", href: pdf.pdf_url, kind: "pdf" });
  if (card.has_code) links.push({ label: "Code link", href: card.canonical_path, kind: "code" });
  links.push({ label: "Cite", href: card.bibtex_path, kind: "cite" });
  const contributions = card.contributions.map((c) => labelFor(tax, "contributions", c));
  const withdrawnSources = card.sources.filter((s) => s.status !== "active").map(sourceLabel);
  let statusNote: string | null = null;
  if (card.status === "withdrawn") statusNote = "Withdrawn";
  else if (card.status === "unavailable") statusNote = "Unavailable at all known sources";
  else if (withdrawnSources.length) statusNote = `Withdrawn or deleted at: ${withdrawnSources.join(", ")}`;
  return {
    id: card.id,
    title: card.title,
    href: card.canonical_path,
    authors: authorsText(card.authors),
    date: formatPublished(card.published),
    venue: card.venue,
    badges,
    contribution: contributions.length ? contributions.join(" · ") : "Research paper",
    links,
    statusNote,
    foundational: card.foundational,
    approximateDate: card.published.precision !== "day",
  };
}

export const ABSTRACT_UNAVAILABLE = "Abstract unavailable from the indexed sources.";
export const PREVIEW_LABEL = "Preview dataset — not the live research index.";
export const PARTIAL_FRESHNESS =
  "Some sources have not refreshed successfully. Previously indexed papers remain available.";
export const EMPTY_RESULTS = "No papers match these filters.";
export const SEARCH_FAILED = "Search could not load. Retry or browse the archive.";
