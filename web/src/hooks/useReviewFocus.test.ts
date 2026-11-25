import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useReviewFocus } from "./useReviewFocus";
import { sug } from "@/components/review/fixtures";
import type { Item } from "@/lib/review";

const item = (over: Parameters<typeof sug>[0]): Item => ({ s: sug(over), ordinal: 1 });

describe("useReviewFocus picked marks", () => {
  it("a picked mark decided again elsewhere no longer holds the focus", () => {
    const later = item({ id: 2, start: 9, end: 12 });
    const first = item({ id: 1, status: "accepted", decided_at: "2026-11-25T10:00:00Z" });
    const { result, rerender } = renderHook(({ items, pickable }) => useReviewFocus(items, pickable), {
      initialProps: { items: [later], pickable: [first] },
    });
    act(() => result.current.pick(first));
    expect(result.current.current?.s.id).toBe(1);
    // A teammate reverts it: it is open again, in the view.
    const reopened = item({ id: 1, decided_at: "2026-11-25T10:05:00Z" });
    rerender({ items: [reopened, later], pickable: [] });
    expect(result.current.current?.s.id).toBe(1);
    // Then it is decided again: a new decision, so the focus moves on.
    const again = item({ id: 1, status: "accepted", decided_at: "2026-11-25T10:06:00Z" });
    rerender({ items: [later], pickable: [again] });
    expect(result.current.current?.s.id).toBe(2);
  });

  it("a picked mark stays focused across a refetch while its decision stands", () => {
    const first = item({ id: 1, status: "accepted", decided_at: "2026-11-25T10:00:00Z" });
    const { result, rerender } = renderHook(({ items, pickable }) => useReviewFocus(items, pickable), {
      initialProps: { items: [item({ id: 2, start: 9, end: 12 })], pickable: [first] },
    });
    act(() => result.current.pick(first));
    rerender({ items: [item({ id: 2, start: 9, end: 12 })], pickable: [{ ...first, s: { ...first.s } }] });
    expect(result.current.current?.s.id).toBe(1);
  });
});
