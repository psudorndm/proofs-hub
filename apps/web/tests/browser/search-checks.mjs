// Real-index browser checks against a built site served by `astro preview`.
//   node tests/browser/search-checks.mjs http://127.0.0.1:4321/
// Requires a release built with `scripts/build_fixture_release.py --bulk 40`.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch { ({ chromium } = require(`${process.env.NODE_PATH}/playwright`)); }

const base = process.argv[2] ?? "http://127.0.0.1:4321/";
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
// The fixture's as-of date is 2026-11-03; pin the browser clock so windows stay deterministic.
await page.clock.setFixedTime(new Date("2026-11-03T12:00:00Z"));
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

async function search(query) {
  await page.goto(`${base}search/${query ? `?${query}` : ""}`);
  await page.waitForFunction(() => {
    const t = document.querySelector("[data-result-count]")?.textContent ?? "";
    return t && t !== "Searching…";
  }, null, { timeout: 15000 });
  const count = await page.textContent("[data-result-count]");
  const titles = await page.$$eval("[data-search-results] .paper-title a", (as) => as.map((a) => a.textContent));
  const empty = await page.$eval("[data-search-results]", (el) => el.querySelector(".empty")?.textContent ?? null);
  return { count, titles, empty, total: Number.parseInt(count, 10) };
}

const F01 = "Zero-knowledge proofs for private arithmetic computations";
const F13 = "Zero-knowledge proofs with tight security reductions for signature schemes";
const F12 = "Succinct arguments for batch statements";

let r = await search("q=zero-knowledge");
check("text query returns more than one page", r.total > 25 && r.titles.length === 25, r.count);
const firstPage = r.titles;
await page.click(".pagination a:has-text('Next')");
await page.waitForFunction(() => location.search.includes("page=2"));
await page.waitForFunction((first) => {
  const t = [...document.querySelectorAll("[data-search-results] .paper-title a")].map((a) => a.textContent);
  return t.length > 0 && t[0] !== first;
}, firstPage[0]);
const second = await page.$$eval("[data-search-results] .paper-title a", (as) => as.map((a) => a.textContent));
check("pagination shows a different page", second.length > 0 && second[0] !== firstPage[0]);
await page.goBack();
await page.waitForFunction(() => !location.search.includes("page=2"));
await page.waitForFunction((first) => document.querySelector("[data-search-results] .paper-title a")?.textContent === first, firstPage[0]);
check("test_url_state_round_trip_and_browser_back", true);

r = await search("source=eprint&source=arxiv");
check("source OR deduplicates canonical works", r.titles.filter((t) => t === F01).length === 1 || r.total > 25, r.count);
r = await search(`source=eprint&source=arxiv&q=${encodeURIComponent("private arithmetic")}`);
check("both-source work appears once", r.titles.filter((t) => t === F01).length === 1, JSON.stringify(r.titles));

r = await search("area=proof-systems&area=security-proofs&q=tight");
check("area OR returns multi-area work once", r.titles.filter((t) => t === F13).length === 1, JSON.stringify(r.titles));
r = await search("area=formal-verification&source=arxiv");
check("cross-facet AND narrows results", r.total === 0, r.count);
r = await search("area=formal-verification");
check("single area facet", r.total === 1, r.count);
const current = await page.$$eval("a[data-area][aria-current]", (as) => as.map((a) => a.dataset.area));
check("area tab reflects URL state", current.length === 1 && current[0] === "formal-verification", JSON.stringify(current));

r = await search("window=unknown");
check("explicit unknown-date selection", r.total === 1 && r.titles[0] === F12, JSON.stringify(r.titles));
r = await search("window=7");
check("rolling window uses day-precision dates only", r.titles.includes(F13) && !r.titles.includes(F12), JSON.stringify(r.titles));

for (const sort of ["newest", "oldest"]) {
  r = await search(`sort=${sort}`);
  const pages = Math.ceil(r.total / 25);
  r = await search(`sort=${sort}&page=${pages}`);
  check(`test_unknown_dates_sort_last (${sort})`, r.titles.at(-1) === F12, r.titles.at(-1));
}

r = await search("q=2020/007");
check("exact identifier search", r.total === 1 && r.titles[0] === F01, JSON.stringify(r));
r = await search(`q=${encodeURIComponent("https://doi.org/10.9999/none")}`);
check("unknown identifier shows zero with the query", r.total === 0 && (r.empty ?? "").includes("10.9999/none"), r.empty);

await page.goto(`${base}search/?year_from=2026&year_to=1980`);
await page.waitForSelector("[data-search-results] .empty");
const invalid = await page.textContent("[data-search-results] .empty");
check("reversed range is rejected inline", invalid.includes("From year must not be after To year"), invalid);

r = await search("year_from=1700&year_to=1701");
check("constrained empty date set returns zero", r.total === 0, r.count);

// Parity: static card (home page) vs. client-rendered card for the same work.
await page.goto(base);
const staticCard = await page.$eval(".paper-card", (el) => ({ id: el.dataset.paperId, html: el.outerHTML }));
r = await search(`q=tight&area=security-proofs`);
const dynamicCard = await page.$eval(`[data-search-results] .paper-card[data-paper-id="${staticCard.id}"]`, (el) => el.outerHTML);
const norm = (html) => html.replace(/\s+/g, " ").replace(/> </g, "><").replace(/ data-astro-[\w-]+="[^"]*"/g, "").replace(/ class=""/g, "").trim();
check("static and dynamic card parity", norm(staticCard.html) === norm(dynamicCard), norm(staticCard.html) === norm(dynamicCard) ? "" : `${norm(staticCard.html)}\n---\n${norm(dynamicCard)}`);

// Release mismatch: a detail payload from another release must not be merged.
await page.route("**/papers/*.json", async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.release_id = "another-release";
  await route.fulfill({ response: res, json: body });
});
await page.goto(`${base}search/?q=zero-knowledge`);
await page.waitForSelector("[data-search-results] .empty");
const mismatch = await page.textContent("[data-search-results] .empty");
check("test_release_mismatch_requests_reload_not_silent_merge", mismatch.includes("Reload"), mismatch);
await page.unroute("**/papers/*.json");

// Index failure message.
await page.route("**/pagefind/pagefind.js", (route) => route.abort());
await page.goto(`${base}search/?q=zero`);
await page.waitForSelector("[data-search-results] .empty");
const failed = await page.textContent("[data-search-results] .empty");
check("search index failure message", failed.includes("Search could not load"), failed);
await page.unroute("**/pagefind/pagefind.js");

// Mobile layout of the results page.
await page.setViewportSize({ width: 390, height: 900 });
await search("q=zero-knowledge");
const sw = await page.evaluate(() => document.documentElement.scrollWidth);
check("no horizontal overflow at 390px on search results", sw <= 390, String(sw));

const unexpected = errors.filter((e) => !e.includes("net::ERR_FAILED") && !e.includes("pagefind.js"));
check("no JavaScript errors during search/pagination", unexpected.length === 0, unexpected.join(" | "));
await browser.close();
for (const { name, ok, detail } of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}${!ok && detail ? ` :: ${detail}` : ""}`);
const failedCount = results.filter((x) => !x.ok).length;
console.log(`${results.length - failedCount}/${results.length} browser checks passed`);
process.exit(failedCount ? 1 : 0);
