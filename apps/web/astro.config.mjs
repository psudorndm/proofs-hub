// Static build from one validated release export (CATALOG_DIR). No database access.
import { defineConfig } from "astro/config";

const site = process.env.PUBLIC_SITE_URL || "http://localhost:4321";
const base = process.env.PUBLIC_BASE_PATH || "/";

export default defineConfig({
  site,
  base,
  output: "static",
  trailingSlash: "always",
  publicDir: "./.catalog-public",
  outDir: "./dist",
  build: { format: "directory" },
  devToolbar: { enabled: false },
});
