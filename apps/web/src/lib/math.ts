// Math in titles/abstracts (spec section 15): KaTeX with trust disabled.
// Non-math text is HTML-escaped; an expression KaTeX rejects is shown as its escaped
// original notation. Nothing from provider data is ever injected as raw HTML.
import katex from "katex";

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

interface Segment { math: boolean; display: boolean; source: string; body: string }

// $$...$$, \[...\], \(...\), and pandoc-style inline $...$ (no space after the opening
// $, none before the closing $, and the closing $ not followed by a digit).
const PATTERN = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?!\s)((?:\\\$|[^$\n])+?)(?<!\s)\$(?!\d)/g;

export function splitMath(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(PATTERN)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ math: false, display: false, source: text.slice(last, start), body: "" });
    const body = m[1] ?? m[2] ?? m[3] ?? m[4] ?? "";
    out.push({ math: true, display: m[1] !== undefined || m[2] !== undefined, source: m[0], body });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ math: false, display: false, source: text.slice(last), body: "" });
  return out;
}

// Commands that KaTeX only honours with `trust`; show them as original notation.
const TRUST_GATED = /\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData)\b/;

export function renderMathSafe(text: string): string {
  return splitMath(text)
    .map((seg) => {
      if (!seg.math) return escapeHtml(seg.source);
      if (TRUST_GATED.test(seg.body)) return `<span class="math-fallback">${escapeHtml(seg.source)}</span>`;
      try {
        return katex.renderToString(seg.body, {
          displayMode: false, // titles/abstracts stay inline even for $$...$$
          throwOnError: true,
          trust: false,
          strict: "ignore",
          output: "htmlAndMathml",
          maxSize: 10,
          maxExpand: 200,
        });
      } catch {
        return `<span class="math-fallback">${escapeHtml(seg.source)}</span>`;
      }
    })
    .join("");
}

export function hasMath(text: string): boolean {
  return splitMath(text).some((s) => s.math);
}
