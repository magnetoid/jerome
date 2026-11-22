import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import type { QueryClient } from "@tanstack/react-query";
import TeamPage from "./TeamPage";
import { ME, json, mockApi, renderApp } from "@/test-utils";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const ann = {
  id: 1, email: "ann@example.com", name: "Ann", active: true,
  must_change_password: false, last_login_at: "2026-11-20T10:00:00Z", created_at: "2026-11-01T10:00:00Z",
};
const bob = {
  id: 2, email: "bob@example.com", name: "Bob", active: true,
  must_change_password: true, last_login_at: null, created_at: "2026-11-02T10:00:00Z",
};

function cachedText(client: QueryClient): string {
  const q = client.getQueryCache().getAll().map((x) => x.state.data);
  const m = client.getMutationCache().getAll().map((x) => [x.state.data, x.state.variables]);
  return JSON.stringify([q, m]);
}

beforeEach(() => vi.mocked(toast.error).mockClear());
afterEach(() => vi.unstubAllGlobals());

function setup(extra: Parameters<typeof mockApi>[0] = {}) {
  const f = mockApi({
    "GET /v1/auth/me": json(200, ME),
    "GET /v1/users": json(200, [ann, bob]),
    ...extra,
  });
  const view = renderApp(<TeamPage />);
  return { f, ...view };
}

