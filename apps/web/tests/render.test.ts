// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderPaperLine } from "../src/lib/render";
import { card } from "./format.test";

describe("renderPaperLine", () => {
  it("test_untrusted_title_and_link_are_safe in the client renderer", () => {
    const li = renderPaperLine(document, { ...card, title: "<img src=x onerror=alert(1)> T", authors: ["<b>A</b>"] });
    expect(li.querySelector("img")).toBeNull();
    expect(li.querySelector("b")).toBeNull();
    expect(li.querySelector(".paper-title")!.textContent).toBe("<img src=x onerror=alert(1)> T");
    expect([...li.querySelectorAll("a")].every((a) => a.href.startsWith("https://"))).toBe(true);
  });

  it("uses the shared structure, classes, and filter data", () => {
    const li = renderPaperLine(document, card);
    expect(li.className).toBe("paper");
    expect([...li.children].map((c) => c.className)).toEqual(["meta", "paper-title", "authors"]);
    expect(li.querySelector(".tag")).toBeNull();
    expect(li.dataset.sources).toBe("arxiv eprint dblp");
    expect(li.querySelector(".meta")!.textContent).toBe("[eprint] 2021/042[arxiv] 2101.00001");
  });
});
