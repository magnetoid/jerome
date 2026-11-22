// The API counts offsets in Unicode code points (Python str indices); JavaScript strings
// count UTF-16 units. Text outside the BMP (𝔄, emoji) is one code point but two units, so
// slicing with the API's offsets directly would misplace every mark after it.

const isHigh = (u: number) => u >= 0xd800 && u <= 0xdbff;
const isLow = (u: number) => u >= 0xdc00 && u <= 0xdfff;

/** The UTF-16 index where code point `cp` starts in `text`, clamped to [0, text.length]. */
export function cpToUtf16(text: string, cp: number): number {
  let i = 0;
  for (let n = 0; n < cp && i < text.length; n++) {
    // A lone surrogate counts as one code point, as it does in Python.
    i += isHigh(text.charCodeAt(i)) && isLow(text.charCodeAt(i + 1)) ? 2 : 1;
  }
  return i;
}

/** `text` between code points `start` and `end`. */
export function cpSlice(text: string, start: number, end: number): string {
  const from = cpToUtf16(text, start);
  return text.slice(from, from + cpToUtf16(text.slice(from), end - start));
}
