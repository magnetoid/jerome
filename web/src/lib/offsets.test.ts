import { describe, expect, it } from "vitest";
import { cpSlice, cpToUtf16 } from "./offsets";

describe("cpSlice", () => {
  it("slices by code points, not UTF-16 units (Review Focus 1)", () => {
    expect(cpSlice("𝔄 teh cat", 2, 5)).toBe("teh");
    // The naive slice is off by one because 𝔄 is two UTF-16 units.
    expect("𝔄 teh cat".slice(2, 5)).not.toBe("teh");
  });

  it("keeps surrogate pairs whole at the slice boundaries", () => {
    const text = "a😀b𝔄c";
    expect(cpSlice(text, 1, 2)).toBe("😀");
    expect(cpSlice(text, 0, 2)).toBe("a😀");
    expect(cpSlice(text, 2, 4)).toBe("b𝔄");
    expect(cpSlice(text, 3, 5)).toBe("𝔄c");
    expect(cpSlice(text, 1, 4)).toBe("😀b𝔄");
  });

  it("handles empty spans and the end of the text", () => {
    expect(cpSlice("𝔄 teh", 2, 2)).toBe("");
    expect(cpSlice("𝔄 teh", 2, 99)).toBe("teh");
    expect(cpSlice("", 0, 3)).toBe("");
  });
});

describe("cpToUtf16", () => {
  it("is the identity for text inside the BMP", () => {
    expect(cpToUtf16("teh cat", 0)).toBe(0);
    expect(cpToUtf16("teh cat", 4)).toBe(4);
    expect(cpToUtf16("Straße", 6)).toBe(6);
  });

  it("counts an emoji or astral letter as one code point and two units", () => {
    expect(cpToUtf16("😀x", 1)).toBe(2);
    expect(cpToUtf16("😀x", 2)).toBe(3);
    expect(cpToUtf16("𝔄𝔅 teh", 2)).toBe(4);
    expect(cpToUtf16("𝔄𝔅 teh", 3)).toBe(5);
  });

  it("counts a combined emoji sequence as its separate code points", () => {
    // 👍🏽 is U+1F44D U+1F3FD: two code points, four UTF-16 units.
    expect(cpToUtf16("👍🏽!", 1)).toBe(2);
    expect(cpToUtf16("👍🏽!", 2)).toBe(4);
  });

  it("clamps to the text length and to zero", () => {
    expect(cpToUtf16("𝔄b", 10)).toBe(3);
    expect(cpToUtf16("abc", -1)).toBe(0);
  });

  it("treats a lone surrogate as one code point, like Python does", () => {
    expect(cpToUtf16("\ud835x", 1)).toBe(1);
    expect(cpToUtf16("\ud835x", 2)).toBe(2);
  });
});
