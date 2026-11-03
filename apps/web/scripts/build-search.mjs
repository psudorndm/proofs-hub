// Build the Pagefind index from the generated canonical paper pages only (spec section 14).
// Navigation, About, status pages, aliases, and archive cards are never indexed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as pagefind from "pagefind";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(app, "dist");
const catalog = path.resolve(process.env.CATALOG_DIR ?? "");
const manifest = JSON.parse(fs.readFileSync(path.join(catalog, "manifest.json"), "utf-8"));
const ids = fs.readdirSync(path.join(catalog, "papers")).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)).sort();

function fail(message) {
  console.error(`build-search: ${message}`);
  process.exitCode = 1;
}

const { index, errors } = await pagefind.createIndex({ forceLanguage: "en" });
if (errors.length || !index) {
  fail(`could not create index: ${errors.join("; ")}`);
} else {
  let added = 0;
  for (const id of ids) {
    const paper = JSON.parse(fs.readFileSync(path.join(catalog, "papers", `${id}.json`), "utf-8"));
    const file = path.join(dist, "papers", id, "index.html");
    if (!fs.existsSync(file)) { fail(`missing generated page for ${id}`); continue; }
    const res = await index.addHTMLFile({ url: paper.canonical_path, content: fs.readFileSync(file, "utf-8") });
    if (res.errors.length) { fail(`${id}: ${res.errors.join("; ")}`); continue; }
    if (res.file?.meta?.id !== id) { fail(`${id}: indexed page lacks its id metadata`); continue; }
    added += 1;
  }
  const outputPath = path.join(dist, "assets", "releases", manifest.release_id, "pagefind");
  const written = await index.writeFiles({ outputPath });
  if (written.errors.length) fail(`write failed: ${written.errors.join("; ")}`);
  if (added !== manifest.included_work_count) fail(`indexed ${added} pages, manifest has ${manifest.included_work_count} works`);
  console.log(`build-search: indexed ${added} canonical paper pages -> ${path.relative(app, outputPath)}`);
}
await pagefind.close();
