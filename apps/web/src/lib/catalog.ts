// Build-time loader for one validated release export (CATALOG_DIR). Node only.
import fs from "node:fs";
import path from "node:path";
import type { ListPage, Manifest, PaperCardData, PublicPaper, Taxonomy } from "./contracts";
import { asListPage, asManifest, asPaper } from "./validate";
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

export function listPages(rel: string): ListPage[] {
  const dir = path.join(catalogDir(), rel);
  if (!fs.existsSync(dir)) return [];
  const pages = fs.readdirSync(dir).filter((f: string) => /^\d+\.json$/.test(f))
    .sort((a: string, b: string) => Number.parseInt(a, 10) - Number.parseInt(b, 10));
  return pages.map((f: string) => {
    const page = asListPage(readJson(`${rel}/${f}`));
    if (page.release_id !== manifest().release_id) throw new Error(`${rel}/${f}: release mismatch`);
    return page;
  });
}

const paperCache = new Map<string, PublicPaper>();
export function paper(id: string): PublicPaper {
  let found = paperCache.get(id);
  if (!found) {
    found = asPaper(readJson(`papers/${id}.json`));
    if (found.release_id !== manifest().release_id) throw new Error(`paper ${id}: release mismatch`);
    paperCache.set(id, found);
  }
  return found;
}

export function paperIds(): string[] {
  return fs.readdirSync(path.join(catalogDir(), "papers"))
    .filter((f: string) => f.endsWith(".json")).map((f: string) => f.slice(0, -5)).sort();
}

export function abstractOf(card: PaperCardData): string | null {
  return paper(card.id).abstract;
}

export function aliases(): Record<string, string> {
  return (readJson("aliases.json") as { aliases: Record<string, string> }).aliases;
}

export function coverage(): any {
  return readJson("coverage.json");
}

export function status(): any {
  return readJson("status.json");
}
