import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Paragraph from "./Paragraph";
import { aiSug, block, sug } from "./fixtures";

const marks = (c: HTMLElement) => [...c.querySelectorAll("[data-sid]")].map((e) => e.getAttribute("data-sid"));

describe("Paragraph", () => {
  it("with 𝔄 before the suggestion, the needs-you mark wraps exactly 'teh' (Review Focus 1)", () => {
    const { container } = render(<Paragraph block={block(100, 1, "𝔄 teh cat", [sug()])} />);
    const mark = screen.getByRole("button", { name: /^Needs you/ });
    expect(mark).toHaveTextContent(/^teh$/);
    expect(mark.className).toMatch(/underline/);
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 teh cat$/);
  });

  it("marks emoji-adjacent text at the right place", () => {
    const s = sug({ start: 3, end: 6, original: "dgo", replacement: "dog" });
    render(<Paragraph block={block(100, 1, "😀😀 dgo!", [s])} />);
    expect(screen.getByRole("button", { name: /^Needs you/ })).toHaveTextContent(/^dgo$/);
  });

  it("an auto-applied change shows the struck original, the new text and its explanation", () => {
    const s = aiSug({ explanation: "Common misspelling." });
    const { container } = render(<Paragraph block={block(100, 1, "𝔄 teh cat", [s])} />);
    const mark = screen.getByRole("button", { name: /^Applied by AI/ });
    expect(within(mark).getByText("teh").tagName).toBe("DEL");
    expect(within(mark).getByText("the").tagName).toBe("INS");
    expect(within(mark).getByText("the").className).toMatch(/text-ai/);
    const note = screen.getByRole("complementary", { name: /AI notes/i });
    expect(note).toHaveTextContent("Common misspelling.");
    expect(note.querySelector(".text-ai")).not.toBeNull();
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 tehthe cat$/);
  });

  it("an agreed suggestion shows 'both checkers agree'", () => {
    render(<Paragraph block={block(100, 1, "𝔄 teh cat", [sug({ agreed: true })])} />);
    expect(screen.getByRole("button", { name: /^Needs you/ })).toHaveAttribute(
      "title", expect.stringContaining("both checkers agree"),
    );
  });

  it("a disputed suggestion carries a badge with the other checker's fix", () => {
    render(<Paragraph block={block(100, 1, "𝔄 teh cat", [sug({ disputed: true, alternative: "tea" })])} />);
    const badge = screen.getByText("the checkers disagree");
    expect(badge).toHaveAttribute("title", expect.stringContaining("tea"));
    // The badge sits beside the mark, so the mark still wraps only the text.
    expect(screen.getByRole("button", { name: /^Needs you/ })).toHaveTextContent(/^teh$/);
  });

  it("never marks superseded suggestions", () => {
    const s = sug({ id: 2, status: "superseded" });
    const { container } = render(<Paragraph block={block(100, 1, "𝔄 teh cat", [s])} />);
    expect(marks(container)).toEqual([]);
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 teh cat$/);
  });

  it("does not mark a later suggestion that overlaps one already placed", () => {
    const first = sug({ id: 1, start: 2, end: 5 });
    const later = sug({ id: 2, start: 4, end: 9, original: "h cat", replacement: "h dog" });
    const { container } = render(<Paragraph block={block(100, 1, "𝔄 teh cat", [first, later])} />);
    expect(marks(container)).toEqual(["1"]);
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 teh cat$/);
  });

  it("places a change that is in the text ahead of an overlapping rejected one", () => {
    const rejected = sug({ id: 1, start: 2, end: 5, status: "rejected", decided_by: "bob@example.com" });
    const accepted = sug({ id: 2, start: 4, end: 9, original: "h cat", replacement: "h dog", status: "accepted", decided_by: "bob@example.com" });
    const { container } = render(<Paragraph block={block(100, 1, "𝔄 teh cat", [rejected, accepted])} />);
    expect(marks(container)).toEqual(["2"]);
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 teh dog$/);
  });

  it("marks an open suggestion ahead of an earlier rejected one it overlaps (R29)", () => {
    const rejected = sug({ id: 1, start: 2, end: 5, status: "rejected", decided_by: "bob@example.com" });
    const open = sug({ id: 2, start: 4, end: 9, original: "h cat", replacement: "h dog" });
    const { container } = render(
      <Paragraph block={block(100, 1, "𝔄 teh cat", [rejected, open])} isActive={(s) => s.status === "open"} />,
    );
    expect(marks(container)).toEqual(["2"]);
    const mark = screen.getByRole("button", { name: /^Needs you/ });
    expect(mark).toHaveTextContent(/^h cat$/);
    expect(mark.className).toMatch(/underline/);
    expect(container.querySelector("p")).toHaveTextContent(/^𝔄 teh cat$/);
  });

  it("team decisions are subtle and say who decided on hover", () => {
    const text = "teh cat sat on teh mat";
    const accepted = sug({ id: 1, start: 0, end: 3, status: "accepted", decided_by: "bob@example.com" });
    const edited = sug({ id: 2, start: 4, end: 7, original: "cat", replacement: "cats", status: "edited", final_text: "dog", decided_by: "cy@example.com" });
    const rejected = sug({ id: 3, start: 15, end: 18, status: "rejected", decided_by: "dee@example.com" });
    const { container } = render(<Paragraph block={block(100, 1, text, [accepted, edited, rejected])} />);
    expect(container.querySelector("p")).toHaveTextContent(/^the dog sat on teh mat$/);
    expect(screen.getByRole("button", { name: /^Accepted by bob@example.com/ })).toHaveAttribute(
      "title", expect.stringContaining("Accepted by bob@example.com"),
    );
    expect(screen.getByRole("button", { name: /^Edited by cy@example.com/ })).toHaveTextContent(/^dog$/);
    const rej = screen.getByRole("button", { name: /^Rejected by dee@example.com/ });
    expect(rej).toHaveTextContent(/^teh$/);
    expect(rej).toHaveAttribute("title", expect.stringContaining("Rejected by dee@example.com"));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("marks outside the current view are not focusable buttons", () => {
    const open = sug({ id: 1 });
    const ai = aiSug({ id: 2, start: 6, end: 9, original: "cat", replacement: "cot" });
    const { container } = render(
      <Paragraph block={block(100, 1, "𝔄 teh cat", [open, ai])} isActive={(s) => s.auto_applied} />,
    );
    expect(marks(container)).toEqual(["1", "2"]);
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("data-sid"))).toEqual(["2"]);
  });

  it("flags the focused mark with aria-current and reports clicks", async () => {
    const onSelect = vi.fn();
    const a = sug({ id: 1 });
    const b = sug({ id: 2, start: 6, end: 9, original: "cat", replacement: "cot" });
    render(<Paragraph block={block(100, 1, "𝔄 teh cat", [a, b])} focusedId={2} onSelect={onSelect} />);
    const [first, second] = screen.getAllByRole("button");
    expect(first).not.toHaveAttribute("aria-current");
    expect(second).toHaveAttribute("aria-current", "true");
    await userEvent.click(first);
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("shows the source paragraph next to a translated block", async () => {
    render(<Paragraph block={block(100, 1, "The cat sat.", [], "paragraph", "Die Katze sass.")} />);
    const src = screen.getByLabelText("Source paragraph");
    expect(src).toHaveTextContent("Die Katze sass.");
    // Below 1024 px the original sits behind a toggle.
    const toggle = screen.getByRole("button", { name: "Show original" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(src.className).toMatch(/\bhidden\b/);
    expect(src.className).toMatch(/lg:block/);
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide original" })).toHaveAttribute("aria-expanded", "true");
    expect(src.className).not.toMatch(/\bhidden\b/);
  });

  it("narrow screens put the source column under the translated text; lg puts it on the left", () => {
    const { container } = render(<Paragraph block={block(100, 1, "The cat sat.", [], "paragraph", "Die Katze sass.")} />);
    const col = container.querySelector("[data-source-column]") as HTMLElement;
    const text = container.querySelector("p") as HTMLElement;
    // Text precedes the source column in the DOM, so a single column reads text, then toggle.
    expect(text.compareDocumentPosition(col) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(col.className).toMatch(/\border-last\b/);
    expect(col.className).toMatch(/lg:order-first/);
    expect(col.parentElement!.className).toMatch(/lg:grid-cols-2/);
  });

  it("no source column for a proofread-only book", () => {
    render(<Paragraph block={block(100, 1, "The cat sat.")} />);
    expect(screen.queryByLabelText("Source paragraph")).toBeNull();
    expect(screen.queryByRole("button", { name: /original/i })).toBeNull();
  });
});
