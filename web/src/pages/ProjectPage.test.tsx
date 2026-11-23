import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json } from "@/test-utils";
import {
  activeJob, bodyOf, BOOK, callsTo, estimate, job, pageApi, project, renderPage, SOURCE_BOOK, STYLE,
  TRANSLATED_BOOK, translateProject,
} from "@/components/project/fixtures";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const jobCalls = (f: ReturnType<typeof pageApi>) => callsTo(f, "GET", "/v1/jobs/9").length;
/** The rows of the Jobs table, without its header. */
const jobRows = () =>
  within(screen.getByRole("heading", { name: "Jobs" }).closest("section")!).getAllByRole("row").slice(1);

describe("ProjectPage panels by stage", () => {
  it("set_up shows the start panel and no re-run button", async () => {
    pageApi();
    renderPage();
    expect(await screen.findByRole("button", { name: /start proofreading/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run proofreading again/i })).not.toBeInTheDocument();
    expect(screen.getByText("Set up")).toBeInTheDocument();
  });

  it("shows not-found when the project is missing", async () => {
    pageApi({ "GET /v1/projects": () => json(200, []) });
    renderPage();
    expect(await screen.findByText(/project not found/i)).toBeInTheDocument();
  });

  it("proofreading shows per-chapter progress, cost and no re-run button", async () => {
    pageApi(
      { "GET /v1/jobs/9": () => json(200, job()) },
      project({ stage: "proofreading", active_job: activeJob() }),
    );
    renderPage();
    expect(await screen.findByText("5/5")).toBeInTheDocument();
    expect(screen.getByText("2/6")).toBeInTheDocument();
    expect(screen.getByText(/\$0\.40 of \$4\.00/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^review$/i })).toHaveAttribute("href", "/projects/1/review");
    expect(screen.queryByRole("button", { name: /run proofreading again/i })).not.toBeInTheDocument();
  });

  it("shows the consistency check's state in words, since it has no chunk count (M4)", async () => {
    const tasks = [
      ...(job().tasks as unknown[]),
      { id: 3, kind: "consistency", chapter_id: null, status: "queued", chunks_done: 0, chunks_total: null, attempts: 0, error: null },
    ];
    pageApi(
      { "GET /v1/jobs/9": () => json(200, job({ tasks })) },
      project({ stage: "proofreading", active_job: activeJob() }),
    );
    renderPage();
    const item = (await screen.findByText("Consistency check")).closest("li")!;
    expect(item).toHaveTextContent(/Consistency check\s*Waiting/);
    expect(item).not.toHaveTextContent("/");
    expect(within(item).getByRole("progressbar")).toBeInTheDocument();
  });

  it("to_review shows the summary, per-chapter counts, Review and a re-run button", async () => {
    const f = pageApi({}, project({
      stage: "to_review", open_count: 38, auto_applied_count: 214, decided_count: 12,
    }));
    renderPage();
    expect(await screen.findByText("AI applied 214 · 38 need you · 12 decided by the team")).toBeInTheDocument();
    const row = (await screen.findByText("2. Harbour")).closest("tr")!;
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual(["2. Harbour", "18", "114", "8"]);
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/projects/1/review?chapter=12");
    expect(screen.getByRole("link", { name: /^Review$/ })).toHaveAttribute("href", "/projects/1/review");
    await userEvent.click(screen.getByRole("button", { name: /run proofreading again/i }));
    expect(await screen.findByRole("button", { name: /start proofreading/i })).toBeInTheDocument();
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/estimate").length).toBeGreaterThan(0));
  });

  it("done shows the summary too", async () => {
    pageApi({}, project({ stage: "done", auto_applied_count: 5 }));
    renderPage();
    expect(await screen.findByText(/AI applied 5 · 0 need you/)).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
  });
});

