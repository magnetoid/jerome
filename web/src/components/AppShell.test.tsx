import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import AppShell from "./AppShell";
import { ME, Where, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

function setup(me = ME) {
  const f = mockApi({
    "GET /v1/auth/me": json(200, me),
    "POST /v1/auth/logout": json(204),
  });
  const view = renderApp(
    <>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<p>home page</p>} />
        </Route>
        <Route path="/login" element={<p>login page</p>} />
        <Route path="/change-password" element={<p>change page</p>} />
      </Routes>
      <Where />
    </>,
  );
  return { f, ...view };
}

describe("AppShell", () => {
  it("shows the top bar with links and the page", async () => {
    setup();
    expect(await screen.findByText("home page")).toBeInTheDocument();
    expect(screen.getByText("Jerome")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Team" })).toHaveAttribute("href", "/team");
    expect(screen.getByRole("link", { name: "+ New project" })).toHaveAttribute("href", "/projects/new");
  });

  it("redirects to /change-password when a change is forced", async () => {
    setup({ ...ME, must_change_password: true });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/change-password"));
    expect(screen.queryByText("home page")).not.toBeInTheDocument();
  });

  it("account menu shows the name and Change password", async () => {
    setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Ann/ }));
    expect(await screen.findByRole("menuitem", { name: "Change password" })).toBeInTheDocument();
  });

  it("Log out posts, clears the cache and goes to /login", async () => {
    const { f, client } = setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Ann/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Log out" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/login"));
    expect(f.mock.calls.some(([p, i]) => p === "/v1/auth/logout" && i?.method === "POST")).toBe(true);
    expect(client.getQueryData(["me"])).toBeUndefined();
  });
});

describe("AppShell theme", () => {
  it("offers System, Light and Dark and remembers the choice", async () => {
    setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Ann/ }));
    expect(await screen.findByText("Theme")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "System" })).toBeChecked();
    expect(screen.getByRole("menuitemradio", { name: "Light" })).not.toBeChecked();

    await user.click(screen.getByRole("menuitemradio", { name: "Dark" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("jerome-theme")).toBe("dark");

    await user.click(await screen.findByRole("button", { name: /Ann/ }));
    expect(await screen.findByRole("menuitemradio", { name: "Dark" })).toBeChecked();
  });
});
