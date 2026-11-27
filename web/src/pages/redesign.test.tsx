import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import ActivityPage from "./ActivityPage";
import ChangePasswordPage from "./ChangePasswordPage";
import LoginPage from "./LoginPage";
import ProjectsPage from "./ProjectsPage";
import { activeJob, job, pageApi, project as pageProject, renderPage } from "@/components/project/fixtures";
import { PROJECTS_KEY } from "@/hooks/useProjects";
import { ME, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

type P = Record<string, unknown>;
const project = (over: P): P => ({
  id: 1, title: "Untitled", format: "docx", variant: "en-GB", stage: "set_up",
  open_count: 0, auto_applied_count: 0, decided_count: 0, active_job: null,
  last_activity: null, created_at: "2026-11-01T10:00:00Z", ...over,
});
const never = () => new Promise<Response>(() => {});
const bodyRows = () => screen.getAllByRole("row").slice(1);

describe("Login and Change password", () => {
  it("Login shows the logo and says what Jerome is for, around the same form", () => {
    const { container } = renderApp(<LoginPage />);
    expect(container.querySelector("[data-logo-mark]")).toBeInTheDocument();
    expect(screen.getByText("Jerome")).toHaveClass("font-display");
    expect(screen.getByText("Proofreading and translation for books")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(screen.getByRole("main").firstElementChild).toHaveClass("page-enter");
  });

  it("Change password shows the logo above its card, without the tagline", async () => {
    mockApi({ "GET /v1/auth/me": json(200, ME) });
    const { container } = renderApp(
      <Routes>
        <Route path="/change-password" element={<ChangePasswordPage />} />
      </Routes>,
      "/change-password",
    );
    expect(await screen.findByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(container.querySelector("[data-logo-mark]")).toBeInTheDocument();
    expect(screen.queryByText("Proofreading and translation for books")).toBeNull();
  });
});

describe("Projects", () => {
  it("shows the shape of the list while it loads, and says so to screen readers", () => {
    mockApi({ "GET /v1/projects": never });
    const { container } = renderApp(<ProjectsPage />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading…$/);
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThanOrEqual(5);
  });

  it("sets its title in the display face", () => {
    mockApi({ "GET /v1/projects": never });
    renderApp(<ProjectsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Projects" })).toHaveClass("font-display");
  });

  it("brings the first twelve rows in one after another, and later rows at once", async () => {
    const list = Array.from({ length: 13 }, (_, i) => project({ id: i + 1, title: `Book ${i + 1}` }));
    mockApi({ "GET /v1/projects": () => json(200, list) });
    renderApp(<ProjectsPage />);
    await screen.findByText("Book 1");
    const rows = bodyRows();
    expect(rows).toHaveLength(13);
    rows.slice(0, 12).forEach((row, i) => {
      expect(row).toHaveClass("row-enter");
      expect(row.style.getPropertyValue("--i")).toBe(String(i));
    });
    expect(rows[12]).not.toHaveClass("row-enter");
  });

  it("keeps its rows in place when the list is fetched again", async () => {
    const list = [project({ id: 1, title: "Book 1" }), project({ id: 2, title: "Book 2" })];
    mockApi({ "GET /v1/projects": () => json(200, list) });
    const { client } = renderApp(<ProjectsPage />);
    await screen.findByText("Book 1");
    const before = bodyRows();
    act(() => {
      client.setQueryData(PROJECTS_KEY, list.map((p) => ({ ...p, open_count: 7 })));
    });
    expect(await screen.findAllByText(/7 left/)).toHaveLength(2);
    const after = bodyRows();
    expect(after[0]).toBe(before[0]);
    expect(after[1]).toBe(before[1]);
  });

  it("plays the stagger once: a list that comes back in another order does not replay it", async () => {
    const list = [1, 2, 3].map((id) => project({ id, title: `Book ${id}` }));
    mockApi({ "GET /v1/projects": () => json(200, list) });
    const { client } = renderApp(<ProjectsPage />);
    await screen.findByText("Book 1");
    const before = bodyRows();
    expect(before[0]).toHaveClass("row-enter");
    // Once the rows are in, the class goes: a row the browser moves would play it again.
    await waitFor(() => expect(bodyRows()[0]).not.toHaveClass("row-enter"), { timeout: 3000 });
    act(() => {
      client.setQueryData(PROJECTS_KEY, [list[2], list[0], list[1]]);
    });
    const titles = () => bodyRows().map((r) => within(r).getAllByRole("cell")[0].textContent);
    await waitFor(() => expect(titles()).toEqual(["Book 3", "Book 1", "Book 2"]));
    const after = bodyRows();
    expect(after[0]).toBe(before[2]);
    for (const row of after) expect(row).not.toHaveClass("row-enter");
  });

  it("shows each title as a book's name", async () => {
    mockApi({ "GET /v1/projects": () => json(200, [project({ id: 1, title: "Sea Book" })]) });
    renderApp(<ProjectsPage />);
    expect(await screen.findByRole("link", { name: "Sea Book" })).toHaveClass("font-display");
  });

  it("still offers a first project when there are none", async () => {
    mockApi({ "GET /v1/projects": () => json(200, []) });
    renderApp(<ProjectsPage />);
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+ New project" })).toHaveAttribute("href", "/projects/new");
  });

  it("shows running work as running", async () => {
    const running = { id: 9, status: "running", chapters_done: 2, chapters_total: 8, cost_actual: "1.2", max_cost: "5" };
    mockApi({
      "GET /v1/projects": () => json(200, [project({ id: 4, title: "Busy Book", stage: "proofreading", active_job: running })]),
    });
    renderApp(<ProjectsPage />);
    const strip = await screen.findByRole("region", { name: "Running jobs" });
    expect(within(strip).getByRole("progressbar")).toHaveAttribute("data-running", "true");
    expect(within(strip).getByRole("link", { name: "Busy Book" })).toHaveClass("font-display");
  });
});

describe("Project page", () => {
  it("shows the shape of the page while it loads", () => {
    pageApi({ "GET /v1/projects": never });
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading…$/);
  });

  it("sets the book's title in the display face", async () => {
    pageApi();
    renderPage();
    const title = await screen.findByRole("heading", { level: 1 });
    expect(title).toHaveClass("font-display");
  });

  it("shimmers the bars of work that is running, and no others", async () => {
    pageApi(
      { "GET /v1/jobs/9": () => json(200, job()) },
      pageProject({ stage: "proofreading", active_job: activeJob() }),
    );
    renderPage();
    expect(await screen.findByText("2/6")).toBeInTheDocument();
    const bar = (text: string) => within(screen.getByText(text).closest("li")!).getByRole("progressbar");
    expect(bar("2/6")).toHaveAttribute("data-running", "true");
    expect(bar("5/5")).not.toHaveAttribute("data-running");
    expect(screen.getByRole("progressbar", { name: "Cost so far" })).toHaveAttribute("data-running", "true");
  });
});

describe("Activity", () => {
  it("shows the shape of the table while it loads", () => {
    mockApi({ "GET /v1/activity?limit=50&offset=0": never });
    renderApp(<ActivityPage />, "/activity");
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading…$/);
    expect(screen.getByRole("heading", { level: 1, name: "Activity" })).toHaveClass("font-display");
  });
});