describe("needs attention", () => {
  const capped = () => project({
    stage: "needs_attention", active_job: activeJob({ status: "paused_cost_cap" }),
  });

  it("explains a cost-limit pause and resumes with a higher limit", async () => {
    const f = pageApi({
      "GET /v1/jobs/9": () => json(200, job({ status: "paused_cost_cap" })),
      "POST /v1/jobs/9/resume": () => json(200, job()),
    }, capped());
    renderPage();
    expect(await screen.findByText(/paused at its cost limit/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/new cost limit/i), "12");
    await userEvent.click(screen.getByRole("button", { name: /resume/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/resume")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/jobs/9/resume")[0])).toEqual({ max_cost: 12 });
  });

  it("resumes without a body limit when none is typed", async () => {
    const f = pageApi({
      "GET /v1/jobs/9": () => json(200, job({ status: "paused_cost_cap" })),
      "POST /v1/jobs/9/resume": () => json(200, job()),
    }, capped());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /resume/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/resume")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/jobs/9/resume")[0])).toEqual({});
  });

  it("resumes a failed job (no active_job) and shows what failed", async () => {
    const failed = job({
      status: "failed",
      tasks: [{ id: 2, kind: "chapter", chapter_id: 12, status: "failed", chunks_done: 1, chunks_total: 6, attempts: 3, error: "model overloaded" }],
    });
    const f = pageApi({
      "GET /v1/books/1/jobs": () => json(200, [failed]),
      "POST /v1/jobs/9/resume": () => json(200, job()),
    }, project({ stage: "needs_attention", active_job: null }));
    renderPage();
    expect(await screen.findByText(/model overloaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /resume/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/resume")).toHaveLength(1));
    expect(screen.getByRole("button", { name: /run proofreading again/i })).toBeInTheDocument();
  });
});

describe("resume after a cancel (F3)", () => {
  it.each(["to_review", "done", "set_up"])("offers Resume when the newest job was cancelled (%s)", async (stage) => {
    const f = pageApi({
      "GET /v1/books/1/jobs": () => json(200, [job({ status: "cancelled" }), job({ id: 8, status: "completed" })]),
      "POST /v1/jobs/9/resume": () => json(200, job()),
    }, project({ stage, open_count: stage === "to_review" ? 3 : 0 }));
    renderPage();
    expect(await screen.findByText(/proofreading was cancelled/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^resume$/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/resume")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/jobs/9/resume")[0])).toEqual({});
  });

  it("offers no Resume when the newest job finished", async () => {
    pageApi({
      "GET /v1/books/1/jobs": () => json(200, [job({ status: "completed" }), job({ id: 8, status: "cancelled" })]),
    }, project({ stage: "to_review", open_count: 3 }));
    renderPage();
    await waitFor(() => expect(jobRows()).toHaveLength(2));
    expect(screen.queryByText(/was cancelled/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^resume$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run proofreading again/i })).toBeInTheDocument();
  });
});

describe("jobs table while a job runs (S1)", () => {
  it("refetches the jobs every 3000 ms while the project has a running job", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let cost = "0.10";
    pageApi({
      "GET /v1/jobs/9": () => json(200, job()),
      "GET /v1/books/1/jobs": () => json(200, [job({ cost_actual: cost })]),
    }, project({ stage: "proofreading", active_job: activeJob() }));
    renderPage();
    await waitFor(() => expect(jobRows()[0]).toHaveTextContent("$0.10 of $4.00"));
    expect(jobRows()[0]).toHaveTextContent("Running");
    cost = "0.40";
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(jobRows()[0]).toHaveTextContent("$0.40 of $4.00"));
  });

  it("shows the job's final status and cost once the project has no active job", async () => {
    // The job's own poll has not noticed yet, so only the projects list reports the end.
    let ended = false;
    let reported = false;
    const f = pageApi({
      "GET /v1/jobs/9": () => json(200, job({ status: "queued", cost_actual: "0" })),
      "GET /v1/projects": () => {
        reported = ended;
        return json(200, [ended
          ? project({ stage: "to_review", open_count: 51, auto_applied_count: 25 })
          : project({ stage: "proofreading", active_job: activeJob({ status: "queued", cost_actual: "0" }) })]);
      },
      "GET /v1/books/1/jobs": () => json(200, [reported
        ? job({ status: "completed", cost_actual: "2.5", stats: { auto_applied: 25 } })
        : job({ status: "queued", cost_actual: "0" })]),
    });
    const { client } = renderPage();
    await waitFor(() => expect(jobRows()[0]).toHaveTextContent("Queued"));
    expect(jobRows()[0]).toHaveTextContent("$0.00 of $4.00");

    ended = true;
    await act(() => client.refetchQueries({ queryKey: ["projects"] }));
    expect(await screen.findByText(/AI applied 25 · 51 need you/)).toBeInTheDocument();
    await waitFor(() => expect(jobRows()[0]).toHaveTextContent("Completed"));
    expect(jobRows()[0]).toHaveTextContent("$2.50 of $4.00");
    expect(jobRows()[0]).toHaveTextContent("25");
    expect(callsTo(f, "GET", "/v1/books/1/jobs").length).toBeGreaterThanOrEqual(2);
  });
});

