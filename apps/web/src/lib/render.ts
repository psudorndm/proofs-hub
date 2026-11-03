// Client renderer for search results. Same DTO, classes, and structure as PaperLine.astro.
// Provider text is escaped (textContent, or renderMathSafe's escaping + KaTeX output).
import type { PaperCardData } from "./contracts";
import { lineModel } from "./format";
import { renderMathSafe } from "./math";

export function renderPaperLine(doc: Document, card: PaperCardData): HTMLLIElement {
  const m = lineModel(card);
  const li = doc.createElement("li");
  li.className = `paper ${m.areas[0]?.className.replace("tag ", "") ?? ""}`.trim();
  li.dataset.paperId = m.id;
  li.dataset.areas = m.areaIds.join(" ");
  li.dataset.sources = m.providers.join(" ");
  const meta = doc.createElement("span");
  meta.className = "meta";
  for (const s of m.sources) {
    const ref = doc.createElement("span");
    ref.className = "ref";
    const tag = doc.createElement("span");
    tag.className = "source";
    tag.textContent = `[${s.tag}]`;
    ref.append(tag);
    if (s.id) {
      ref.append(" ");
      if (s.href) {
        const a = doc.createElement("a");
        a.href = s.href;
        a.rel = "noopener";
        a.textContent = s.id;
        ref.append(a);
      } else {
        ref.append(s.id);
      }
    }
    meta.append(ref);
  }
  li.append(meta);
  const title = doc.createElement("span");
  title.className = "paper-title";
  title.innerHTML = renderMathSafe(m.title);
  li.append(title);
  const authors = doc.createElement("span");
  authors.className = "authors";
  authors.textContent = m.authors;
  li.append(authors);
  const tags = doc.createElement("span");
  tags.className = "tags";
  for (const a of m.areas) {
    const t = doc.createElement("span");
    t.className = a.className;
    t.textContent = a.label;
    tags.append(t);
  }
  if (m.note) {
    const n = doc.createElement("span");
    n.className = "tag tag-note";
    n.textContent = m.note;
    tags.append(n);
  }
  li.append(tags);
  return li;
}
