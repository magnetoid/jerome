import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { json } from "@/test-utils";
import { bodyOf, callsTo } from "@/components/project/fixtures";
import {
  aiSug, block, firstPage, renderReview, reviewServer, sug, summaryRow,
} from "@/components/review/fixtures";

let toastError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  toastError = vi.spyOn(toast, "error").mockImplementation(() => "t");
  vi.spyOn(toast, "success").mockImplementation(() => "t");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// Chapter 11: two need you (1, 2), two applied by AI (6, 3), one accepted by a teammate (4).
const s1 = sug({ id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the" });
const s6 = aiSug({
  id: 6, block_id: 100, start: 13, end: 14, original: ".", replacement: "!",
  category: "punctuation", explanation: "Stronger ending.",
});
const s2 = sug({ id: 2, block_id: 101, start: 2, end: 5, original: "dgo", replacement: "dog" });
const s3 = aiSug({
  id: 3, block_id: 101, start: 6, end: 12, original: "barked", replacement: "barks",
  category: "grammar", explanation: "Present tense.",
});
const s4 = sug({
  id: 4, block_id: 102, start: 4, end: 8, original: "said", replacement: "says",
  category: "style", status: "accepted", decided_by: "bob@example.com",
});
const BLOCKS = [
  block(100, 1, "𝔄 teh cat sat.", [s1, s6]),
  block(101, 2, "A dgo barked.", [s2, s3]),
  block(102, 3, "Bob said hi.", [s4]),
];
const SUMMARY = [
  summaryRow(11, 1, "Arrival", 2),
  summaryRow(12, 2, "Harbour", 0),
  summaryRow(13, 3, "Appendix", 4),
];

const card = () => screen.getByRole("region", { name: /focused suggestion/i });
const posts = (f: ReturnType<typeof reviewServer>["fetchMock"], path: string) => callsTo(f, "POST", path);

describe("ReviewPage", () => {
  it("j and a accept the focused suggestion, and the counter drops by one", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    expect(await screen.findByText("2 left")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /focused suggestion/i })).not.toBeInTheDocument();

    await userEvent.keyboard("j");
    expect(within(card()).getByText("teh")).toBeInTheDocument();
    expect(within(card()).getByText("the")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Needs you: “teh”/ })).toHaveAttribute("aria-current", "true");

    await userEvent.keyboard("a");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
    expect(await screen.findByText("1 left")).toBeInTheDocument();
    // Focus moves on to the next suggestion that needs a person.
    await waitFor(() => expect(within(card()).getByText("dgo")).toBeInTheDocument());
  });

  it("the focus card shows the why, the checkers, the house rule and key hints", async () => {
    const special = sug({
      id: 1, agreed: true, disputed: true, alternative: "tea", rule: "house_spelling",
      explanation: "House spelling.", category: "consistency", severity: "medium", source: "consistency",
    });
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [special])], [summaryRow(11, 1, "Arrival", 1)]);
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("j");
    const c = card();
    expect(within(c).getByText("House spelling.")).toBeInTheDocument();
    expect(within(c).getByText("both checkers agree")).toBeInTheDocument();
    expect(within(c).getByText("the checkers disagree")).toBeInTheDocument();
    expect(within(c).getByText(/tea/)).toBeInTheDocument();
    expect(within(c).getByText(/house spelling rule/i)).toBeInTheDocument();
    expect(within(c).getByText("Needs you")).toBeInTheDocument();
    for (const name of ["Accept (a)", "Reject (r)", "Edit (e)", "Undo (u)"]) {
      expect(within(c).getByRole("button", { name })).toBeInTheDocument();
    }
    await userEvent.click(within(c).getByRole("button", { name: "Accept (a)" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
  });

  it("r on an AI-applied change calls /reject", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.click(screen.getByRole("button", { name: "Applied by AI" }));
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Applied by AI")).toBeInTheDocument();
    await userEvent.keyboard("r");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/6/reject")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/6/undo")).toHaveLength(0);
  });

  it("typing a inside the edit textarea does not accept; Escape closes it; Save posts /edit", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.keyboard("j");
    await userEvent.keyboard("e");
    const field = screen.getByRole("textbox", { name: /replacement/i });
    expect(field).toHaveValue("the");
    expect(field).toHaveFocus();
    await userEvent.keyboard("a");
    expect(field).toHaveValue("thea");
    expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(0);

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: /replacement/i })).not.toBeInTheDocument();
    expect(posts(fetchMock, "/v1/suggestions/1/edit")).toHaveLength(0);

    await userEvent.keyboard("e");
    const again = screen.getByRole("textbox", { name: /replacement/i });
    await userEvent.clear(again);
    await userEvent.type(again, "then");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/edit")).toHaveLength(1));
    expect(bodyOf(posts(fetchMock, "/v1/suggestions/1/edit")[0])).toEqual({ replacement: "then" });
    expect(await screen.findByText("1 left")).toBeInTheDocument();
  });

  it("a 409 on accept shows the API detail as a toast and refetches that chapter (Review Focus 2)", async () => {
    const server = reviewServer(BLOCKS, SUMMARY, {
      "POST /v1/suggestions/1/accept": () => {
        // A teammate decided it first.
        server.set(1, { status: "accepted", decided_by: "bob@example.com" });
        return json(409, { detail: "cannot accept a accepted suggestion" });
      },
    });
    renderReview();
    await screen.findByText("2 left");
    expect(callsTo(server.fetchMock, "GET", firstPage(11).slice(4))).toHaveLength(1);
    await userEvent.keyboard("ja");
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("cannot accept a accepted suggestion"));
    await waitFor(() => expect(callsTo(server.fetchMock, "GET", firstPage(11).slice(4))).toHaveLength(2));
    // The stale needs-you mark is gone; the teammate's decision shows instead.
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Needs you: “teh”/ })).not.toBeInTheDocument());
    const mark = document.querySelector('[data-sid="1"]');
    expect(mark).toHaveTextContent(/^the$/);
    expect(mark).toHaveAttribute("title", expect.stringContaining("Accepted by bob@example.com"));
    expect(await screen.findByText("1 left")).toBeInTheDocument();
  });

  it("the Applied by AI view lists only auto-applied changes, in document order", async () => {
    reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    expect(screen.getAllByRole("button", { name: /^Needs you:/ })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Applied by AI" }));
    expect(screen.getByRole("button", { name: "Applied by AI" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryAllByRole("button", { name: /^Needs you:/ })).toHaveLength(0);
    expect(screen.queryAllByRole("button", { name: /^Accepted by/ })).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: /^Applied by AI:/ }).map((b) => b.dataset.sid)).toEqual(["6", "3"]);
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Stronger ending.")).toBeInTheDocument();
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Present tense.")).toBeInTheDocument();
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Present tense.")).toBeInTheDocument();
    // The AI's notes stay in the margin in every view.
    expect(screen.getAllByRole("complementary", { name: /AI notes/i })).toHaveLength(2);
  });

  it("the bulk action confirms, then posts the chapter and category filter", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY, {
      "GET /v1/books/1/suggestions?chapter_id=11&category=typo&status=open&limit=1": () =>
        json(200, [s1], { "X-Total-Count": "2" }),
      "POST /v1/suggestions/bulk": () => json(200, { decided: 2 }),
    });
    renderReview();
    await screen.findByText("2 left");
    expect(screen.queryByRole("button", { name: /accept all/i })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Category"), "typo");
    const bulk = await screen.findByRole("button", { name: "Accept all 2 remaining typo in this chapter" });
    // It sits in the sticky toolbar, so it does not scroll away with the text.
    const toolbar = bulk.closest("[data-review-toolbar]");
    expect(toolbar).not.toBeNull();
    expect(toolbar).toHaveClass("sticky", "top-0");
    await userEvent.click(bulk);
    const dialog = await screen.findByRole("alertdialog");
    expect(posts(fetchMock, "/v1/suggestions/bulk")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: /accept all/i }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/bulk")).toHaveLength(1));
    expect(bodyOf(posts(fetchMock, "/v1/suggestions/bulk")[0])).toEqual({
      book_id: 1, filter: { chapter_id: 11, category: "typo", status: "open" }, action: "accept",
    });
    await waitFor(() => expect(callsTo(fetchMock, "GET", firstPage(11).slice(4))).toHaveLength(2));
  });

  it("deciding the last open item shows the next-chapter prompt", async () => {
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [s1, s6])], SUMMARY.map((c) => (
      c.chapter_id === 11 ? { ...c, open: 1 } : c
    )));
    renderReview();
    await screen.findByText("1 left");
    expect(screen.queryByText(/chapter done/i)).not.toBeInTheDocument();
    await userEvent.keyboard("ja");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
    expect(await screen.findByText("0 left")).toBeInTheDocument();
    expect(await screen.findByText(/chapter done/i)).toBeInTheDocument();
    // Harbour (2) has nothing open, so the prompt skips to Appendix (3).
    expect(screen.getByRole("link", { name: /3\. Appendix/ })).toHaveAttribute("href", "/projects/1/review/13");
  });

  it("when nothing is left in the book, the prompt says so and links back to the project", async () => {
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [s1])], [
      summaryRow(11, 1, "Arrival", 1), summaryRow(12, 2, "Harbour", 0),
    ]);
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("ja");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
    expect(await screen.findByText(/nothing left to review in this book/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to project/i })).toHaveAttribute("href", "/projects/1");
  });
});

