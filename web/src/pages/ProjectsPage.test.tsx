import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProjectsPage from "./ProjectsPage";
import { json, mockApi, renderApp } from "@/test-utils";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type P = Record<string, unknown>;
function project(over: P): P {
  return {
    id: 1, title: "Untitled", format: "docx", variant: "en-GB", stage: "set_up",
    open_count: 0, auto_applied_count: 0, decided_count: 0, active_job: null,
    last_activity: null, created_at: "2026-11-01T10:00:00Z", ...over,
  };
}
const job = (over: P = {}) => ({
  id: 9, status: "running", chapters_done: 2, chapters_total: 8,
  cost_actual: "1.2", max_cost: "5", ...over,
});

const LIST = [
  project({ id: 3, title: "Zebra Book", stage: "set_up" }),
  project({ id: 1, title: "Alpha Book", stage: "to_review", open_count: 12, auto_applied_count: 40, decided_count: 5, variant: "de-DE",
    last_activity: { actor: "Ann", at: new Date(Date.now() - 3 * 3600_000).toISOString() } }),
  project({ id: 2, title: "Mid Book", stage: "done" }),
  project({ id: 4, title: "Busy Book", stage: "proofreading", active_job: job() }),
  project({ id: 5, title: "Capped Book", stage: "needs_attention", active_job: job({ id: 10, status: "paused_cost_cap" }) }),
];

function listRoutes(list: P[]) {
  return mockApi({ "GET /v1/projects": () => json(200, list) });
}
const listCalls = (f: ReturnType<typeof mockApi>) =>
  f.mock.calls.filter(([p]) => p === "/v1/projects").length;

describe("ProjectsPage", () => {
  it("renders rows in API order with one correctly labelled next step each", async () => {
    listRoutes(LIST);
    renderApp(<ProjectsPage />);
    await screen.findByText("Zebra Book");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual([
      "Zebra Book", "Alpha Book", "Mid Book", "Busy Book", "Capped Book",
    ]);
    const step = (i: number) => within(rows[i]).getByRole("link");
    expect(within(rows[0]).getAllByRole("link")).toHaveLength(1);
    expect(step(0)).toHaveTextContent("Start proofreading");
    expect(step(0)).toHaveAttribute("href", "/projects/3");
    expect(step(1)).toHaveTextContent("Review (12 left)");
    expect(step(1)).toHaveAttribute("href", "/projects/1/review");
    expect(step(2)).toHaveTextContent("Export");
    expect(step(3)).toHaveTextContent("View progress");
    expect(step(4)).toHaveTextContent("Resume");
    expect(step(4)).toHaveAttribute("href", "/projects/5");
  });

  it("shows stage labels, counts, language and last activity", async () => {
    listRoutes(LIST);
    renderApp(<ProjectsPage />);
    const row = (await screen.findByText("Alpha Book")).closest("tr")!;
    expect(within(row).getByText("To review")).toBeInTheDocument();
    expect(within(row).getByText("German (DE)")).toBeInTheDocument();
    expect(within(row).getByText(/^12 left ·/)).toBeInTheDocument();
    expect(within(row).getByText(/40 applied by AI/)).toBeInTheDocument();
    expect(within(row).getByText(/5 decided/)).toBeInTheDocument();
    expect(within(row).getByText("Ann, 3 h ago")).toBeInTheDocument();
    const other = screen.getByText("Zebra Book").closest("tr")!;
    expect(within(other).getByText("—")).toBeInTheDocument();
    expect(within(other).getByText("Set up")).toBeInTheDocument();
  });

  it("filters by title, case-insensitively", async () => {
    const user = userEvent.setup();
    listRoutes(LIST);
    renderApp(<ProjectsPage />);
    await screen.findByText("Zebra Book");
    await user.type(screen.getByLabelText("Search projects"), "aLPHa");
    expect(screen.getByText("Alpha Book")).toBeInTheDocument();
    expect(screen.queryByText("Zebra Book")).not.toBeInTheDocument();
  });

  it("shows the empty state with a New project button", async () => {
    listRoutes([]);
    renderApp(<ProjectsPage />);
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+ New project" })).toHaveAttribute("href", "/projects/new");
  });

  it("shows the running strip with progress and cost, only for queued or running jobs", async () => {
    listRoutes(LIST);
    renderApp(<ProjectsPage />);
    const strip = await screen.findByRole("region", { name: "Running jobs" });
    expect(within(strip).getByText("Busy Book")).toBeInTheDocument();
    expect(within(strip).getByText("2 / 8")).toBeInTheDocument();
    expect(within(strip).getByText("$1.20 of $5.00")).toBeInTheDocument();
    expect(within(strip).queryByText("Capped Book")).not.toBeInTheDocument();
    expect(within(strip).getByRole("progressbar")).toBeInTheDocument();
  });

  it("hides the strip when nothing is running", async () => {
    listRoutes([project({ id: 1, title: "Calm" })]);
    renderApp(<ProjectsPage />);
    await screen.findByText("Calm");
    expect(screen.queryByRole("region", { name: "Running jobs" })).not.toBeInTheDocument();
  });

  it("polls every 3000 ms while a job runs and stops once none is active", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let data: P[] = [project({ id: 1, title: "Busy", stage: "proofreading", active_job: job() })];
    const f = mockApi({ "GET /v1/projects": () => json(200, data) });
    renderApp(<ProjectsPage />);
    await screen.findByRole("table");
    expect(listCalls(f)).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(2900));
    expect(listCalls(f)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(200));
    await waitFor(() => expect(listCalls(f)).toBe(2));

    data = [project({ id: 1, title: "Busy", stage: "to_review", open_count: 3 })];
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(listCalls(f)).toBe(3));
    await screen.findByText("Review (3 left)");

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(listCalls(f)).toBe(3);
  });

  it("does not poll when the only job is paused on its cost cap", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = listRoutes([
      project({ id: 5, title: "Capped", stage: "needs_attention", active_job: job({ status: "paused_cost_cap" }) }),
    ]);
    renderApp(<ProjectsPage />);
    await screen.findByText("Capped");
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(listCalls(f)).toBe(1);
  });
});
