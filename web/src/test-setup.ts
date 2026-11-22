import "@testing-library/jest-dom/vitest";

// jsdom lacks ResizeObserver, which Radix's Checkbox needs.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom lacks IntersectionObserver, which the review screen uses to load more paragraphs.
// Tests that need to trigger it stub their own.
if (!("IntersectionObserver" in globalThis)) {
  globalThis.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  } as unknown as typeof IntersectionObserver;
}
