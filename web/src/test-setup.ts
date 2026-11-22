import "@testing-library/jest-dom/vitest";

// jsdom lacks ResizeObserver, which Radix's Checkbox needs.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
