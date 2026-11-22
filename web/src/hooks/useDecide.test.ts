import { describe, expect, it } from "vitest";
import { withDecision } from "./useDecide";
import { sug } from "@/components/review/fixtures";

describe("withDecision", () => {
  const a = sug({ id: 1, start: 2, end: 5 });
  const overlapping = sug({ id: 2, start: 4, end: 9 });
  const touching = sug({ id: 3, start: 5, end: 7 });
  const rejected = sug({ id: 4, start: 3, end: 6, status: "rejected" });

  it("puts the decided row in place", () => {
    const row = { ...a, status: "rejected" as const, decided_by: "ann@example.com" };
    expect(withDecision([a, overlapping], row)).toEqual([row, overlapping]);
  });

  it("an accept supersedes the open suggestions it overlaps, as the API does", () => {
    const row = { ...a, status: "accepted" as const };
    const out = withDecision([a, overlapping, touching, rejected], row);
    expect(out.map((s) => [s.id, s.status])).toEqual([
      [1, "accepted"], [2, "superseded"], [3, "open"], [4, "rejected"],
    ]);
  });

  it("an edit supersedes too; a reject or undo leaves the others to the refetch", () => {
    const edited = { ...a, status: "edited" as const, final_text: "tea" };
    expect(withDecision([a, overlapping], edited)[1].status).toBe("superseded");
    const undone = { ...a, status: "open" as const };
    expect(withDecision([a, overlapping], undone)[1].status).toBe("open");
  });
});
