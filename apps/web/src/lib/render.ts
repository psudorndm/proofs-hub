// Client-side card renderer. Same DTO, classes, text, and structure as PaperCard.astro.
// All provider text goes through textContent; no HTML from data is ever injected.
import type { PaperCardData, Taxonomy } from "./contracts";
import { ABSTRACT_UNAVAILABLE, cardModel } from "./format";

function el<K extends keyof HTMLElementTagNameMap>(
  doc: Document, tag: K, attrs: Record<string, string> = {}, text?: string,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function renderPaperCard(
  doc: Document, card: PaperCardData, taxonomy: Taxonomy, abstract: string | null,
): HTMLElement {
  const m = cardModel(card, taxonomy);
  const article = el(doc, "article", { class: "paper-card", "data-paper-id": m.id });
  const h3 = el(doc, "h3", { class: "paper-title" });
  h3.append(el(doc, "a", { href: m.href }, m.title));
  article.append(h3);
  const meta = el(doc, "p", { class: "paper-meta" });
  meta.append(doc.createTextNode(`${m.authors} · `));
  meta.append(el(doc, "span", m.approximateDate ? { class: "approx" } : {}, m.date));
  if (m.venue) meta.append(doc.createTextNode(` · ${m.venue}`));
  article.append(meta);
  const tags = el(doc, "ul", { class: "paper-tags" });
  for (const b of m.badges) tags.append(el(doc, "li", { class: b.className }, b.label));
  tags.append(el(doc, "li", { class: "badge badge-contribution" }, m.contribution));
  if (m.foundational) tags.append(el(doc, "li", { class: "badge badge-foundational" }, "Foundational"));
  if (m.statusNote) tags.append(el(doc, "li", { class: "badge badge-status" }, m.statusNote));
  article.append(tags);
  const links = el(doc, "p", { class: "paper-links" });
  for (const l of m.links) {
    const attrs: Record<string, string> = { href: l.href, "data-kind": l.kind };
    if (l.kind !== "cite" && l.kind !== "code") attrs.rel = "noopener";
    links.append(el(doc, "a", attrs, l.label));
  }
  article.append(links);
  const details = el(doc, "details", { class: "abstract" });
  details.append(el(doc, "summary", {}, "Show abstract"));
  details.append(el(doc, "p", { "data-math": "" }, abstract ?? ABSTRACT_UNAVAILABLE));
  article.append(details);
  return article;
}
