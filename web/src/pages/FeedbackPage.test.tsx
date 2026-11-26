import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import FeedbackPage, { type FeedbackItem } from "./FeedbackPage";
import { ME, MEMBER, Where, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

const BUG: FeedbackItem = {
  id: 2,
  kind: "bug",
  message: "Save does nothing.\nOn the project page.",
  page: "/projects/3",
  viewport: "1440x900",
  user_agent: "Firefox",
  has_screenshot: true,
  status: "new",
  created_by: "bo@example.com",
  created_at: "2026-11-26T10:00:00Z",
};
const DONE: FeedbackItem = {
  ...BUG,
  id: 1,
  kind: "recommendation",
  message: "Bigger buttons",
  has_screenshot: false,
  status: "done",
};

function setup(me: typeof ME | typeof MEMBER = ME) {
  const f = mockApi({
    "GET /v1/auth/me": json(200, me),
    "GET /v1/feedback": () => json(200, [BUG, DONE]),
    "PATCH /v1/feedback/2": json(200, { ...BUG, status: "done" }),
    "DELETE /v1/feedback/2": json(204),
  });
  renderApp(
    <>
      <Routes>
        <Route path="/feedback" element={<FeedbackPage />} />
        <Route path="/" element={<p>home</p>} />
      </Routes>
      <Where />
    </>,
    "/feedback",
  );
  return f;
}

describe("FeedbackPage", () => {
  it("lists new feedback with its screenshot and details", async () => {
    setup();
    const item = await screen.findByRole("listitem", { name: "Bug from bo@example.com" });
    expect(within(item).getByText(/Save does nothing/)).toBeInTheDocument();
    expect(within(item).getByRole("link", { name: "/projects/3" })).toHaveAttribute("href", "/projects/3");
    expect(within(item).getByRole("img")).toHaveAttribute("src", "/v1/feedback/2/screenshot");
    expect(screen.queryByText("Bigger buttons")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: /All/ }));
    expect(screen.getByText("Bigger buttons")).toBeInTheDocument();
  });

  it("marks feedback done", async () => {
    const f = setup();
    const user = userEvent.setup();
    const item = await screen.findByRole("listitem", { name: "Bug from bo@example.com" });
    await user.click(within(item).getByRole("button", { name: "Mark done" }));
    await waitFor(() =>
      expect(
        f.mock.calls.some(([p, i]) => p === "/v1/feedback/2" && i?.method === "PATCH" && i.body === '{"status":"done"}'),
      ).toBe(true),
    );
  });

  it("deletes feedback after asking", async () => {
    const f = setup();
    const user = userEvent.setup();
    const item = await screen.findByRole("listitem", { name: "Bug from bo@example.com" });
    await user.click(within(item).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(f.mock.calls.some(([p, i]) => p === "/v1/feedback/2" && i?.method === "DELETE")).toBe(true),
    );
  });

  it("sends members back to the projects", async () => {
    const f = setup(MEMBER);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
    expect(f.mock.calls.some(([p]) => p === "/v1/feedback")).toBe(false);
  });
});