describe("u after the focus moved on by itself (F2)", () => {
  // A third AI change, so the focus can be moved on by hand after a decision.
  const s8 = aiSug({
    id: 8, block_id: 102, start: 9, end: 11, original: "hi", replacement: "hello",
    explanation: "Friendlier.",
  });
  const WITH_S8 = [BLOCKS[0], BLOCKS[1], block(102, 3, "Bob said hi.", [s4, s8])];

  async function rejectFirstAiChange(fetchMock: ReturnType<typeof reviewServer>["fetchMock"]) {
    await screen.findByText("2 left");
    await userEvent.click(screen.getByRole("button", { name: "Applied by AI" }));
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Stronger ending.")).toBeInTheDocument();
    await userEvent.keyboard("r");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/6/reject")).toHaveLength(1));
    // The rejected change leaves the view and the focus moves on to the next AI change.
    await waitFor(() => expect(within(card()).getByText("Present tense.")).toBeInTheDocument());
  }

  it("in Applied by AI, u undoes the change just rejected, not the one focus moved on to", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await rejectFirstAiChange(fetchMock);
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/6/undo")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/3/undo")).toHaveLength(0);
    // With nothing left of this session's decisions, u does not reach for the focused change.
    await waitFor(() => expect(within(card()).getByRole("button", { name: "Undo (u)" })).toBeDisabled());
    await userEvent.keyboard("u");
    expect(posts(fetchMock, "/v1/suggestions/3/undo")).toHaveLength(0);
  });

  it("after the person moves on with j, u undoes the decided suggestion in focus", async () => {
    const { fetchMock } = reviewServer(WITH_S8, SUMMARY);
    renderReview();
    await rejectFirstAiChange(fetchMock);
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Friendlier.")).toBeInTheDocument();
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/8/undo")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/6/undo")).toHaveLength(0);
  });

  it("after the person clicks a mark, u undoes that one", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await rejectFirstAiChange(fetchMock);
    await userEvent.click(screen.getByRole("button", { name: /^Applied by AI: “barked”/ }));
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/3/undo")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/6/undo")).toHaveLength(0);
  });
});

