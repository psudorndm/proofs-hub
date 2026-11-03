// Copy what the static site needs from one validated release (CATALOG_DIR).
// The release is consumed read-only; a failed build never modifies it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(app, ".catalog-public");
const fail = (message) => { console.error(`prepare-catalog: ${message}`); process.exit(1); };

const catalog = process.env.CATALOG_DIR;
if (!catalog) fail("CATALOG_DIR must point at a validated release directory");
const dir = path.resolve(catalog);
if (!fs.existsSync(path.join(dir, "manifest.json"))) fail(`${dir} has no manifest.json`);
const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf-8"));
const base = process.env.PUBLIC_BASE_PATH || "/";
if (manifest.public_base_path !== base) {
  fail(`release was exported for base path ${manifest.public_base_path} but PUBLIC_BASE_PATH is ${base}`);
}
const releaseSchema = fs.readFileSync(path.join(dir, "schema/public-paper.schema.json"), "utf-8");
const supported = fs.readFileSync(path.join(app, "src/lib/schema/public-paper.schema.json"), "utf-8");
if (JSON.stringify(JSON.parse(releaseSchema)) !== JSON.stringify(JSON.parse(supported))) {
  fail("release schema differs from the frontend's supported schema (run `make schema` and rebuild)");
}
fs.rmSync(out, { recursive: true, force: true });
const releaseDir = path.join(out, "assets", "releases", manifest.release_id);
fs.mkdirSync(releaseDir, { recursive: true });
fs.copyFileSync(path.join(dir, "manifest.json"), path.join(releaseDir, "manifest.json"));
fs.writeFileSync(path.join(out, ".nojekyll"), "");
console.log(`prepare-catalog: release ${manifest.release_id} (${manifest.dataset_mode}), ${manifest.included_work_count} works`);
