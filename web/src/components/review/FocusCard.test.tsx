import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      asking={false}
      onAccept={noop} onReject={noop} onEdit={noop} onUndo={noop} onSave={noop} onCancelEdit={noop}
      onAskAi={noop} onUseAiFix={noop}
    />,
  );
}

function cardFor(s: Suggestion, over: { asking?: boolean; onAskAi?: () => void; onUseAiFix?: () => void } = {}) {
  const noop = vi.fn();
  return render(
    <FocusCard
      s={s} hiddenBy={null} editing={false} busy={false} canUndo={false} asking={over.asking ?? false}
      onAccept={noop} onReject={noop} onEdit={noop} onUndo={noop} onSave={noop} onCancelEdit={noop}
      onAskAi={over.onAskAi ?? noop} onUseAiFix={over.onUseAiFix ?? noop}
    />,
  );
}

const lt = (over: Partial<Suggestion> = {}) => sug({ source: "languagetool", ...over });

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

describe("FocusCard AI check", () => {
  it("shows Ask AI only for an unchecked LanguageTool mark", async () => {
    const onAskAi = vi.fn();
    const { unmount } = cardFor(lt(), { onAskAi });
    await userEvent.click(screen.getByRole("button", { name: "Ask AI" }));
    expect(onAskAi).toHaveBeenCalledOnce();
    unmount();
    for (const s of [
      sug(), lt({ agreed: true }), lt({ status: "rejected" }), lt({ ai_verdict: "correct", ai_reason: "Right." }),
    ]) {
      const card = cardFor(s);
      expect(screen.queryByRole("button", { name: "Ask AI" })).toBeNull();
      card.unmount();
    }
  });

  it("says it is asking while the call runs", () => {
    cardFor(lt(), { asking: true });
    expect(screen.getByRole("button", { name: "Asking AI…" })).toBeDisabled();
  });

  it("shows the verdict and reason", async () => {
    const onUseAiFix = vi.fn();
    cardFor(lt({ ai_verdict: "better", ai_replacement: "the cat", ai_reason: "Keeps the article." }), { onUseAiFix });
    const verdict = screen.getByLabelText("AI verdict");
    expect(verdict).toHaveTextContent("AI suggests a better fix: “the cat”");
    expect(verdict).toHaveTextContent("Keeps the article.");
    await userEvent.click(screen.getByRole("button", { name: "Use AI fix" }));
    expect(onUseAiFix).toHaveBeenCalledOnce();
  });

  it("LanguageTool right keeps Accept the main button", () => {
    cardFor(lt({ ai_verdict: "correct", ai_reason: "A typo." }));
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("AI: LanguageTool is right");
    expect(screen.queryByRole("button", { name: "Use AI fix" })).toBeNull();
    expect(screen.getByRole("button", { name: /^Accept/ })).not.toHaveClass("border");
  });

  it("not an error makes Reject the main button", () => {
    cardFor(lt({ ai_verdict: "not_an_error", ai_reason: "A name." }));
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("AI: not an error");
    expect(screen.getByRole("button", { name: /^Reject/ })).not.toHaveClass("border");
    expect(screen.getByRole("button", { name: /^Accept/ })).toHaveClass("border");
  });

  it("no Use AI fix once the mark is decided", () => {
    cardFor(lt({ status: "rejected", ai_verdict: "better", ai_replacement: "x", ai_reason: "r" }));
    expect(screen.queryByRole("button", { name: "Use AI fix" })).toBeNull();
  });
});
