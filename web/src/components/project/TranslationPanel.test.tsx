import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import TranslationPanel from "./TranslationPanel";
import type { Project } from "@/hooks/useProjects";
import { json, renderApp } from "@/test-utils";
import { callsTo, pageApi, TRANSLATION, translateProject } from "./fixtures";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.success).mockClear();
});
afterEach(() => vi.unstubAllGlobals());

const translated = () =>
  translateProject({ stage: "translated", translation: TRANSLATION.counts }) as unknown as Project;

const block = (over: Record<string, unknown> = {}) => ({
  id: 501, book_id: 1, chapter_id: 11, ordinal: 3, kind: "paragraph",
  text: "Das Schiff kam im Morgengrauen.", marks: [], translation_status: "done", ...over,
});

function setup(over: Parameters<typeof pageApi>[0] = {}, format = "docx") {
  const f = pageApi({ "GET /v1/books/1/translation": () => json(200, TRANSLATION), ...over });
  const view = renderApp(<TranslationPanel project={translated()} format={format} />);
  return { f, ...view };
}

const flaggedItem = (text: string) => screen.getByText(text).closest("li")!;

describe("TranslationPanel, translated", () => {
  it("translated project lists flagged blocks with Translate again and Keep original for DOCX", async () => {
    setup();
    expect(await screen.findByText(/37 paragraphs translated/)).toHaveTextContent(
      "37 paragraphs translated · 2 flagged · 1 kept as the original",
    );
    const list = screen.getByRole("list", { name: "Flagged paragraphs" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    const first = flaggedItem("The ship came in at dawn.");
    // The chapter's name, once the chapters have loaded.
    await waitFor(() => expect(first).toHaveTextContent("1. Arrival"));
    expect(within(first).getByRole("button", { name: "Translate again" })).toBeEnabled();
    expect(within(first).getByRole("button", { name: "Keep original" })).toBeEnabled();
    expect(flaggedItem("Gulls circled the harbour wall.")).toHaveTextContent("2. Harbour");
  });

  it("offers no Keep original on an EPUB book", async () => {
    setup({}, "epub");
    await screen.findByText("The ship came in at dawn.");
    expect(screen.getAllByRole("button", { name: "Translate again" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Keep original" })).not.toBeInTheDocument();
  });

  it("Translate again posts for that block and refreshes the translation and the projects", async () => {
    const { f, client } = setup({ "POST /v1/blocks/501/translate": () => json(200, block()) });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await screen.findByText("The ship came in at dawn.");
    await userEvent.click(
      within(flaggedItem("The ship came in at dawn.")).getByRole("button", { name: "Translate again" }),
    );
    await waitFor(() => expect(callsTo(f, "POST", "/v1/blocks/501/translate")).toHaveLength(1));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["translation", "1"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects"] });
    expect(toast.success).toHaveBeenCalledWith("Paragraph translated");
  });

  it("says so when Translate again leaves the block flagged", async () => {
    setup({ "POST /v1/blocks/501/translate": () => json(200, block({ translation_status: "flagged" })) });
    await screen.findByText("The ship came in at dawn.");
    await userEvent.click(
      within(flaggedItem("The ship came in at dawn.")).getByRole("button", { name: "Translate again" }),
    );
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      "The paragraph still could not be translated. Try again, or keep the original.",
    ));
  });

  it("Keep original posts keep-source; an API refusal shows as an error toast", async () => {
    const { f } = setup({
      "POST /v1/blocks/502/keep-source": () => json(409, { detail: "block 502 is done, not flagged" }),
    });
    await screen.findByText("Gulls circled the harbour wall.");
    await userEvent.click(
      within(flaggedItem("Gulls circled the harbour wall.")).getByRole("button", { name: "Keep original" }),
    );
    await waitFor(() => expect(callsTo(f, "POST", "/v1/blocks/502/keep-source")).toHaveLength(1));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("block 502 is done, not flagged"));
  });

  it("proofreading start shows the flagged-blocks note", async () => {
    setup();
    expect(await screen.findByRole("button", { name: /start proofreading/i })).toBeInTheDocument();
    const note = await screen.findByText(/Proofreading skips the 2 flagged paragraphs/);
    expect(note).toHaveTextContent(
      "Proofreading skips the 2 flagged paragraphs, and export waits until they are resolved.",
    );
    // The spec lets proofreading start while blocks are flagged.
    await waitFor(() => expect(screen.getByRole("button", { name: /start proofreading/i })).toBeEnabled());
  });

  it("no flagged list and no note when nothing is flagged", async () => {
    setup({ "GET /v1/books/1/translation": () => json(200, { counts: { done: 40 }, flagged: [] }) });
    expect(await screen.findByText(/40 paragraphs translated/)).toBeInTheDocument();
    await screen.findByRole("button", { name: /start proofreading/i });
    expect(screen.queryByRole("list", { name: "Flagged paragraphs" })).not.toBeInTheDocument();
    expect(screen.queryByText(/flagged paragraph/)).not.toBeInTheDocument();
  });

  it("still offers the proofreading start when the translation cannot be read", async () => {
    setup({ "GET /v1/books/1/translation": () => json(500, { detail: "database unavailable" }) });
    expect(await screen.findByRole("alert")).toHaveTextContent("database unavailable");
    expect(await screen.findByRole("button", { name: /start proofreading/i })).toBeInTheDocument();
  });
});
