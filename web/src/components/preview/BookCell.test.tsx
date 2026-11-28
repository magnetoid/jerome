import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import BookCell from "./BookCell";
import type { MarkJson, PreviewBlock, StateText } from "@/lib/preview";

const mark = (start: number, end: number, kind: string, href: string | null = null): MarkJson => ({ start, end, kind, href });

function cell(text: StateText, kind = "paragraph", firstAfterHeading = false) {
  const block: PreviewBlock = { id: 1, ordinal: 0, kind, original: text, translation: null, proofread: text };
  return render(<BookCell block={block} state="proofread" lang="de-DE" firstAfterHeading={firstAfterHeading} />);
}

describe("BookCell", () => {
  it("sets each mark around the right letters", () => {
    const text = "one two thr fou fiv six sev";
    const { container } = cell({
      text,
      marks: [
        mark(0, 3, "italic"), mark(4, 7, "bold"), mark(8, 11, "smallcaps"), mark(12, 15, "superscript"),
        mark(16, 19, "subscript"), mark(20, 23, "underline"), mark(24, 27, "link", "https://example.com/"),
      ],
    });
    expect(container.querySelector("em")).toHaveTextContent(/^one$/);
    expect(container.querySelector("strong")).toHaveTextContent(/^two$/);
    expect(container.querySelector(".small-caps")).toHaveTextContent(/^thr$/);
    expect(container.querySelector("sup")).toHaveTextContent(/^fou$/);
    expect(container.querySelector("sub")).toHaveTextContent(/^fiv$/);
    expect(container.querySelector("u")).toHaveTextContent(/^six$/);
    expect(container.querySelector("a[href='https://example.com/']")).toHaveTextContent(/^sev$/);
    expect(container).toHaveTextContent(text);
  });

  it("nests overlapping marks with the link outside", () => {
    const { container } = cell({ text: "bold italic", marks: [mark(0, 11, "italic"), mark(0, 4, "bold"), mark(0, 11, "link", "https://e.org/")] });
    const a = container.querySelector("a")!;
    expect(a.querySelector("strong > em")).toHaveTextContent(/^bold$/);
    expect([...container.querySelectorAll("a")].every((l) => l.getAttribute("href") === "https://e.org/")).toBe(true);
    expect(container.querySelector("strong")).toHaveTextContent(/^bold$/);
  });

  it("shows a picture as a box", () => {
    cell({ text: "a ￼ b", marks: [] });
    expect(screen.getByRole("img", { name: "Picture" })).toHaveTextContent("Picture");
  });

  it("draws each kind of block as its element", () => {
    expect(cell({ text: "Title", marks: [] }, "heading").container.querySelector("h2")).toHaveTextContent("Title");
    expect(cell({ text: "Said", marks: [] }, "quote").container.querySelector("blockquote")).toHaveTextContent("Said");
    expect(cell({ text: "Item", marks: [] }, "list_item").container.querySelector("ul > li")).toHaveTextContent("Item");
    expect(cell({ text: "Cell", marks: [] }, "table_cell").container.querySelector("p")).toHaveTextContent("Cell");
  });

  it("sets the language and the indent", () => {
    const { container } = cell({ text: "After the title.", marks: [] }, "paragraph", true);
    const root = container.firstElementChild!;
    expect(root).toHaveAttribute("lang", "de-DE");
    expect(container.querySelector("p")).toHaveClass("no-indent");
    expect(cell({ text: "Later.", marks: [] }).container.querySelector("p")).not.toHaveClass("no-indent");
  });

  it("counts mark offsets in code points", () => {
    const { container } = cell({ text: "\u{1F600} cat sat", marks: [mark(2, 5, "italic")] });
    expect(container.querySelector("em")).toHaveTextContent(/^cat$/);
  });

  it("puts a note before a footnote's text", () => {
    const block: PreviewBlock = { id: 1, ordinal: 0, kind: "footnote", original: null, translation: null, proofread: { text: "Source note.", marks: [] } };
    const { container } = render(<ol><BookCell block={block} state="proofread" lang="en-GB" firstAfterHeading={false} note="Flagged: not translated" /></ol>);
    expect(container.querySelector("li")).toHaveTextContent("Flagged: not translatedSource note.");
  });
});
