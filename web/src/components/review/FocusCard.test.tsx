import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import FocusCard from "./FocusCard";
import { aiSug, sug } from "./fixtures";
import type { Suggestion } from "@/lib/review";

function renderCard(hiddenBy: Suggestion | null, source?: string | null) {
  const noop = vi.fn();
  return render(
    <FocusCard
      s={sug({ id: 2, start: 4, end: 9, original: "h cat", replacement: "h dog" })}
      hiddenBy={hiddenBy}
      source={source}
      editing={false}
      busy={false}
      canUndo={false}
      onAccept={noop} onReject={noop} onEdit={noop} onUndo={noop} onSave={noop} onCancelEdit={noop}
    />,
  );
}

describe("FocusCard 'not marked' note (R29)", () => {
  it("says nothing when the suggestion is marked in the text", () => {
    renderCard(null);
    expect(screen.queryByText(/not marked in the text/i)).not.toBeInTheDocument();
  });

  it.each([
    ["a person's accept", sug({ id: 1, status: "accepted", decided_by: "bob@example.com" })],
    ["a person's edit", sug({ id: 1, status: "edited", final_text: "tea", decided_by: "bob@example.com" })],
    ["an AI-applied change", aiSug({ id: 1 })],
  ])("names a change already applied when hidden by %s", (_, by) => {
    renderCard(by);
    expect(screen.getByText("Not marked in the text: it overlaps a change already applied there.")).toBeInTheDocument();
  });

  it("names an open suggestion", () => {
    renderCard(sug({ id: 1 }));
    expect(screen.getByText("Not marked in the text: it overlaps another open suggestion marked there.")).toBeInTheDocument();
  });

  it("names a rejected suggestion", () => {
    renderCard(sug({ id: 1, status: "rejected", decided_by: "bob@example.com" }));
    expect(screen.getByText("Not marked in the text: it overlaps a rejected suggestion shown there.")).toBeInTheDocument();
  });
});

describe("FocusCard source paragraph", () => {
  it("shows the source paragraph of a translated block", () => {
    renderCard(null, "Die Katze sass.");
    expect(screen.getByLabelText("Original paragraph")).toHaveTextContent("Die Katze sass.");
  });

  it("shows no original without a source", () => {
    renderCard(null, null);
    expect(screen.queryByLabelText("Original paragraph")).toBeNull();
  });
});