describe("progress polling and cancel", () => {
  it("polls the job every 3000 ms and stops once it completes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let status = "running";
    const f = pageApi(
      { "GET /v1/jobs/9": () => json(200, job({ status })) },
      project({ stage: "proofreading", active_job: activeJob() }),
    );
    const { client } = renderPage();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await screen.findByText("2/6");
    expect(jobCalls(f)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(2900));
    expect(jobCalls(f)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(200));
    await waitFor(() => expect(jobCalls(f)).toBe(2));
    status = "completed";
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(jobCalls(f)).toBe(3));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["chapter-summary", "1"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs", "1"] });
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(jobCalls(f)).toBe(3);
  });

  it("cancels only after confirmation", async () => {
    const f = pageApi({
      "GET /v1/jobs/9": () => json(200, job()),
      "POST /v1/jobs/9/cancel": () => json(200, job({ status: "cancelled" })),
    }, project({ stage: "proofreading", active_job: activeJob() }));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
    expect(callsTo(f, "POST", "/v1/jobs/9/cancel")).toHaveLength(0);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /cancel proofreading/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/jobs/9/cancel")).toHaveLength(1));
  });
});

describe("always-available parts", () => {
  it("lists jobs newest first with passes, status and cost", async () => {
    pageApi({
      "GET /v1/books/1/jobs": () => json(200, [
        job({ id: 2, status: "completed", passes: ["languagetool"], cost_actual: "0", max_cost: "1", stats: {} }),
        job({ id: 1, status: "completed", cost_actual: "2.5", max_cost: "4", stats: { auto_applied: 214 } }),
      ]),
    });
    renderPage();
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Free check");
    expect(rows[0]).toHaveTextContent("$0.00 of $1.00");
    expect(rows[1]).toHaveTextContent("AI + LanguageTool");
    expect(rows[1]).toHaveTextContent("Completed");
    expect(rows[1]).toHaveTextContent("$2.50 of $4.00");
    expect(rows[1]).toHaveTextContent("214");
  });

  it("offers all four exports for a docx book as download links", async () => {
    pageApi();
    renderPage();
    const link = await screen.findByRole("link", { name: "Word with tracked changes" });
    expect(link).toHaveAttribute("href", "/v1/books/1/export?format=docx-tracked");
    expect(link).toHaveAttribute("download");
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(
      expect.arrayContaining(["Word clean", "Word with tracked changes", "Word review copy", "EPUB clean"]),
    );
    expect(screen.getByText("Word review copy").getAttribute("href")).toBe("/v1/books/1/export?format=docx-review");
    expect(screen.getByRole("link", { name: "Word clean" })).toHaveAttribute("href", "/v1/books/1/export?format=docx-clean");
  });

  it("offers EPUB clean only for an epub book and no review upload", async () => {
    pageApi({ "GET /v1/books/1": () => json(200, { ...BOOK, format: "epub" }) });
    renderPage();
    await screen.findByRole("link", { name: "EPUB clean" });
    expect(screen.queryByRole("link", { name: /Word/ })).not.toBeInTheDocument();
  });

  it("shows the Settings tab with house style and auto-apply", async () => {
    pageApi();
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    expect(await screen.findByRole("button", { name: /^house style$/i })).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: /apply clear-cut fixes/i })).toBeInTheDocument();
  });

  it("deletes only after confirmation, then goes home", async () => {
    const f = pageApi({ "DELETE /v1/books/1": () => json(204) });
    const { client } = renderPage();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await userEvent.click(await screen.findByRole("button", { name: /delete project/i }));
    expect(callsTo(f, "DELETE", "/v1/books/1")).toHaveLength(0);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects"] });
  });

  async function backToSettings() {
    await userEvent.click(screen.getByRole("tab", { name: "Overview" }));
    await userEvent.click(screen.getByRole("tab", { name: "Settings" }));
  }

  it("keeps saved auto-apply values after leaving and re-entering Settings", async () => {
    pageApi({ "PUT /v1/books/1/auto-apply": (init) => json(200, JSON.parse(init.body as string)) });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    await userEvent.selectOptions(await screen.findByLabelText(/minimum severity/i), "low");
    await userEvent.click(screen.getByRole("button", { name: /save auto-apply/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /save auto-apply/i })).toBeEnabled());
    await backToSettings();
    await waitFor(() => expect(screen.getByLabelText(/minimum severity/i)).toHaveValue("low"));
  });

  it("keeps the saved house style after leaving and re-entering Settings", async () => {
    const f = pageApi({
      "PUT /v1/books/1/style-sheet": (init) =>
        json(200, { ...STYLE, ...JSON.parse(init.body as string) }),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    await userEvent.click(await screen.findByRole("button", { name: /^house style$/i }));
    await userEvent.type(await screen.findByLabelText(/words to ignore/i), "Aberdeen");
    await userEvent.click(screen.getByRole("button", { name: /save house style/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /save house style/i })).toBeEnabled());
    await waitFor(() => expect(callsTo(f, "PUT", "/v1/books/1/style-sheet")).toHaveLength(1));
    await backToSettings();
    await userEvent.click(await screen.findByRole("button", { name: /^house style$/i }));
    expect(await screen.findByLabelText(/words to ignore/i)).toHaveValue("Aberdeen");
  });
});

