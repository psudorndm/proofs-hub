import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

// WCAG 2.x relative luminance and contrast ratio.
function luminance(hex: string): number {
  const v = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = Number.parseInt(v.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const css = fs.readFileSync(path.join(import.meta.dirname, "../src/styles/tokens.css"), "utf-8");
function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--(color-[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1]!, m[2]!]));
}
const light = tokens(css.split("@media (prefers-color-scheme: dark)")[0]!);
const dark = { ...light, ...tokens(css.split("@media (prefers-color-scheme: dark)")[1]!) };

const PAIRS: [string, string][] = [
  ["color-text", "color-bg"], ["color-text", "color-surface"], ["color-muted", "color-bg"],
  ["color-muted", "color-surface"], ["color-accent", "color-surface"], ["color-accent", "color-bg"],
  ["color-ps-text", "color-ps-bg"], ["color-sp-text", "color-sp-bg"], ["color-fv-text", "color-fv-bg"],
  ["color-topic-text", "color-topic-bg"], ["color-warn-text", "color-warn-bg"],
  ["color-preview-text", "color-preview-bg"],
];

describe("token contrast (WCAG AA, 4.5:1)", () => {
  for (const [name, scheme] of [["light", light], ["dark", dark]] as const) {
    for (const [fg, bg] of PAIRS) {
      it(`${name}: ${fg} on ${bg}`, () => {
        expect(contrast(scheme[fg]!, scheme[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
  it("white button text on the light accent", () => {
    expect(contrast("#ffffff", light["color-accent"]!)).toBeGreaterThanOrEqual(4.5);
  });
});
