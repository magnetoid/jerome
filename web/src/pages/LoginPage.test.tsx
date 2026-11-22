import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import LoginPage from "./LoginPage";
import { ME, Where, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

function setup(entry = "/login") {
  return renderApp(
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={null} />
      </Routes>
      <Where />
    </>,
    entry,
  );
}

async function submit(email = "ann@example.com", password = "pw") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password + "{Enter}");
}

describe("LoginPage", () => {
  it("shows Wrong email or password on 401", async () => {
    mockApi({ "POST /v1/auth/login": json(401, { detail: "Wrong email or password" }) });
    setup();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password");
    expect(screen.getByTestId("where")).toHaveTextContent("/login");
  });

  it("shows the wait in minutes on 429, rounded up", async () => {
    mockApi({
      "POST /v1/auth/login": json(429, { detail: "Too many failed logins; try again later" }, { "Retry-After": "901" }),
    });
    setup();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Try again in 16 minutes.",
    );
  });

  it("shows seconds when the wait is under a minute", async () => {
    mockApi({
      "POST /v1/auth/login": json(429, { detail: "x" }, { "Retry-After": "30" }),
    });
    setup();
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Try again in 30 seconds.",
    );
  });

  it("goes to /change-password when a change is forced, even with next", async () => {
    mockApi({ "POST /v1/auth/login": json(200, { ...ME, must_change_password: true }) });
    setup("/login?next=%2Fteam");
    await submit();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/change-password"));
  });

  it("goes to next after login", async () => {
    mockApi({ "POST /v1/auth/login": json(200, ME) });
    setup("/login?next=%2Fprojects%2F3%3Ftab%3D2");
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent("/projects/3?tab=2"),
    );
  });

  it.each(["//evil.example.com", "https://evil.example.com", "/\\evil.example.com"])(
    "ignores unsafe next %s",
    async (next) => {
      mockApi({ "POST /v1/auth/login": json(200, ME) });
      setup("/login?next=" + encodeURIComponent(next));
      await submit();
      await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
    },
  );

  it("seeds the me query so the shell shows the user straight away", async () => {
    mockApi({ "POST /v1/auth/login": json(200, ME) });
    const { client } = setup();
    await submit();
    await waitFor(() => expect(client.getQueryData(["me"])).toEqual(ME));
  });

  it("disables the button while pending", async () => {
    let release!: () => void;
    mockApi({
      "POST /v1/auth/login": () =>
        new Promise<Response>((r) => {
          release = () => r(json(200, ME));
        }),
    });
    setup();
    await submit();
    expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
  });
});
