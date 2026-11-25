import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FocusCard from "./FocusCard";
import { aiSug, sug } from "./fixtures";
import type { Suggestion } from "@/lib/review";
import { formatDateTime } from "@/lib/format";

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
      onAskAi={noop} onUseAiFix={noop} onUseOption={noop} onRemember={noop}
    />,
  );
}

function cardFor(
  s: Suggestion,
  over: {
    asking?: boolean;
    onAskAi?: () => void;
    onUseAiFix?: () => void;
    onUseOption?: (text: string) => void;
    onRemember?: () => void;
    reverts?: boolean;
  } = {},
) {
  const noop = vi.fn();
  return render(
    <FocusCard
      s={s} hiddenBy={null} editing={false} busy={false} canUndo={false} asking={over.asking ?? false}
      reverts={over.reverts}
      onAccept={noop} onReject={noop} onEdit={noop} onUndo={noop} onSave={noop} onCancelEdit={noop}
      onAskAi={over.onAskAi ?? noop} onUseAiFix={over.onUseAiFix ?? noop} onUseOption={over.onUseOption ?? noop}
      onRemember={over.onRemember ?? noop}
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
      sug(), lt({ agreed: true }), lt({ status: "rejected" }), lt({ ai_verdict: "error", ai_replacement: "the", ai_reason: "Right." }),
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

  it("an error with another fix shows it and makes Use AI fix the main button", async () => {
    const onUseAiFix = vi.fn();
    cardFor(
      lt({ options: ["the", "tea"], ai_verdict: "error", ai_replacement: "tea", ai_reason: "They drink it." }),
      { onUseAiFix },
    );
    const verdict = screen.getByLabelText("AI verdict");
    expect(verdict).toHaveTextContent("AI: definitely an error · fix “tea” (LanguageTool option 2)");
    expect(verdict).toHaveTextContent("They drink it.");
    expect(within(verdict).getByText(/definitely an error/)).toHaveClass("text-red-700");
    expect(screen.getByRole("button", { name: "Use AI fix" })).not.toHaveClass("border");
    expect(screen.getByRole("button", { name: /^Accept/ })).toHaveClass("border");
    await userEvent.click(screen.getByRole("button", { name: "Use AI fix" }));
    expect(onUseAiFix).toHaveBeenCalledOnce();
  });

  it("an error with LanguageTool's first fix keeps Accept the main button", () => {
    cardFor(lt({ ai_verdict: "error", ai_replacement: "the", ai_reason: "A typo." }));
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("AI: definitely an error · fix “the” (LanguageTool option 1)");
    expect(screen.queryByRole("button", { name: "Use AI fix" })).toBeNull();
    expect(screen.getByRole("button", { name: /^Accept/ })).not.toHaveClass("border");
  });

  it("an AI fix of its own is named as the AI's", () => {
    cardFor(lt({ ai_verdict: "error", ai_replacement: "The", ai_reason: "Starts a sentence." }));
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("AI: definitely an error · fix “The” (the AI's own)");
  });

  it("maybe an error leaves no main button", () => {
    cardFor(lt({ options: ["the", "tea"], ai_verdict: "maybe_error", ai_replacement: "tea", ai_reason: "Depends." }));
    const verdict = screen.getByLabelText("AI verdict");
    expect(verdict).toHaveTextContent("AI: maybe an error · fix “tea” (LanguageTool option 2)");
    expect(within(verdict).getByText(/maybe an error/)).toHaveClass("text-amber-800");
    for (const name of [/^Accept/, /^Reject/, /^Use AI fix/]) {
      expect(screen.getByRole("button", { name })).toHaveClass("border");
    }
  });

  it("not an error makes Reject the main button", () => {
    cardFor(lt({ ai_verdict: "not_an_error", ai_reason: "A name." }));
    const verdict = screen.getByLabelText("AI verdict");
    expect(verdict).toHaveTextContent("AI: definitely not an error · A name.");
    expect(within(verdict).getByText(/definitely not an error/)).toHaveClass("text-green-700");
    expect(screen.getByRole("button", { name: /^Reject/ })).not.toHaveClass("border");
    expect(screen.getByRole("button", { name: /^Accept/ })).toHaveClass("border");
  });

  it("no Use AI fix once the mark is decided", () => {
    cardFor(lt({ status: "rejected", ai_verdict: "error", ai_replacement: "x", ai_reason: "r" }));
    expect(screen.queryByRole("button", { name: "Use AI fix" })).toBeNull();
  });
});

