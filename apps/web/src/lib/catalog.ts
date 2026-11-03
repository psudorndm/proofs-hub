// Build-time loader for one validated release export (CATALOG_DIR). Node only.
import fs from "node:fs";
import path from "node:path";
import type { Manifest, RecentLists, Taxonomy } from "./contracts";
import { asManifest, asRecent } from "./validate";
import supportedSchema from "./schema/public-paper.schema.json";

function catalogDir(): string {
  const dir = process.env.CATALOG_DIR;
  if (!dir) throw new Error("CATALOG_DIR must point at a validated release directory");
  return path.resolve(dir);
}

function readJson(rel: string): unknown {
  return JSON.parse(fs.readFileSync(path.join(catalogDir(), rel), "utf-8"));
}

let cachedManifest: Manifest | undefined;
export function manifest(): Manifest {
  if (!cachedManifest) {
    const releaseSchema = readJson("schema/public-paper.schema.json");
    if (JSON.stringify(releaseSchema) !== JSON.stringify(supportedSchema)) {
      throw new Error("Release schema differs from the schema this frontend supports; rebuild both");
    }
    cachedManifest = asManifest(readJson("manifest.json"));
  }
  return cachedManifest;
}

export function taxonomy(): Taxonomy {
  return readJson("taxonomy.json") as Taxonomy;
}

export function recent(): RecentLists {
  const lists = asRecent(readJson("lists/recent.json"));
  if (lists.release_id !== manifest().release_id) throw new Error("lists/recent.json: release mismatch");
  return lists;
}
