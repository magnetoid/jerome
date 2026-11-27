import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import PreviewPage from "./PreviewPage";
import { Where, json, mockApi, renderApp } from "@/test-utils";
import type { PreviewBlock, PreviewPageData } from "@/lib/preview";

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

const SUMMARY = [
  { chapter_id: 3, ordinal: 1, title: "Arrival", proofread: true, open: 0, auto_applied: 0, decided: 0 },
  { chapter_id: 4, ordinal: 2, title: "Harbour", proofread: false, open: 0, auto_applied: 0, decided: 0 },
];

function block(i: number, over: Partial<PreviewBlock> = {}): PreviewBlock {
  return {
    id: 100 + i,
    ordinal: i,
    kind: "paragraph",
    original: { text: `Hello ${i}.`, marks: [] },
    translation: { text: `Hallo ${i}.`, marks: [], status: "done" },
    proofread: { text: `Servus ${i}.`, marks: [], changes: [] },
    ...over,
  };
}

function page(over: Partial<PreviewPageData> = {}): PreviewPageData {
  return {
    chapter_id: 3, book_id: 7, title: "Arrival", proofread: true, total_blocks: 2, offset: 0,
    work: "translate", variant: "de-DE", source_variant: "en-GB", trim_size: "6x9in",
    blocks: [block(0), block(1, { translation: { text: "", marks: [], status: "pending" }, proofread: { text: "", marks: [], changes: [] } })],
    ...over,
  };
}

function setup(entry: string, first: PreviewPageData = page(), more?: PreviewPageData) {
  const routes: Parameters<typeof mockApi>[0] = {
    "GET /v1/books/7/chapter-summary": json(200, SUMMARY),
    [`GET /v1/chapters/${first.chapter_id}/preview?offset=0&limit=100`]: () => json(200, first),
  };
  if (more) routes[`GET /v1/chapters/${first.chapter_id}/preview?offset=100&limit=100`] = () => json(200, more);
  const f = mockApi(routes);
  renderApp(
    <>
      <Routes>
        <Route path="/projects/:id/preview/:chapterId?" element={<PreviewPage />} />
      </Routes>
      <Where />
    </>,
    entry,
  );
  return f;
}

const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent);

