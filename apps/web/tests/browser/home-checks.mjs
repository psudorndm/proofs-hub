// Browser checks for the single-page index, against `astro preview` of a real build.
//   node tests/browser/home-checks.mjs http://127.0.0.1:4321/
// Expects a fixture release built with `scripts/build_fixture_release.py --bulk 40`.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch { ({ chromium } = require(`${process.env.NODE_PATH}/playwright`)); }

const base = process.argv[2] ?? "http://127.0.0.1:4321/";
const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok, detail });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.clock.setFixedTime(new Date("2026-11-03T12:00:00Z"));
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("ERR_FAILED")) errors.push(m.text()); });

const F01 = "Zero-knowledge proofs for private arithmetic computations";
const F13 = "Zero-knowledge proofs with tight security reductions for signature schemes";
const visibleTitles = (scope) => page.$$eval(`${scope} li.paper:not([hidden]) .paper-title`, (n) => n.map((x) => x.textContent));
const sectionTitles = (id) => visibleTitles(`section[aria-labelledby="${id}"]`);
async function searchFor(q) {
  await page.fill("#q", q);
  // Wait for the debounced update to reach the URL, then for the search to finish.
  await page.waitForFunction((want) => (new URLSearchParams(location.search).get("q") ?? "") === want, q);
  if (q === "") return { count: "", titles: [] };
  await page.waitForFunction(() => {
    const t = document.querySelector("[data-count]")?.textContent ?? "";
    return t !== "" && !t.startsWith("searching");
  }, null, { timeout: 15000 });
  return { count: await page.textContent("[data-count]"), titles: await visibleTitles("[data-results]") };
}

await page.goto(base);
check("three windows are shown", (await page.$$eval("[data-window] h2", (h) => h.map((x) => x.textContent))).join("|") === "this week|this month|this year");
check("this week lists the recent work", (await sectionTitles("this-week")).includes(F13));
check("empty window says so", await page.$eval('section[aria-labelledby="this-month"] [data-empty]', (n) => !n.hidden));
check("preview label is shown", (await page.textContent(".preview")).includes("Preview dataset — not the live research index."));

await page.click('button[data-area="formal-verification"]');
check("area toggle filters the lists", (await sectionTitles("this-week")).length === 0 && await page.$eval('section[aria-labelledby="this-week"] [data-empty]', (n) => !n.hidden));
check("area toggle updates the URL", page.url().includes("area=formal-verification"));
await page.click('button[data-area="security-proofs"]');
check("a work with two areas matches either", (await sectionTitles("this-week")).includes(F13));
await page.click('button[data-source="arxiv"]');
check("area and source combine (AND)", (await sectionTitles("this-week")).includes(F13), "F13 has an arXiv source");
await page.goBack();
check("back restores the previous toggle", page.url().includes("area=security-proofs") && !page.url().includes("source=") &&
  await page.$eval('button[data-source=""]', (b) => b.getAttribute("aria-pressed") === "true"));
await page.click('button[data-area=""]');

let r = await searchFor("tight security");
check("search replaces the lists with results", r.titles.includes(F13) && await page.$eval("[data-recent]", (n) => n.hidden), r.count);
r = await searchFor("2020/007");
check("exact identifier search", r.titles.length === 1 && r.titles[0] === F01, JSON.stringify(r));
r = await searchFor("10.9999/nothing-here");
check("unknown identifier says so", r.titles.length === 0 && r.count.includes("no paper with identifier"), r.count);
r = await searchFor("zero-knowledge");
check("long result lists are capped with a note", r.titles.length <= 100 && /\d+ papers?/.test(r.count), r.count);
await page.click('button[data-area="formal-verification"]');
await page.waitForFunction(() => {
  const t = document.querySelector("[data-count]")?.textContent ?? "";
  return location.search.includes("area=formal-verification") && t !== "" && !t.startsWith("searching");
});
const fv = await visibleTitles("[data-results]");
check("toggles also filter search results", fv.length < r.titles.length, `${fv.length} of ${r.titles.length}`);
await page.click('button[data-area=""]');
r = await searchFor("tags");
const unsafe = await page.$$eval("[data-results] li.paper", (lis) => lis.map((li) => ({ text: li.querySelector(".paper-title").textContent, scripts: li.querySelectorAll("script, b, img").length })));
check("untrusted title text is displayed safely", unsafe.some((u) => u.text.includes("<script>")) && unsafe.every((u) => u.scripts === 0), JSON.stringify(unsafe));
await searchFor("");
check("clearing search brings the lists back", await page.$eval("[data-recent]", (n) => !n.hidden));

// Parity: the static entry and the client-rendered entry for the same work.
await page.goto(base);
const staticHtml = await page.$eval("[data-window] li.paper", (li) => li.outerHTML);
const id = await page.$eval("[data-window] li.paper", (li) => li.dataset.paperId);
await searchFor("tight security");
const dynamicHtml = await page.$eval(`[data-results] li[data-paper-id="${id}"]`, (li) => li.outerHTML);
const norm = (h) => h.replace(/\s+/g, " ").replace(/> </g, "><").replace(/ data-astro-[\w-]+="[^"]*"/g, "").trim();
check("static and client-rendered entries match", norm(staticHtml) === norm(dynamicHtml), `${norm(staticHtml)}\n---\n${norm(dynamicHtml)}`);

// Without JavaScript: lists are readable, controls are hidden.
const nojs = await browser.newPage({ javaScriptEnabled: false });
await nojs.goto(base);
check("lists work without JavaScript", (await nojs.$$("[data-window] li.paper")).length > 0 && await nojs.$eval("[data-controls]", (n) => n.hidden));
await nojs.close();

await page.setViewportSize({ width: 390, height: 900 });
await page.goto(base);
const sw = await page.evaluate(() => document.documentElement.scrollWidth);
check("no horizontal overflow at 390px", sw <= 390, String(sw));
check("no JavaScript errors", errors.length === 0, errors.join(" | "));

await browser.close();
for (const { name, ok, detail } of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}${!ok && detail ? ` :: ${detail}` : ""}`);
const failed = results.filter((x) => !x.ok).length;
console.log(`${results.length - failed}/${results.length} browser checks passed`);
process.exit(failed ? 1 : 0);
