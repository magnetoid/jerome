import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import ChangePasswordPage from "./ChangePasswordPage";
import { ME, Where, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

function setup(me = ME) {
  const fetchMock = mockApi({
    "GET /v1/auth/me": json(200, me),
    "POST /v1/auth/password": json(204),
    "POST /v1/auth/logout": json(204),
  });
  renderApp(
    <>
      <Routes>
        <Route path="/change-password" element={<ChangePasswordPage />} />
        <Route path="*" element={null} />
      </Routes>
      <Where />
    </>,
    "/change-password",
  );
  return fetchMock;
}

async function fill(current: string, next: string, confirm: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Current password"), current);
  await user.type(screen.getByLabelText("New password"), next);
  await user.type(screen.getByLabelText("Confirm new password"), confirm);
  await user.click(screen.getByRole("button", { name: "Change password" }));
}

describe("ChangePasswordPage", () => {
  it("rejects a short new password without calling the API", async () => {
    const f = setup();
    await fill("old", "short", "short");
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 10 characters");
    expect(f.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false);
  });

  it("rejects a mismatched confirmation", async () => {
    setup();
    await fill("old", "long-enough-pw", "long-enough-px");
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
  });

  it("shows the API detail on error", async () => {
    mockApi({
      "GET /v1/auth/me": json(200, ME),
      "POST /v1/auth/password": json(400, { detail: "Current password is wrong" }),
    });
    renderApp(<ChangePasswordPage />);
    await fill("old", "long-enough-pw", "long-enough-pw");
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is wrong");
  });

  it("changes the password, refreshes me and goes home", async () => {
    const f = setup();
    await fill("old", "long-enough-pw", "long-enough-pw");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
    const post = f.mock.calls.find(([p]) => p === "/v1/auth/password")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({
      current: "old",
      new: "long-enough-pw",
    });
    expect(f.mock.calls.filter(([p]) => p === "/v1/auth/me").length).toBeGreaterThanOrEqual(2);
  });

  it("says so and offers Log out when the change is forced", async () => {
    setup({ ...ME, must_change_password: true });
    expect(await screen.findByText("Choose a new password to continue")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Log out" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/login"));
  });

  it("offers no Log out button when not forced", async () => {
    setup();
    await screen.findByLabelText("Current password");
    expect(screen.queryByRole("button", { name: "Log out" })).not.toBeInTheDocument();
  });
});