describe("PreviewPage", () => {
  it("shows a translated book's original and proofread side by side", async () => {
    setup("/projects/7/preview/3");
    await screen.findByText("Hello 0.");
    expect(headers()).toEqual(["Original", "Proofread"]);
    const row = screen.getByText("Hello 0.").closest('[role="row"]') as HTMLElement;
    expect(within(row).getAllByRole("cell")).toHaveLength(2);
    expect(within(row).getByText("Servus 0.")).toBeInTheDocument();
  });

  it("shows one state in the single layout, kept in the address", async () => {
    setup("/projects/7/preview/3");
    const user = userEvent.setup();
    await screen.findByText("Hello 0.");
    await user.click(screen.getByRole("radio", { name: "Single" }));
    await user.click(screen.getByRole("radio", { name: "Translation" }));
    expect(headers()).toEqual(["Translation"]);
    expect(screen.getByText("Hallo 0.")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("layout=single&states=translation");
  });

  it("offers no translation for a proofread-only book", async () => {
    setup("/projects/7/preview/3", page({ work: "proofread", source_variant: null, blocks: [block(0, { translation: null })] }));
    await screen.findByText("Hello 0.");
    expect(screen.queryByRole("checkbox", { name: "Translation" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("radio", { name: "Single" }));
    expect(screen.queryByRole("radio", { name: "Translation" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Original" })).toBeInTheDocument();
  });

  it("says a pending paragraph is not translated yet", async () => {
    setup("/projects/7/preview/3?states=original,translation");
    const cell = await screen.findByText("Not translated yet");
    expect(cell.closest('[role="row"]')).toHaveTextContent("Hello 1.");
  });

  it("says when the chapter is not proofread yet", async () => {
    setup("/projects/7/preview/3", page({ proofread: false }));
    await screen.findByText("Hello 0.");
    const header = screen.getAllByRole("columnheader")[1];
    expect(header).toHaveTextContent("Proofread");
    expect(header).toHaveTextContent("Not proofread yet");
  });

  it("follows the address over the last choice kept", async () => {
    window.localStorage.setItem("jerome.preview", JSON.stringify({ view: "text", layout: "single", states: ["translation"], changes: false }));
    setup("/projects/7/preview/3?view=text&layout=side&states=original,translation,proofread");
    await screen.findByText("Hello 0.");
    expect(headers()).toEqual(["Original", "Translation", "Proofread"]);
  });

  it("uses the last choice kept when the address has none", async () => {
    window.localStorage.setItem("jerome.preview", JSON.stringify({ layout: "single", states: ["translation"] }));
    setup("/projects/7/preview/3");
    await screen.findByText("Hallo 0.");
    expect(headers()).toEqual(["Translation"]);
  });

  it("loads the next page on Load more", async () => {
    const blocks = Array.from({ length: 100 }, (_, i) => block(i));
    const f = setup(
      "/projects/7/preview/3",
      page({ total_blocks: 101, blocks }),
      page({ total_blocks: 101, offset: 100, blocks: [block(100)] }),
    );
    await screen.findByText("Hello 0.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("Hello 100.");
    expect(f.mock.calls.some(([p]) => p === "/v1/chapters/3/preview?offset=100&limit=100")).toBe(true);
  });

  it("opens the first chapter when none is in the address", async () => {
    setup("/projects/7/preview?layout=single");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7/preview/3?layout=single"));
  });

  it("keeps the choice when the chapter changes", async () => {
    setup("/projects/7/preview/3?layout=single&states=original");
    await screen.findByText("Hello 0.");
    vi.stubGlobal("fetch", vi.fn(async () => json(200, page({ chapter_id: 4 }))));
    await userEvent.setup().selectOptions(screen.getByLabelText("Chapter"), "4");
    expect(screen.getByTestId("where")).toHaveTextContent("/projects/7/preview/4?layout=single&states=original");
  });

  it("sets the book view in each column's language, with the notes under the text", async () => {
    const note = block(2, {
      kind: "footnote",
      original: { text: "A note.", marks: [] },
      translation: { text: "Eine Notiz.", marks: [], status: "done" },
      proofread: { text: "Eine Anmerkung.", marks: [], changes: [] },
    });
    setup("/projects/7/preview/3?view=book", page({ total_blocks: 3, blocks: [block(0, { kind: "heading" }), block(1), note] }));
    await screen.findByText("Hello 0.");
    const rows = screen.getAllByRole("row");
    expect(rows.some((r) => r.textContent?.includes("A note."))).toBe(false);
    const notes = screen.getAllByRole("list", { name: "Notes" });
    expect(notes).toHaveLength(2);
    expect(notes[0]).toHaveTextContent("A note.");
    expect(notes[1]).toHaveTextContent("Eine Anmerkung.");
    expect(screen.getByText("Hello 0.").closest("[lang]")).toHaveAttribute("lang", "en-GB");
    expect(screen.getByText("Servus 0.").closest("[lang]")).toHaveAttribute("lang", "de-DE");
    expect(screen.getByText("Servus 1.").closest("p")).toHaveClass("no-indent");
  });

  it("says a pending paragraph is not translated yet in the default proofread column", async () => {
    setup("/projects/7/preview/3");
    await screen.findByText("Hello 1.");
    const row = screen.getByText("Hello 1.").closest('[role="row"]') as HTMLElement;
    expect(within(row).getAllByRole("cell")[1]).toHaveTextContent("Not translated yet");
  });

  it("marks a flagged paragraph as not translated, in the source language", async () => {
    const flagged = block(1, {
      translation: { text: "Hello 1.", marks: [], status: "flagged" },
      proofread: { text: "Hello 1.", marks: [], changes: [] },
    });
    setup("/projects/7/preview/3?view=book&states=original,translation,proofread", page({ blocks: [block(0), flagged] }));
    await screen.findByText("Hello 0.");
    const row = screen.getAllByRole("row").find((r) => r.textContent?.includes("Hello 1."))!;
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("Flagged: not translated");
    expect(cells[2]).toHaveTextContent("Flagged: not translated");
    expect(cells[1].querySelector("[lang]")).toHaveAttribute("lang", "en-GB");
  });

  it("says which notes are not translated in the Book view", async () => {
    const pending = block(2, {
      kind: "footnote",
      original: { text: "A note.", marks: [] },
      translation: { text: "", marks: [], status: "pending" },
      proofread: { text: "", marks: [], changes: [] },
    });
    const flagged = block(3, {
      kind: "footnote",
      original: { text: "Another note.", marks: [] },
      translation: { text: "Another note.", marks: [], status: "flagged" },
      proofread: { text: "Another note.", marks: [], changes: [] },
    });
    setup("/projects/7/preview/3?view=book", page({ total_blocks: 3, blocks: [block(0), pending, flagged] }));
    await screen.findByText("Hello 0.");
    const proofreadNotes = within(screen.getAllByRole("list", { name: "Notes" })[1]).getAllByRole("listitem");
    expect(proofreadNotes[0]).toHaveTextContent("Not translated yet");
    expect(proofreadNotes[1]).toHaveTextContent("Flagged: not translated");
    expect(proofreadNotes[1]).toHaveAttribute("lang", "en-GB");
  });

  it("marks the chosen option of each group in the toolbar", async () => {
    setup("/projects/7/preview/3");
    await screen.findByText("Hello 0.");
    const on = (role: string, name: string) => screen.getByRole(role, { name }).getAttribute("data-on");
    expect(on("radio", "Text")).toBe("true");
    expect(on("radio", "Book")).toBeNull();
    expect(on("radio", "Side by side")).toBe("true");
    expect(on("radio", "Single")).toBeNull();
    expect(on("checkbox", "Original")).toBe("true");
    expect(on("checkbox", "Translation")).toBeNull();
    expect(on("checkbox", "Proofread")).toBe("true");

    await userEvent.setup().click(screen.getByRole("radio", { name: "Book" }));
    expect(on("radio", "Book")).toBe("true");
    expect(on("radio", "Text")).toBeNull();
  });
});