describe("FocusCard LanguageTool options", () => {
  it("lists the options, marks the AI's pick, and uses the one clicked", async () => {
    const onUseOption = vi.fn();
    cardFor(lt({ options: ["the", "tea", "ten"], ai_verdict: "maybe_error", ai_replacement: "tea", ai_reason: "r" }), {
      onUseOption,
    });
    const list = screen.getByRole("group", { name: "LanguageTool options" });
    const buttons = within(list).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["1. the", "2. tea · AI's pick", "3. ten"]);
    await userEvent.click(buttons[2]);
    expect(onUseOption).toHaveBeenCalledWith("ten");
  });

  it("names a deletion option and fix as delete", () => {
    cardFor(lt({ replacement: "the", options: ["the", ""], ai_verdict: "error", ai_replacement: "", ai_reason: "Doubled." }));
    const list = screen.getByRole("group", { name: "LanguageTool options" });
    expect(within(list).getAllByRole("button").map((b) => b.textContent)).toEqual(["1. the", "2. delete · AI's pick"]);
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("fix: delete (LanguageTool option 2)");
  });

  it("a maybe without a fix names none", () => {
    cardFor(lt({ ai_verdict: "maybe_error", ai_replacement: null, ai_reason: "Could be dialect." }));
    expect(screen.getByLabelText("AI verdict")).toHaveTextContent("AI: maybe an error · Could be dialect.");
    expect(screen.queryByRole("button", { name: "Use AI fix" })).toBeNull();
  });

  it("shows no list for one option, an old mark, or a decided mark", () => {
    for (const s of [lt({ options: ["the"] }), lt({ options: null }), lt({ options: ["the", "tea"], status: "accepted" })]) {
      const card = cardFor(s);
      expect(screen.queryByRole("group", { name: "LanguageTool options" })).toBeNull();
      card.unmount();
    }
  });
});

describe("FocusCard Remember for all books", () => {
  it("is offered on a LanguageTool mark the AI rated fine, and calls onRemember", async () => {
    const onRemember = vi.fn();
    cardFor(lt({ ai_verdict: "not_an_error", ai_reason: "A name." }), { onRemember });
    await userEvent.click(screen.getByRole("button", { name: "Remember for all books" }));
    expect(onRemember).toHaveBeenCalledOnce();
  });

  it("is offered for a rule mark that has its rule", () => {
    cardFor(lt({ category: "grammar", lt_rule: "SENT_START", ai_verdict: "not_an_error", ai_reason: "r" }));
    expect(screen.getByRole("button", { name: "Remember for all books" })).toBeInTheDocument();
  });

  it("is not offered otherwise", () => {
    for (const s of [
      lt(),
      lt({ ai_verdict: "maybe_error", ai_reason: "r" }),
      lt({ ai_verdict: "not_an_error", ai_reason: "r", status: "rejected" }),
      sug({ source: "model", ai_verdict: "not_an_error", ai_reason: "r" }),
      lt({ category: "grammar", lt_rule: null, ai_verdict: "not_an_error", ai_reason: "r" }),
    ]) {
      const card = cardFor(s);
      expect(screen.queryByRole("button", { name: "Remember for all books" })).toBeNull();
      card.unmount();
    }
  });
});

describe("FocusCard decided mark", () => {
  it("says who decided it and when, and offers Revert", () => {
    const at = "2026-11-25T10:42:00Z";
    cardFor(sug({ status: "accepted", decided_by: "bob@example.com", decided_at: at }), { reverts: true });
    expect(screen.getByText(`Accepted by bob@example.com · ${formatDateTime(at)}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revert (u)" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo (u)" })).not.toBeInTheDocument();
  });

  it("an open mark offers Undo", () => {
    cardFor(sug());
    expect(screen.getByRole("button", { name: "Undo (u)" })).toBeInTheDocument();
    expect(screen.getByText("Needs you")).toBeInTheDocument();
  });
});
