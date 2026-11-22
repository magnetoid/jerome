import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActivityPage from "./ActivityPage";
import { json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

const row = (over: Record<string, unknown> = {}) => ({
  id: 1, at: "2026-11-02T09:00:00Z", actor: "janus-desktop", channel: "mcp",
  tool: "update_settings", book_id: 5, chapter_id: null, job_id: null,
  summary: "Changed the spelling variant", before: null, after: null, ...over,
});

describe("ActivityPage", () => {
  it("lists rows with who, what and the before and after values", async () => {
    mockApi({
      "GET /v1/agent-actions?limit=50&offset=0": json(
        200,
        [row({ before: { variant: "en-US" }, after: { variant: "en-GB" }, chapter_id: 7, job_id: 3 })],
        { "X-Total-Count": "1" },
      ),
    });
    renderApp(<ActivityPage />, "/activity");
    expect(screen.getByRole("heading", { name: "Janus activity" })).toBeInTheDocument();
    expect(await screen.findByText("Janus (janus-desktop)")).toBeInTheDocument();
    expect(screen.getByText("Changed the spelling variant")).toBeInTheDocument();
    expect(screen.getByText(/"variant":"en-US"/)).toBeInTheDocument();
    expect(screen.getByText(/"variant":"en-GB"/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Book 5" })).toHaveAttribute("href", "/projects/5");
    expect(screen.getByRole("link", { name: "Chapter 7" })).toHaveAttribute("href", "/projects/5/review/7");
    expect(screen.getByText("Job 3")).toBeInTheDocument();
  });

  it("filters by book and links back to it", async () => {
    const f = mockApi({
      "GET /v1/agent-actions?book_id=5&limit=50&offset=0": json(200, [row()], { "X-Total-Count": "1" }),
    });
    renderApp(<ActivityPage />, "/activity?book=5");
    expect(await screen.findByText("Janus (janus-desktop)")).toBeInTheDocument();
    expect(f.mock.calls[0][0]).toBe("/v1/agent-actions?book_id=5&limit=50&offset=0");
    expect(screen.getByRole("link", { name: "Back to the book" })).toHaveAttribute("href", "/projects/5");
  });

  it("says so when there is nothing", async () => {
    mockApi({ "GET /v1/agent-actions?limit=50&offset=0": json(200, [], { "X-Total-Count": "0" }) });
    renderApp(<ActivityPage />, "/activity");
    expect(await screen.findByText("Janus hasn't changed anything yet.")).toBeInTheDocument();
  });

  it("pages by X-Total-Count", async () => {
    const f = mockApi({
      "GET /v1/agent-actions?limit=50&offset=0": json(200, [row({ summary: "first page" })], { "X-Total-Count": "60" }),
      "GET /v1/agent-actions?limit=50&offset=50": json(200, [row({ id: 2, summary: "second page" })], { "X-Total-Count": "60" }),
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
