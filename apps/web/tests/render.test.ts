// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderPaperCard } from "../src/lib/render";
import type { PaperCardData, Taxonomy } from "../src/lib/contracts";

const taxonomy: Taxonomy = { version: "t", areas: [{ id: "proof-systems", display_name: "Proof systems" }], topics: [], contributions: [] };
const card: PaperCardData = {
  id: "w1", title: "<img src=x onerror=alert(1)> Title", authors: ["<b>A</b>"],
  published: { year: 2020, month: 1, day: 3, precision: "day" }, areas: ["proof-systems"], topics: [],
  contributions: [], foundational: false, status: "active", sources: [], venue: null, doi: null,
  has_code: false, has_abstract: false, latest_update: null, canonical_path: "/papers/w1/",
  bibtex_path: "/citations/w1.bib", json_path: "/a/w1.json",
};

describe("renderPaperCard", () => {
  it("test_untrusted_title_and_link_are_safe in the client renderer", () => {
    const node = renderPaperCard(document, card, taxonomy, "<script>alert(1)</script>");
    expect(node.querySelector("img")).toBeNull();
    expect(node.querySelector("script")).toBeNull();
    expect(node.querySelector("b")).toBeNull();
    expect(node.querySelector(".paper-title a")!.textContent).toBe(card.title);
    expect(node.querySelector(".abstract p")!.textContent).toBe("<script>alert(1)</script>");
    expect([...node.querySelectorAll("a")].every((a) => !a.getAttribute("href")!.startsWith("javascript:"))).toBe(true);
  });

  it("uses the shared structure and classes", () => {
    const node = renderPaperCard(document, card, taxonomy, null);
    expect([...node.children].map((c) => c.tagName + "." + c.className)).toEqual([
      "H3.paper-title", "P.paper-meta", "UL.paper-tags", "P.paper-links", "DETAILS.abstract",
    ]);
    expect(node.querySelector(".abstract p")!.textContent).toBe("Abstract unavailable from the indexed sources.");
  });
});
