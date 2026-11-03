// Copy one validated release (CATALOG_DIR) into the static public directory.
// The release is consumed read-only; a failed build never modifies it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "..");
const out = path.join(app, ".catalog-public");

function fail(message) {
  console.error(`prepare-catalog: ${message}`);
  process.exit(1);
}

const catalog = process.env.CATALOG_DIR;
if (!catalog) fail("CATALOG_DIR must point at a validated release directory");
const dir = path.resolve(catalog);
const manifestPath = path.join(dir, "manifest.json");
if (!fs.existsSync(manifestPath)) fail(`${dir} has no manifest.json`);
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
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
fs.mkdirSync(out, { recursive: true });
const releases = path.join(out, "assets", "releases");
function copyRelease(src) {
  const m = JSON.parse(fs.readFileSync(path.join(src, "manifest.json"), "utf-8"));
  fs.cpSync(src, path.join(releases, m.release_id), { recursive: true });
  return m.release_id;
}
const current = copyRelease(dir);
// Keep up to two previous releases' assets so already-open pages can finish requests.
const previous = (process.env.PREVIOUS_CATALOG_DIRS || "").split(path.delimiter).filter(Boolean).slice(0, 2);
const kept = previous.map((p) => copyRelease(path.resolve(p)));
fs.cpSync(path.join(dir, "feeds"), path.join(out, "feeds"), { recursive: true });
fs.cpSync(path.join(dir, "citations"), path.join(out, "citations"), { recursive: true });
fs.writeFileSync(path.join(releases, "current.json"), JSON.stringify({ release_id: current, previous: kept }));
fs.writeFileSync(path.join(out, ".nojekyll"), "");
console.log(`prepare-catalog: release ${current} (${manifest.dataset_mode}), ${manifest.included_work_count} works`);
