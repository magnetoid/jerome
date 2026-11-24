import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActivityPage from "./ActivityPage";
import { json, mockApi, renderApp } from "@/test-utils";
import { whoLabel } from "@/lib/activity";

afterEach(() => vi.unstubAllGlobals());

const agent = (over: Record<string, unknown> = {}) => ({
  kind: "agent", id: 1, at: "2026-11-02T09:00:00Z", actor: "janus-desktop", channel: "mcp",
  book_id: 5, book_title: "Moby", chapter_id: null, chapter_title: null,
  summary: "Changed the spelling variant", action: null, suggestion_id: null, original: null,
  replacement: null, tool: "update_settings", job_id: null, before: null, after: null,
  revertable: false, revert_blocked: null, ...over,
});

const decision = (over: Record<string, unknown> = {}) => ({
  ...agent(),
  kind: "decision", id: 10, actor: "ann@example.com", channel: "web", tool: null,
  chapter_id: 7, chapter_title: "Loomings", summary: "Accepted “teh” → “the”", action: "accept",
  suggestion_id: 3, original: "teh", replacement: "the", revertable: true, ...over,
});

const page = (rows: unknown[], total = rows.length) => json(200, rows, { "X-Total-Count": String(total) });
const ALL = "GET /v1/activity?limit=50&offset=0";

describe("ActivityPage", () => {
  it("lists agent changes with who, what and the before and after values", async () => {
    mockApi({
      [ALL]: page([agent({ before: { variant: "en-US" }, after: { variant: "en-GB" }, chapter_id: 7, chapter_title: "Loomings", job_id: 3 })]),
    });
    renderApp(<ActivityPage />, "/activity");
    expect(screen.getByRole("heading", { name: "Activity" })).toBeInTheDocument();
    expect(await screen.findByText("Janus (janus-desktop)")).toBeInTheDocument();
    expect(screen.getByText("Changed the spelling variant")).toBeInTheDocument();
    expect(screen.getByText(/"variant":"en-US"/)).toBeInTheDocument();
    expect(screen.getByText(/"variant":"en-GB"/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Moby" })).toHaveAttribute("href", "/projects/5");
    expect(screen.getByRole("link", { name: "Loomings" })).toHaveAttribute("href", "/projects/5/review/7");
    expect(screen.getByText("Job 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revert" })).not.toBeInTheDocument();
  });

  it("reverts one correction and refetches", async () => {
    let reverted = false;
    const f = mockApi({
      [ALL]: () => page(reverted
        ? [decision({ id: 11, action: "undo", summary: "Undid the decision on “teh” → “the”", revertable: false }),
           decision({ revertable: false, revert_blocked: "Changed again after this; revert the later change first." })]
        : [decision()]),
      "POST /v1/activity/decisions/10/revert": () => { reverted = true; return json(200, { id: 3, status: "open" }); },
    });
    renderApp(<ActivityPage />, "/activity");
    const user = userEvent.setup();
    expect(await screen.findByText("ann@example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revert" }));
    expect(await screen.findByText("Undid the decision on “teh” → “the”")).toBeInTheDocument();
    expect(f.mock.calls.some(([p, i]) => p === "/v1/activity/decisions/10/revert" && i?.method === "POST")).toBe(true);
    const buttons = screen.getAllByRole("button", { name: "Revert" });
    expect(buttons).toHaveLength(1); // an undo row has no button
    expect(buttons[0]).toBeDisabled();
    expect(screen.getByText("Changed again after this; revert the later change first.")).toBeInTheDocument();
  });

  it("filters by book, chapter and who", async () => {
    const f = mockApi({
      "GET /v1/activity?book_id=5&limit=50&offset=0": page([decision()]),
      "GET /v1/books/5/chapter-summary": json(200, [
        { chapter_id: 7, ordinal: 1, title: "Loomings", proofread: true, open: 0, auto_applied: 0, decided: 1 },
      ]),
      "GET /v1/activity?book_id=5&chapter_id=7&limit=50&offset=0": page([decision()]),
      "GET /v1/activity?book_id=5&chapter_id=7&who=automatic&limit=50&offset=0": page([]),
    });
    renderApp(<ActivityPage />, "/activity?book=5");
    const user = userEvent.setup();
    expect(await screen.findByText("ann@example.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the book" })).toHaveAttribute("href", "/projects/5");
    expect(screen.queryByRole("link", { name: "Moby" })).not.toBeInTheDocument(); // one book: no book link
    await user.selectOptions(await screen.findByLabelText("Chapter"), "Loomings");
    await user.selectOptions(screen.getByLabelText("Who"), "Automatic");
    expect(await screen.findByText("Nothing has been changed yet.")).toBeInTheDocument();
    const paths = f.mock.calls.map(([p]) => p);
    expect(paths).toContain("/v1/activity?book_id=5&chapter_id=7&who=automatic&limit=50&offset=0");
  });

  it("says so when there is nothing", async () => {
    mockApi({ [ALL]: page([]) });
    renderApp(<ActivityPage />, "/activity");
    expect(await screen.findByText("Nothing has been changed yet.")).toBeInTheDocument();
  });

  it("pages by X-Total-Count", async () => {
    const f = mockApi({
      [ALL]: page([agent({ summary: "first page" })], 60),
      "GET /v1/activity?limit=50&offset=50": page([agent({ id: 2, summary: "second page" })], 60),
    });
    renderApp(<ActivityPage />, "/activity");
    const user = userEvent.setup();
    expect(await screen.findByText("first page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("second page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await waitFor(() => expect(screen.getByText("first page")).toBeInTheDocument());
    expect(f).toHaveBeenCalledTimes(3);
  });
});

describe("whoLabel", () => {
  it("names each kind of author", () => {
    expect(whoLabel({ actor: "k", channel: "mcp" })).toBe("Janus (k)");
    expect(whoLabel({ actor: "x", channel: "auto" })).toBe("Automatic");
    expect(whoLabel({ actor: "ann", channel: "word" })).toBe("ann (Word)");
    expect(whoLabel({ actor: "ci", channel: "api" })).toBe("ci (API)");
    expect(whoLabel({ actor: "ann", channel: "web" })).toBe("ann");
  });
});
