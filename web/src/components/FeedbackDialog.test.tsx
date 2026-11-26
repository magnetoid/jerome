import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import AppShell from "./AppShell";
import { ME, MEMBER, json, mockApi, renderApp } from "@/test-utils";

const capture = vi.hoisted(() => vi.fn());
vi.mock("@/lib/screenshot", () => ({ captureScreen: capture }));

beforeEach(() => {
  capture.mockReset();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:shot"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

function setup(me: typeof ME | typeof MEMBER = MEMBER) {
  const f = mockApi({
    "GET /v1/auth/me": json(200, me),
    "POST /v1/feedback": json(201, { id: 1 }),
  });
  renderApp(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/projects/:id" element={<p>project page</p>} />
      </Route>
    </Routes>,
    "/projects/3?tab=jobs",
  );
  return f;
}

function sent(f: ReturnType<typeof mockApi>): FormData {
  const call = f.mock.calls.find(([p, i]) => p === "/v1/feedback" && i?.method === "POST");
  expect(call).toBeDefined();
  return call![1]!.body as FormData;
}

describe("Send feedback", () => {
  it("takes a screenshot, then sends the kind, message, page and picture", async () => {
    const shot = new Blob(["png"], { type: "image/png" });
    capture.mockResolvedValue(shot);
    const f = setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send feedback" }));
    expect(capture).toHaveBeenCalledOnce();
    const dialog = await screen.findByRole("dialog", { name: "Send feedback" });
    expect(screen.getByRole("img", { name: "Screenshot of the page" })).toHaveAttribute("src", "blob:shot");
    const send = screen.getByRole("button", { name: "Send feedback", hidden: false });
    expect(dialog).toContainElement(send);
    expect(send).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: "Feature" }));
    expect(screen.getByRole("radio", { name: "Feature" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByLabelText("Your message"));
    await user.paste("  Word counts per chapter  ");
    await user.click(send);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const body = sent(f);
    expect(body.get("kind")).toBe("feature");
    expect(body.get("message")).toBe("Word counts per chapter");
    expect(body.get("page")).toBe("/projects/3?tab=jobs");
    expect(body.get("viewport")).toMatch(/^\d+x\d+$/);
    expect(body.get("screenshot")).toBeInstanceOf(Blob);
  });

  it("leaves the screenshot out when unticked", async () => {
    capture.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    const f = setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send feedback" }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("checkbox", { name: "Attach a screenshot of this page" }));
    await user.click(screen.getByLabelText("Your message"));
    await user.paste("The export is slow");
    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const body = sent(f);
    expect(body.get("kind")).toBe("bug");
    expect(body.get("screenshot")).toBeNull();
  });

  it("still opens when the screenshot fails", async () => {
    capture.mockResolvedValue(null);
    setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send feedback" }));
    await screen.findByRole("dialog");
    expect(screen.getByText("No screenshot could be taken of this page.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("shows the Feedback page link to the super admin only", async () => {
    capture.mockResolvedValue(null);
    setup(ME);
    expect(await screen.findByRole("link", { name: "Feedback" })).toHaveAttribute("href", "/feedback");
  });

  it("hides the Feedback page link from members", async () => {
    setup(MEMBER);
    expect(await screen.findByRole("button", { name: "Send feedback" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Feedback" })).not.toBeInTheDocument();
  });
});
