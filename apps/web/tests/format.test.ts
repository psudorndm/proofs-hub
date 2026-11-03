import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { cardModel, authorsText, sourceLabel } from "../src/lib/format";
import { formatPublished, ageDays, todayUTC } from "../src/lib/dates";
import { pageHref, withBase, releaseAsset } from "../src/lib/urls";
import { PAPER_CARD_KEYS, PUBLIC_PAPER_KEYS, type PaperCardData, type Taxonomy } from "../src/lib/contracts";
import paperSchema from "../src/lib/schema/public-paper.schema.json";
import cardSchema from "../src/lib/schema/paper-card.schema.json";

const taxonomy: Taxonomy = {
  version: "t",
  areas: [{ id: "proof-systems", display_name: "Proof systems" }],
  topics: [{ id: "zero-knowledge", display_name: "Zero knowledge", areas: ["proof-systems"] }],
  contributions: [{ id: "survey-tutorial", display_name: "Survey / tutorial" }],
};

const card: PaperCardData = {
  id: "w1", title: "<script>x</script> Title", authors: ["A", "B"],
  published: { year: 1985, month: null, day: null, precision: "year" },
  areas: ["proof-systems"], topics: ["zero-knowledge"], contributions: [], foundational: true,
  status: "active",
  sources: [
    { provider: "eprint", source_id: "2021/42", landing_url: "https://eprint.iacr.org/2021/042", pdf_url: null, version: null, status: "active" },
    { provider: "arxiv", source_id: "2101.00001", landing_url: "https://arxiv.org/abs/2101.00001", pdf_url: "https://arxiv.org/pdf/2101.00001v2", version: "v2", status: "withdrawn" },
  ],
  venue: "J. Cryptol.", doi: "10.9999/x", has_code: false, has_abstract: false, latest_update: null,
  canonical_path: "/proof-papers/papers/w1/", bibtex_path: "/proof-papers/citations/w1.bib",
  json_path: "/proof-papers/assets/releases/r/papers/w1.json",
};

describe("card model", () => {
  it("keeps titles as data, labels sources, and never fabricates a contribution", () => {
    const m = cardModel(card, taxonomy);
    expect(m.title).toBe("<script>x</script> Title");
    expect(m.contribution).toBe("Research paper");
    expect(m.date).toBe("1985 · publication year only");
    expect(m.approximateDate).toBe(true);
    expect(m.links.map((l) => l.label)).toEqual(["ePrint 2021/042", "arXiv 2101.00001v2", "Publisher", "PDF", "Cite"]);
    expect(m.statusNote).toBe("Withdrawn or deleted at: arXiv 2101.00001v2");
    expect(m.badges.map((b) => b.label)).toEqual(["Proof systems", "Zero knowledge"]);
  });

  it("formats authors and partial dates", () => {
    expect(authorsText([])).toBe("Authors unavailable");
    expect(authorsText(Array.from({ length: 12 }, (_, i) => `N${i}`))).toMatch(/and 2 more$/);
    expect(formatPublished({ year: 2026, month: 11, day: null, precision: "month" })).toBe("November 2026 · month only");
    expect(formatPublished({ year: 2026, month: 11, day: 3, precision: "day" })).toBe("3 November 2026");
    expect(formatPublished({ year: null, month: null, day: null, precision: "unknown" })).toBe("Date unknown");
    expect(sourceLabel(card.sources[0]!)).toBe("ePrint 2021/042");
  });
});

describe("dates and urls", () => {
  it("uses the injected clock and day arithmetic", () => {
    expect(todayUTC(new Date("2026-11-03T23:59:59Z"))).toBe("2026-11-03");
    expect(ageDays("2026-10-28", "2026-11-03")).toBe(6);
  });

  it("resolves paths under a non-root base", () => {
    expect(withBase("papers/x/", "/proof-papers/")).toBe("/proof-papers/papers/x/");
    expect(withBase("/feeds/new.atom", "/")).toBe("/feeds/new.atom");
    expect(pageHref("archive/2020", 1, "/proof-papers/")).toBe("/proof-papers/archive/2020/");
    expect(pageHref("archive/2020", 3, "/proof-papers/")).toBe("/proof-papers/archive/2020/3/");
    expect(releaseAsset("rel 1", "papers/a.json", "/p/")).toBe("/p/assets/releases/rel%201/papers/a.json");
  });
});

describe("contract alignment", () => {
  it("TypeScript key lists match the exported JSON Schemas", () => {
    expect([...PUBLIC_PAPER_KEYS].sort()).toEqual(Object.keys((paperSchema as any).properties).sort());
    expect([...PAPER_CARD_KEYS].sort()).toEqual(Object.keys((cardSchema as any).properties).sort());
  });

  it("release fixtures validate with the runtime validator", async () => {
    const dir = process.env.CATALOG_DIR;
    if (!dir) return;
    const { asPaper, asListPage, asManifest } = await import("../src/lib/validate");
    asManifest(JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf-8")));
    asListPage(JSON.parse(fs.readFileSync(path.join(dir, "lists/new/1.json"), "utf-8")));
    for (const f of fs.readdirSync(path.join(dir, "papers"))) {
      asPaper(JSON.parse(fs.readFileSync(path.join(dir, "papers", f), "utf-8")));
    }
    expect(() => asPaper({ id: "x" })).toThrow(/public schema/);
  });
});