describe("TeamPage", () => {
  it("lists members with status, last login and the must-change hint", async () => {
    setup();
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    expect(within(bobRow).getByText("Active")).toBeInTheDocument();
    expect(within(bobRow).getByText("Never")).toBeInTheDocument();
    expect(within(bobRow).getByText("Must change password")).toBeInTheDocument();
    const annRow = screen.getByText("ann@example.com").closest("tr")!;
    expect(within(annRow).queryByText("Must change password")).not.toBeInTheDocument();
  });

  it("shows Change password instead of Reset password on your own row", async () => {
    setup();
    const annRow = (await screen.findByText("ann@example.com")).closest("tr")!;
    expect(within(annRow).getByRole("link", { name: "Change password" })).toHaveAttribute(
      "href", "/change-password");
    expect(within(annRow).queryByRole("button", { name: /Reset password/ })).not.toBeInTheDocument();
    const bobRow = screen.getByText("bob@example.com").closest("tr")!;
    expect(within(bobRow).getByRole("button", { name: /Reset password/ })).toBeInTheDocument();
    expect(within(bobRow).queryByRole("link", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("adds a member and shows the temporary password once, with a copy button", async () => {
    const created = { ...bob, id: 3, email: "cy@example.com", name: "Cy" };
    const { f, client } = setup({
      "POST /v1/users": () => json(201, { user: created, temporary_password: "tmp-Secret-123" }),
    });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    await user.type(await screen.findByLabelText("Email"), "cy@example.com");
    await user.type(screen.getByLabelText("Name"), "Cy");
    await user.click(screen.getByRole("button", { name: "Add member" }));

    const dialog = await screen.findByRole("dialog", { name: "Temporary password" });
    expect(within(dialog).getByText("tmp-Secret-123")).toBeInTheDocument();
    const post = f.mock.calls.find(([p, i]) => p === "/v1/users" && i?.method === "POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ email: "cy@example.com", name: "Cy" });

    await user.click(within(dialog).getByRole("button", { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith("tmp-Secret-123");

    expect(cachedText(client)).not.toContain("tmp-Secret-123");

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("tmp-Secret-123")).not.toBeInTheDocument());
    expect(cachedText(client)).not.toContain("tmp-Secret-123");
    expect(f.mock.calls.filter(([p, i]) => p === "/v1/users" && !i?.method).length).toBeGreaterThanOrEqual(2);
  });

  it("shows the 409 as a toast when deactivating the last active member", async () => {
    setup({
      "PATCH /v1/users/2": json(409, { detail: "cannot deactivate the last active member" }),
    });
    const user = userEvent.setup();
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    await user.click(within(bobRow).getByRole("button", { name: /Deactivate/ }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Deactivate" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("cannot deactivate the last active member"));
  });

  it("resets a password only after confirming, and shows it in the same one-time dialog", async () => {
    const { f, client } = setup({
      "PATCH /v1/users/2": () => json(200, { user: bob, temporary_password: "new-tmp-pw-9" }),
    });
    const user = userEvent.setup();
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    await user.click(within(bobRow).getByRole("button", { name: /Reset password/ }));
    const confirm = await screen.findByRole("alertdialog", { name: "Reset Bob's password?" });
    expect(confirm).toHaveTextContent(/signs them out everywhere/i);
    expect(confirm).toHaveTextContent(/replaces their password/i);
    expect(f.mock.calls.filter(([p]) => p === "/v1/users/2")).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog", { name: "Temporary password" });
    expect(within(dialog).getByText("new-tmp-pw-9")).toBeInTheDocument();
    const patch = f.mock.calls.find(([p]) => p === "/v1/users/2")!;
    expect(JSON.parse(patch[1]!.body as string)).toEqual({ reset_password: true });
    expect(cachedText(client)).not.toContain("new-tmp-pw-9");
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("new-tmp-pw-9")).not.toBeInTheDocument());
    expect(cachedText(client)).not.toContain("new-tmp-pw-9");
  });

  it("sends nothing when the reset is cancelled", async () => {
    const { f } = setup({
      "PATCH /v1/users/2": () => json(200, { user: bob, temporary_password: "new-tmp-pw-9" }),
    });
    const user = userEvent.setup();
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    await user.click(within(bobRow).getByRole("button", { name: /Reset password/ }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(f.mock.calls.filter(([p]) => p === "/v1/users/2")).toHaveLength(0);
  });

  it("closes the temporary-password dialog only through Done", async () => {
    setup({
      "PATCH /v1/users/2": () => json(200, { user: bob, temporary_password: "new-tmp-pw-9" }),
    });
    // The modal makes the page behind it ignore the pointer; click there anyway.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    await user.click(within(bobRow).getByRole("button", { name: /Reset password/ }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog", { name: "Temporary password" });
    expect(within(dialog).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    await user.click(document.querySelector<HTMLElement>('[data-slot="dialog-overlay"]')!);
    await user.click(document.body);
    expect(screen.getByRole("dialog", { name: "Temporary password" })).toBeInTheDocument();
    expect(screen.getByText("new-tmp-pw-9")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("new-tmp-pw-9")).not.toBeInTheDocument());
  });

  it("renames a member", async () => {
    const { f } = setup({
      "PATCH /v1/users/2": () => json(200, { user: { ...bob, name: "Robert" }, temporary_password: null }),
    });
    const user = userEvent.setup();
    const bobRow = (await screen.findByText("bob@example.com")).closest("tr")!;
    await user.click(within(bobRow).getByRole("button", { name: /Rename/ }));
    const dialog = await screen.findByRole("dialog", { name: "Rename member" });
    const input = within(dialog).getByLabelText("Name");
    await user.clear(input);
    await user.type(input, "Robert");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const patch = f.mock.calls.find(([p]) => p === "/v1/users/2")!;
      expect(JSON.parse(patch[1]!.body as string)).toEqual({ name: "Robert" });
    });
  });

  it("reactivates without a confirmation", async () => {
    const off = { ...bob, active: false };
    const { f } = setup({
      "GET /v1/users": json(200, [ann, off]),
      "PATCH /v1/users/2": () => json(200, { user: bob, temporary_password: null }),
    });
    const user = userEvent.setup();
    const row = (await screen.findByText("bob@example.com")).closest("tr")!;
    expect(within(row).getByText("Inactive")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: /Reactivate/ }));
    await waitFor(() => {
      const patch = f.mock.calls.find(([p]) => p === "/v1/users/2")!;
      expect(JSON.parse(patch[1]!.body as string)).toEqual({ active: true });
    });
  });
});