describe("translate + proofread projects", () => {
  const books = {
    "GET /v1/books/1": () => json(200, TRANSLATED_BOOK),
    "GET /v1/books/2": () => json(200, SOURCE_BOOK),
  };

  it("set_up translate project shows the translation estimate and starts a translate job", async () => {
    const f = pageApi({
      ...books,
      "POST /v1/books/1/estimate": () => json(200, estimate("5.5")),
      "POST /v1/books/1/jobs": () => json(201, job({ type: "translate", passes: ["translate"] })),
    }, translateProject());
    const { client } = renderPage();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    expect(await screen.findByText("Estimated cost: $1.00 to $5.50")).toBeInTheDocument();
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/estimate")[0])).toEqual({ type: "translate" });
    expect(screen.queryByRole("button", { name: /start proofreading/i })).not.toBeInTheDocument();
    const limit = screen.getByLabelText(/cost limit/i);
    await waitFor(() => expect(limit).toHaveValue(6));
    await userEvent.clear(limit);
    await userEvent.type(limit, "8");
    await userEvent.click(screen.getByRole("button", { name: "Start translation" }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/jobs")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/jobs")[0])).toEqual({ type: "translate", max_cost: 8 });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs", "1"] });
  });

  it("shows the translation estimate's error, such as a missing translation model", async () => {
    pageApi({
      ...books,
      "POST /v1/books/1/estimate": () => json(409, { detail: "no translation model is configured" }),
    }, translateProject());
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("no translation model is configured");
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
  });

  it("links the source book's details from the header", async () => {
    pageApi(books, translateProject());
    renderPage();
    const link = await screen.findByRole("button", { name: /Translated from Sea Book/ });
    expect(link).toHaveTextContent("Translated from Sea Book · English (GB)");
    await userEvent.click(link);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Sea Book");
    expect(dialog).toHaveTextContent("English (GB)");
    expect(dialog).toHaveTextContent("sea.docx");
  });

  it("no source link on a proofread-only book", async () => {
    const f = pageApi();
    renderPage();
    await screen.findByRole("button", { name: /start proofreading/i });
    expect(screen.queryByText(/Translated from/)).not.toBeInTheDocument();
    expect(callsTo(f, "GET", "/v1/books/2")).toHaveLength(0);
  });

  it("translating shows the job's per-chapter progress and cancels the translation", async () => {
    const translating = job({ type: "translate", passes: ["translate"] });
    pageApi({
      ...books,
      "GET /v1/jobs/9": () => json(200, translating),
      "GET /v1/books/1/jobs": () => json(200, [translating]),
    }, translateProject({ stage: "translating", active_job: activeJob() }));
    renderPage();
    expect(await screen.findByText("2/6")).toBeInTheDocument();
    expect(screen.getByText("Translating")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Cost so far" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^review$/i })).not.toBeInTheDocument();
    await waitFor(() => expect(jobRows()[0]).toHaveTextContent("Translation"));
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "Cancel translation" })).toBeInTheDocument();
  });

  it("translated shows the counts and the proofreading start, with no re-run button", async () => {
    pageApi({
      ...books,
      "GET /v1/books/1/translation": () => json(200, { counts: { done: 40 }, flagged: [] }),
      "GET /v1/books/1/jobs": () => json(200, [job({ type: "translate", passes: ["translate"], status: "completed" })]),
    }, translateProject({ stage: "translated", translation: { done: 40 } }));
    renderPage();
    expect(await screen.findByText(/40 paragraphs translated/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /start proofreading/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run proofreading again/i })).not.toBeInTheDocument();
    expect(screen.getByText("Translated")).toBeInTheDocument();
  });

  it("a failed translation explains itself as a translation", async () => {
    const failed = job({ type: "translate", passes: ["translate"], status: "failed" });
    pageApi({ ...books, "GET /v1/books/1/jobs": () => json(200, [failed]) },
      translateProject({ stage: "needs_attention" }));
    renderPage();
    expect(await screen.findByText(/Translation stopped because something went wrong/)).toBeInTheDocument();
  });
});
