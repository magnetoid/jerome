import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { routeKey, usePageEnter } from "@/lib/pageEnter";

describe("routeKey", () => {
  it.each([
    ["/projects/7/review/3", "/projects/7/review"],
    ["/projects/7/preview/12", "/projects/7/preview"],
    ["/projects/7/review/3/", "/projects/7/review"],
    ["/projects/7/review", "/projects/7/review"],
    ["/projects/7/preview", "/projects/7/preview"],
    ["/projects/7", "/projects/7"],
    ["/projects/new", "/projects/new"],
    ["/", "/"],
    ["/settings", "/settings"],
  ])("%s is %s", (pathname, key) => {
    expect(routeKey(pathname)).toBe(key);
  });

  it("is the same for every chapter of one book, and differs between books", () => {
    expect(routeKey("/projects/7/review/3")).toBe(routeKey("/projects/7/review/4"));
    expect(routeKey("/projects/7/review/3")).not.toBe(routeKey("/projects/8/review/3"));
    expect(routeKey("/projects/7/review/3")).not.toBe(routeKey("/projects/7/preview/3"));
  });
});

describe("usePageEnter", () => {
  afterEach(() => vi.restoreAllMocks());

  function Box({ k, cls }: { k: string; cls?: string }) {
    const ref = usePageEnter(k, cls);
    return <div ref={ref} data-testid="box" className={cls ?? "page-enter"} />;
  }

  // Reading offsetWidth between taking the class off and putting it back is what
  // restarts a CSS animation, so each read is one restart.
  const restarts = () => vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(0);

  it("restarts the animation when the key changes, on the same element", () => {
    const reads = restarts();
    const { rerender } = render(<Box k="/a" />);
    const box = screen.getByTestId("box");
    expect(reads).not.toHaveBeenCalled();
    rerender(<Box k="/b" />);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("box")).toBe(box);
    expect(box).toHaveClass("page-enter");
  });

  it("leaves the element alone while the key stays", () => {
    const reads = restarts();
    const { rerender } = render(<Box k="/a" />);
    rerender(<Box k="/a" />);
    rerender(<Box k="/a" />);
    expect(reads).not.toHaveBeenCalled();
  });

  it("restarts another class when given one", () => {
    const reads = restarts();
    const { rerender } = render(<Box k="1" cls="card-enter" />);
    rerender(<Box k="2" cls="card-enter" />);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("box")).toHaveClass("card-enter");
    expect(screen.getByTestId("box")).not.toHaveClass("page-enter");
  });

  it("stays still while the key changes quickly, as when j is held down", () => {
    const reads = restarts();
    const clock = vi.spyOn(performance, "now");
    clock.mockReturnValue(1000);
    const { rerender } = render(<Box k="1" cls="card-enter" />);
    rerender(<Box k="2" cls="card-enter" />);
    expect(reads).toHaveBeenCalledTimes(1);
    // A change every 33 ms, about the rate of a held key: no restart, the card stays readable.
    for (const [i, k] of ["3", "4", "5", "6"].entries()) {
      clock.mockReturnValue(1033 + i * 33);
      rerender(<Box k={k} cls="card-enter" />);
    }
    expect(reads).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("box")).toHaveClass("card-enter");
    // After a rest the next change plays again.
    clock.mockReturnValue(1132 + 150);
    rerender(<Box k="7" cls="card-enter" />);
    expect(reads).toHaveBeenCalledTimes(2);
  });
});
