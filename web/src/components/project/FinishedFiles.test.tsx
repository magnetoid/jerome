import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FinishedFiles from "./FinishedFiles";
import { json, mockApi, renderApp } from "@/test-utils";
import { PUBLICATION, bodyOf, callsTo, job } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

const publishJob = (format: string) =>
  job({ id: 30, type: "publish", status: "queued", passes: [format], max_cost: "0", cost_actual: "0",
    tasks: [{ id: 3, kind: "publish", chapter_id: null, status: "queued", chunks_done: 0, chunks_total: null, attempts: 0, error: null }] });

describe("FinishedFiles", () => {
  it("Make EPUB and Make PDF POST /v1/books/{id}/publish", async () => {
    const f = mockApi({
      "GET /v1/books/1/publication": () => json(200, PUBLICATION),
      "POST /v1/books/1/publish": (init) => json(201, publishJob(JSON.parse(init.body ?? "{}").format)),
    });
    renderApp(<FinishedFiles bookId="1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Make EPUB" }));
    await userEvent.click(screen.getByRole("button", { name: "Make PDF" }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/publish")).toHaveLength(2));
    const sent = callsTo(f, "POST", "/v1/books/1/publish").map((c) => bodyOf(c).format);
    expect(sent).toEqual(["epub-book", "pdf-book"]);
  });

  it('is disabled with "Set the author first" when there is no author', async () => {
    mockApi({ "GET /v1/books/1/publication": () => json(200, { ...PUBLICATION, authors: [] }) });
    renderApp(<FinishedFiles bookId="1" />);
    expect(await screen.findByText(/Set the author first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make EPUB" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Make PDF" })).toBeDisabled();
  });

  it("is disabled while paragraphs are untranslated", async () => {
    mockApi({ "GET /v1/books/1/publication": () => json(200, PUBLICATION) });
    renderApp(<FinishedFiles bookId="1" untranslated={3} />);
    expect(await screen.findByText(/waits for 3 untranslated paragraphs/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make EPUB" })).toBeDisabled();
  });

  it("shows the server's refusal", async () => {
    mockApi({
      "GET /v1/books/1/publication": () => json(200, PUBLICATION),
      "POST /v1/books/1/publish": () => json(409, { detail: "book 1 already has an active job" }),
    });
    renderApp(<FinishedFiles bookId="1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Make PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has an active job");
  });
});