describe("focus card placement (S2)", () => {
  it("shares one container with its paragraph and sticks to the bottom of the screen", async () => {
    reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.keyboard("jj");
    expect(within(card()).getByText("dgo")).toBeInTheDocument();
    // The container is the sticky card's limit: it pins to the bottom of the screen while its
    // paragraph is in view, then settles under the paragraph. (Layout itself is not checked
    // in jsdom.)
    const box = card().parentElement!;
    expect(box).toHaveAttribute("data-block-id", "101");
    expect(within(box).getByRole("button", { name: /^Needs you: “dgo”/ })).toBeInTheDocument();
    expect(box.lastElementChild).toBe(card());
    expect(card()).toHaveClass("sticky", "bottom-4", "bg-card");
  });

  it("translation category is filterable", async () => {
    const t = sug({
      id: 9, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the",
      category: "translation", explanation: "Says something different from the source.",
    });
    reviewServer([block(100, 1, "𝔄 teh cat sat.", [t, s6], "paragraph", "Die Katze sass.")], SUMMARY);
    renderReview();
    await screen.findByLabelText("Source paragraph");
    expect(screen.getByLabelText("Source paragraph")).toHaveTextContent("Die Katze sass.");
    expect(screen.getByRole("option", { name: "Translation" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Category"), "translation");
    expect(screen.getAllByRole("button", { name: /^Needs you:/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /^Needs you:/ }));
    expect(within(card()).getByText("Translation")).toBeInTheDocument();
    expect(within(card()).getByLabelText("Original paragraph")).toHaveTextContent("Die Katze sass.");
  });

  it("Ask AI posts, shows the verdict, and Use AI fix edits with the AI's text", async () => {
    const mark = sug({ id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "tea", source: "languagetool" });
    const summary = SUMMARY.map((c) => (c.chapter_id === 11 ? { ...c, open: 1 } : c));
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [mark])], summary, {
      "POST /v1/suggestions/1/ai-check": () =>
        json(200, { ...mark, ai_verdict: "error", ai_replacement: "the", ai_reason: "A typo of the.", ai_model: "m-1" }),
    });
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("j");
    await userEvent.click(within(card()).getByRole("button", { name: "Ask AI" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/ai-check")).toHaveLength(1));
    expect(await within(card()).findByLabelText("AI verdict")).toHaveTextContent("A typo of the.");
    await userEvent.click(within(card()).getByRole("button", { name: "Use AI fix" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/edit")).toHaveLength(1));
    expect(bodyOf(posts(fetchMock, "/v1/suggestions/1/edit")[0])).toEqual({ replacement: "the" });
  });

  it("Remember for all books posts, refreshes, and u undoes each mark it rejected", async () => {
    const mark = sug({
      id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the", source: "languagetool",
      ai_verdict: "not_an_error", ai_reason: "A name.",
    });
    const summary = SUMMARY.map((c) => (c.chapter_id === 11 ? { ...c, open: 1 } : c));
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [mark])], summary, {
      "POST /v1/suggestions/1/remember": () =>
        json(200, {
          word: { id: 1, language: "en", word: "teh", rule: "*", reason: "A name.", book_id: 1, created_by: "a", created_at: "2026-11-25T00:00:00Z" },
          rejected: 2,
          marks: [{ id: 1, chapter_id: 11 }, { id: 2, chapter_id: 11 }],
        }),
      "POST /v1/suggestions/1/undo": () => json(200, mark),
      "POST /v1/suggestions/2/undo": () => json(200, { ...mark, id: 2 }),
    });
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("j");
    await userEvent.click(within(card()).getByRole("button", { name: "Remember for all books" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/remember")).toHaveLength(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Remembered “teh” for all books; rejected 2 marks"));
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/undo")).toHaveLength(1));
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/2/undo")).toHaveLength(1));
  });

  it("another LanguageTool option is used with an edit", async () => {
    const mark = sug({
      id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the", source: "languagetool",
      options: ["the", "tea"],
    });
    const summary = SUMMARY.map((c) => (c.chapter_id === 11 ? { ...c, open: 1 } : c));
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [mark])], summary);
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("j");
    const list = within(card()).getByRole("group", { name: "LanguageTool options" });
    await userEvent.click(within(list).getByRole("button", { name: "2. tea" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/edit")).toHaveLength(1));
    expect(bodyOf(posts(fetchMock, "/v1/suggestions/1/edit")[0])).toEqual({ replacement: "tea" });
  });

  it("LanguageTool's first option accepts the mark", async () => {
    const mark = sug({
      id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the", source: "languagetool",
      options: ["the", "tea"],
    });
    const summary = SUMMARY.map((c) => (c.chapter_id === 11 ? { ...c, open: 1 } : c));
    const { fetchMock } = reviewServer([block(100, 1, "𝔄 teh cat sat.", [mark])], summary);
    renderReview();
    await screen.findByText("1 left");
    await userEvent.keyboard("j");
    const list = within(card()).getByRole("group", { name: "LanguageTool options" });
    await userEvent.click(within(list).getByRole("button", { name: "1. the" }));
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/1/edit")).toHaveLength(0);
  });

  it("Check LT marks posts the chapter and reports what is left", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY, {
      "POST /v1/books/1/chapters/11/ai-check": () => json(200, { checked: 40, remaining: 23, failed: 0, suggestions: [] }),
    });
    renderReview();
    await screen.findByText("2 left");
    const button = screen.getByRole("button", { name: "Check LT marks in this chapter" });
    expect(button.closest("[data-review-toolbar]")).not.toBeNull();
    await userEvent.click(button);
    await waitFor(() => expect(posts(fetchMock, "/v1/books/1/chapters/11/ai-check")).toHaveLength(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Checked 40 of 63; click again for the rest"));
  });
});
