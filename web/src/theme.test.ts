/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Read from disk: under vitest a stylesheet imports as an empty string.
const read = (path: string) => readFileSync(resolve(__dirname, path), "utf8");
const css = read("index.css");
const bookCss = read("components/preview/book.css");
const html = read("../index.html");

/** The body of the first rule that starts a line with `selector {`. */
function rule(selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  if (start < 0) throw new Error(`no ${selector} rule in index.css`);
  return css.slice(start, css.indexOf("\n}", start));
}

function token(block: string, name: string): string {
  const found = block.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (!found) throw new Error(`no --${name}`);
  return found[1].trim();
}

/** Relative luminance of an `oklch(L C H)` colour. */
function luminance(oklch: string): number {
  const parts = oklch.match(/oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)/);
  if (!parts) throw new Error(`not a plain oklch colour: ${oklch}`);
  const [L, C, H] = parts.slice(1).map(Number);
  const a = C * Math.cos((H * Math.PI) / 180);
  const b = C * Math.sin((H * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const red = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const green = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const blue = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(one: string, other: string): number {
  const [hi, lo] = [luminance(one), luminance(other)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("the theme", () => {
  it("loads the animation classes the overlays already use", () => {
    expect(css).toMatch(/@import "tw-animate-css";/);
  });

  it("is paper and ink, with a gilt accent, in both themes", () => {
    const light = rule(":root");
    const dark = rule(".dark");
    expect(token(light, "background")).toBe("oklch(0.985 0.006 85)");
    expect(token(dark, "background")).toBe("oklch(0.17 0.008 70)");
    expect(token(light, "brand")).toBe("oklch(0.72 0.13 78)");
    expect(token(dark, "brand")).toBe("oklch(0.8 0.12 82)");
    expect(token(light, "brand-foreground")).toBe("oklch(0.24 0.02 60)");
    expect(token(dark, "brand-foreground")).toBe("oklch(0.2 0.01 70)");
    expect(token(light, "ring")).toBe("oklch(0.72 0.13 78 / 55%)");
    expect(token(dark, "ring")).toBe("oklch(0.8 0.12 82 / 55%)");
  });

  it("maps the brand colour and the two faces into Tailwind", () => {
    const theme = rule("@theme inline");
    expect(token(theme, "color-brand")).toBe("var(--brand)");
    expect(token(theme, "color-brand-foreground")).toBe("var(--brand-foreground)");
    expect(token(theme, "font-sans")).toMatch(/^"Inter Variable"/);
    expect(token(theme, "font-display")).toMatch(/^"EB Garamond"/);
  });

  it.each([
    [":root", "background"],
    [":root", "muted"],
    [":root", "card"],
    [".dark", "background"],
    [".dark", "muted"],
    [".dark", "card"],
  ])("keeps muted text readable in %s on %s", (selector, surface) => {
    const block = rule(selector);
    expect(contrast(token(block, "muted-foreground"), token(block, surface))).toBeGreaterThanOrEqual(4.5);
  });

  it.each([":root", ".dark"])("keeps body text readable in %s", (selector) => {
    const block = rule(selector);
    expect(contrast(token(block, "foreground"), token(block, "background"))).toBeGreaterThanOrEqual(7);
    expect(contrast(token(block, "primary-foreground"), token(block, "primary"))).toBeGreaterThanOrEqual(7);
    expect(contrast(token(block, "brand-foreground"), token(block, "brand"))).toBeGreaterThanOrEqual(4.5);
  });

  it("defines the motion layer", () => {
    for (const name of ["page-enter", "row-enter", "card-enter", "shimmer", "pulse-dot", "skeleton"]) {
      expect(css).toContain(`@keyframes ${name} {`);
    }
    for (const name of ["page-enter", "row-enter", "card-enter", "shimmer", "pulse-dot", "skeleton", "pressable"]) {
      expect(css).toMatch(new RegExp(`\\n  \\.${name}(::after)? \\{`));
    }
    expect(css).toContain("--ease-out: cubic-bezier(0.22, 1, 0.36, 1);");
    expect(css).toContain("--dur-fast: 120ms;");
    expect(css).toContain("--dur-base: 180ms;");
    expect(css).toContain("--dur-page: 240ms;");
  });

  it("turns every animation and transition off for reduced motion", () => {
    const start = css.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(start).toBeGreaterThan(-1);
    const block = css.slice(start);
    expect(block).toMatch(/\*,\s*\*::before,\s*\*::after \{/);
    expect(block).toContain("animation-duration: 0.01ms !important;");
    expect(block).toContain("animation-iteration-count: 1 !important;");
    expect(block).toContain("transition-duration: 0.01ms !important;");
    expect(block).toContain("scroll-behavior: auto !important;");
  });

  it("declares the book face once, in the main stylesheet", () => {
    expect(css.match(/@font-face \{\s*font-family: "EB Garamond";/g)).toHaveLength(4);
    expect(bookCss).not.toContain("@font-face");
  });

  it("shows keyboard focus on a plain link as a solid gilt outline", () => {
    // The ring token carries its own alpha: halving it again made focus nearly invisible.
    expect(css).not.toContain("outline-ring/50");
    expect(css).toMatch(/\n  a:focus-visible \{\s*outline: 2px solid var\(--brand\);\s*outline-offset: 2px;/);
  });

  it("links the favicon", () => {
    expect(html).toMatch(/<link rel="icon" type="image\/svg\+xml" href="\/favicon\.svg" \/>/);
  });
});
