import { describe, expect, it } from "vitest";
import { hasMath, renderMathSafe, splitMath } from "../src/lib/math";

describe("math rendering", () => {
  it("renders inline math with KaTeX and escapes the surrounding text", () => {
    const html = renderMathSafe("Arguments for <NP> with $O(\\log n)$ proofs & more");
    expect(html).toContain('class="katex"');
    expect(html).toContain("&lt;NP&gt;");
    expect(html).toContain("&amp; more");
    expect(html).not.toContain("<NP>");
  });

  it("falls back to escaped original notation on unsupported syntax", () => {
    const html = renderMathSafe("Bad $\\frac{1}{$ math and $\\notacommand{x}$");
    expect(html).toContain('<span class="math-fallback">$\\frac{1}{$</span>');
    expect(html).toContain('<span class="math-fallback">$\\notacommand{x}$</span>');
  });

  it("never turns trusted-only commands into links or HTML", () => {
    const html = renderMathSafe("$\\href{javascript:alert(1)}{x}$ and $\\htmlId{a}{b}$");
    expect(html).not.toMatch(/<a[\s>]/);
    expect(html).not.toMatch(/\shref=/);
    expect(html).not.toContain('id="a"');
    expect(html).toContain('<span class="math-fallback">$\\href{javascript:alert(1)}{x}$</span>');
  });

  it("does not treat currency as math", () => {
    expect(hasMath("It costs $5 and $10 per proof")).toBe(false);
    expect(renderMathSafe("It costs $5 and $10")).toBe("It costs $5 and $10");
    expect(splitMath("Use \\(x\\) or $$y$$").filter((s) => s.math).map((s) => s.body)).toEqual(["x", "y"]);
  });

  it("escapes script content in plain text", () => {
    expect(renderMathSafe("<script>alert(1)</script>")).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
  });
});
