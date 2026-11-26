import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImportPanel from "./ImportPanel";
import { json, mockApi, renderApp } from "@/test-utils";
import { bodyOf, BOOK, callsTo, job } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

const REPORT = {
  pages: 10, text_pages: 4, ocr_pages: 5, engine_pages: { ai: 0, tesseract: 0 },
  image_pages: 1, blank_pages: 0, failed_pages: [], ai_fallbacks: {},
};
const importJob = (stats = {}, over = {}) =>
  job({ type: "import", status: "running", stats: { pages: 10, pages_done: 3, ...stats }, ...over });

function setup(book: object, jobs: unknown[] = [], extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /v1/books/1": () => json(200, { ...BOOK, ...book }),
    "GET /v1/books/1/jobs": () => json(200, jobs),
    ...extra,
  });
}

describe("ImportPanel", () => {
  it("shows the page progress while importing", async () => {
    setup({ import_status: "importing" }, [importJob()]);
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Importing… page 3 of 10")).toBeInTheDocument();
  });

  it("shows the estimate with the max cost prefilled, and confirms with it", async () => {
    const awaiting = importJob(
      { awaiting_estimate: true, pages_done: 4 },
      { estimate: { ocr_pages: 6, cost: "1.5" } },
    );
    const f = setup({ import_status: "importing" }, [awaiting], {
      "POST /v1/books/1/import/confirm": () => json(200, importJob()),
    });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Estimate ready: 6 pages need AI OCR, about $1.50")).toBeInTheDocument();
    const field = screen.getByLabelText(/max cost/i);
    expect(field).toHaveValue(1.5);
    await userEvent.clear(field);
    await userEvent.type(field, "2");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/import/confirm")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/import/confirm")[0])).toEqual({ max_cost: 2 });
  });

  it("disables Confirm for an empty or zero max cost", async () => {
    setup({ import_status: "importing" }, [
      importJob({ awaiting_estimate: true }, { estimate: { ocr_pages: 6, cost: "1.5" } }),
    ]);
    renderApp(<ImportPanel bookId="1" />);
    const field = await screen.findByLabelText(/max cost/i);
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeEnabled();
    await userEvent.clear(field);
    expect(confirm).toBeDisabled();
    await userEvent.type(field, "0");
    expect(confirm).toBeDisabled();
  });

  it("posts use-tesseract from Use Tesseract instead", async () => {
    const f = setup(
      { import_status: "importing" },
      [importJob({ awaiting_estimate: true }, { estimate: { ocr_pages: 6, cost: "1.5" } })],
      { "POST /v1/books/1/import/use-tesseract": () => json(200, importJob()) },
    );
    renderApp(<ImportPanel bookId="1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Use Tesseract instead" }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/import/use-tesseract")).toHaveLength(1));
  });

  it("offers a higher max cost or Tesseract for an import paused at the cost limit", async () => {
    const paused = importJob({ awaiting_estimate: false }, { status: "paused_cost_cap", max_cost: "1.5" });
    const f = setup({ import_status: "importing" }, [paused], {
      "POST /v1/books/1/import/confirm": () => json(200, importJob()),
      "POST /v1/books/1/import/use-tesseract": () => json(200, importJob()),
    });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Paused at the cost limit")).toBeInTheDocument();
    const field = screen.getByLabelText(/max cost/i);
    expect(field).toHaveValue(1.5);
    await userEvent.clear(field);
    await userEvent.type(field, "3");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/import/confirm")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/import/confirm")[0])).toEqual({ max_cost: 3 });
    expect(screen.getByRole("button", { name: "Use Tesseract instead" })).toBeInTheDocument();
  });

  it("says how many pages Tesseract read after the maximum cost was reached", async () => {
    setup({
      import_status: null,
      import_report: { ...REPORT, engine_pages: { ai: 1, tesseract: 2 }, ai_fallbacks: { cost_cap: 2 } },
    });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Tesseract read 2 pages after the maximum cost was reached.")).toBeInTheDocument();
    expect(screen.queryByText(/because the AI failed/)).toBeNull();
  });

  it("shows a failed import with its error and retries by resuming the job", async () => {
    const f = setup({ import_status: "failed", import_error: "the PDF is damaged" }, [
      importJob({}, { status: "failed" }),
    ], { "POST /v1/jobs/9/resume": () => json(200, importJob()) });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Import failed: the PDF is damaged")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    await waitFor(() => expect(retry).toBeEnabled());
    await userEvent.click(retry);
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/resume")).toHaveLength(1));
  });

  it("warns about the pages that could not be read", async () => {
    setup({ import_status: null, import_report: { ...REPORT, failed_pages: [3, 7] } });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("2 pages could not be read: 3, 7")).toBeInTheDocument();
  });

  it("says which engine read the pages and how many the AI failed on", async () => {
    setup({
      import_status: null,
      import_report: {
        ...REPORT, engine_pages: { ai: 3, tesseract: 2 }, ai_fallbacks: { refused: 1, error: 1 },
      },
    });
    renderApp(<ImportPanel bookId="1" />);
    expect(await screen.findByText("Read by AI: 3 pages · Tesseract: 2 pages")).toBeInTheDocument();
    expect(screen.getByText("Tesseract read 2 pages because the AI failed on them.")).toBeInTheDocument();
  });

  it("renders nothing for a book that was not imported or had nothing to report", async () => {
    const f = setup({ import_status: null, import_report: REPORT });
    const { container } = renderApp(<ImportPanel bookId="1" />);
    await waitFor(() => expect(callsTo(f, "GET", "/v1/books/1")).toHaveLength(1));
    expect(container.querySelector("p, button")).toBeNull();
  });
});
