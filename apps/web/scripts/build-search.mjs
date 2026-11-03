// Pagefind index: one record per published canonical work (spec section 14).
// The site has no per-paper pages for now, so records are added with the Node API's
// addCustomRecord. Weighting (title 3, authors 2, body 1) is approximated by repeating
// the title three times and the authors twice in the record text (decision D017).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as pagefind from "pagefind";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(app, "dist");
const catalog = path.resolve(process.env.CATALOG_DIR ?? "");
const read = (rel) => JSON.parse(fs.readFileSync(path.join(catalog, rel), "utf-8"));
const manifest = read("manifest.json");
const searchData = read("search-index-data.json").papers;
const ids = fs.readdirSync(path.join(catalog, "papers")).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)).sort();
const pad = (n) => String(n).padStart(8, "0");
let failed = false;
const fail = (m) => { console.error(`build-search: ${m}`); failed = true; };

function cardOf(p) {
  return {
    id: p.id, title: p.title, authors: p.authors.map((a) => a.display_name), published: p.published,
    areas: p.areas, topics: p.topics, contributions: p.contributions, foundational: p.foundational,
    status: p.status, sources: p.sources,
    venue: p.publications.find((x) => x.venue_name)?.venue_name ?? null,
    doi: p.publications.find((x) => x.doi)?.doi ?? null,
    has_code: p.code_links.length > 0, has_abstract: p.abstract !== null,
    latest_update: p.latest_update, canonical_path: p.canonical_path, bibtex_path: p.bibtex_path,
    json_path: `${manifest.public_base_path}assets/releases/${manifest.release_id}/papers/${p.id}.json`,
  };
}

const { index, errors } = await pagefind.createIndex({ forceLanguage: "en" });
if (errors.length || !index) fail(`could not create index: ${errors.join("; ")}`);
let added = 0;
for (const id of failed ? [] : ids) {
  const p = read(`papers/${id}.json`);
  const s = searchData[id];
  if (!s) { fail(`${id}: no search data`); continue; }
  const authors = p.authors.map((a) => a.display_name).join(", ");
  const content = [p.title, p.title, p.title, authors, authors, p.abstract ?? "", s.identifiers.join(" "),
    p.topics.join(" ")].join("\n");
  const res = await index.addCustomRecord({
    url: p.canonical_path,
    content,
    language: "en",
    meta: { title: p.title, id, release: manifest.release_id, card: JSON.stringify(cardOf(p)) },
    filters: {
      area: p.areas, source: [...new Set(p.sources.map((x) => x.provider))], status: [p.status],
      identifier: s.identifiers, publication_period: [s.publication_period],
    },
    sort: { rank_newest: pad(s.rank_newest), rank_oldest: pad(s.rank_oldest), rank_updated: pad(s.rank_updated) },
  });
  if (res.errors.length) { fail(`${id}: ${res.errors.join("; ")}`); continue; }
  added += 1;
}
const outputPath = path.join(dist, "assets", "releases", manifest.release_id, "pagefind");
if (index && !failed) {
  const written = await index.writeFiles({ outputPath });
  if (written.errors.length) fail(`write failed: ${written.errors.join("; ")}`);
}
if (added !== manifest.included_work_count) fail(`indexed ${added} records, manifest has ${manifest.included_work_count} works`);
await pagefind.close();
if (failed) process.exit(1);
console.log(`build-search: indexed ${added} works -> ${path.relative(app, outputPath)}`);
