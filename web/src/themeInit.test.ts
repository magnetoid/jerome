import { afterEach, describe, expect, it, vi } from "vitest";
import html from "../index.html?raw";

const match = html.match(/<script id="theme-init">([\s\S]*?)<\/script>/);

function run() {
  if (!match) throw new Error("no #theme-init script in index.html");
  new Function(match[1])();
}

function darkMedia(matches: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches, media: q }));
}

afterEach(() => vi.unstubAllGlobals());

describe("theme-init script", () => {
  it("applies a stored dark theme", () => {
    localStorage.setItem("jerome-theme", "dark");
    run();
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("stored light wins over a dark system", () => {
    localStorage.setItem("jerome-theme", "light");
    darkMedia(true);
    run();
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("follows a dark system when nothing is stored", () => {
    darkMedia(true);
    run();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("follows a dark system when system is stored", () => {
    localStorage.setItem("jerome-theme", "system");
    darkMedia(true);
    run();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("does not throw when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(run).not.toThrow();
    vi.restoreAllMocks();
  });
});
