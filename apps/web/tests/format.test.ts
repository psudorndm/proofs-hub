import { describe, expect, it } from "vitest";
import { authorsText, displayId, lineModel } from "../src/lib/format";
import { ageDays, todayUTC } from "../src/lib/dates";
import { withBase } from "../src/lib/urls";
import { PAPER_CARD_KEYS, PUBLIC_PAPER_KEYS, type PaperCardData } from "../src/lib/contracts";
import paperSchema from "../src/lib/schema/public-paper.schema.json";
import cardSchema from "../src/lib/schema/paper-card.schema.json";

export const card: PaperCardData = {
  id: "w1", title: "<script>x</script> Title.", authors: ["Ann", "Bob", "Cy"],
  published: { year: 2026, month: 10, day: 30, precision: "day" },
  areas: ["proof-systems", "security-proofs"], topics: [], contributions: [], foundational: false,
  status: "active",
  sources: [
    { provider: "arxiv", source_id: "2101.00001", landing_url: "https://arxiv.org/abs/2101.00001", pdf_url: null, version: "v2", status: "active" },
    { provider: "eprint", source_id: "2021/42", landing_url: "https://eprint.iacr.org/2021/042", pdf_url: null, version: null, status: "active" },
    { provider: "dblp", source_id: "conf/x/Y21", landing_url: "https://dblp.org/rec/conf/x/Y21", pdf_url: null, version: null, status: "withdrawn" },
  ],
  venue: null, doi: null, has_code: false, has_abstract: false, latest_update: null,
  canonical_path: "/papers/w1/", bibtex_path: "/citations/w1.bib", json_path: "/a/w1.json",
};

describe("one-line entries", () => {
  it("orders sources ePrint first, hides withdrawn sources, and keeps text as data", () => {
    const m = lineModel(card);
    expect(m.sources.map((s) => `[${s.tag}] ${s.id}`)).toEqual(["[eprint] 2021/042", "[arxiv] 2101.00001"]);
    expect(m.title).toBe("<script>x</script> Title");
    expect(m.authors).toBe("Ann, Bob and Cy");
    expect(m.areas.map((a) => a.label)).toEqual(["proof systems", "security proofs"]);
    expect(m.providers).toEqual(["arxiv", "eprint", "dblp"]);
    expect(m.note).toBeNull();
  });

  it("labels withdrawn works and pads ePrint numbers", () => {
    expect(lineModel({ ...card, status: "withdrawn" }).note).toBe("withdrawn");
    expect(displayId(card.sources[1]!)).toBe("2021/042");
    expect(authorsText([])).toBe("Authors unavailable");
    expect(authorsText(["Solo"])).toBe("Solo");
  });

  it("dates and base paths", () => {
    expect(todayUTC(new Date("2026-11-03T23:59:59Z"))).toBe("2026-11-03");
    expect(ageDays("2026-10-28", "2026-11-03")).toBe(6);
    expect(withBase("assets/x", "/proof-papers/")).toBe("/proof-papers/assets/x");
  });
});

describe("contract alignment", () => {
  it("TypeScript key lists match the exported JSON Schemas", () => {
    expect([...PUBLIC_PAPER_KEYS].sort()).toEqual(Object.keys((paperSchema as any).properties).sort());
    expect([...PAPER_CARD_KEYS].sort()).toEqual(Object.keys((cardSchema as any).properties).sort());
  });

  it("the fixture release's recent lists validate", async () => {
    const dir = process.env.CATALOG_DIR;
    if (!dir) return;
    const fs = await import("node:fs");
    const path = await import("node:path");
    const { asManifest, asRecent } = await import("../src/lib/validate");
    asManifest(JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf-8")));
    asRecent(JSON.parse(fs.readFileSync(path.join(dir, "lists/recent.json"), "utf-8")));
    expect(() => asRecent({ release_id: "x" })).toThrow(/public schema/);
  });
});
