import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useNavigate } from "react-router-dom";
import AppShell from "./AppShell";
import { ME, MEMBER, Where, json, mockApi, renderApp } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

function setup(me: typeof ME | typeof MEMBER = ME) {
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
  it("shows the sidebar with links and the page", async () => {
    setup();
    expect(await screen.findByText("home page")).toBeInTheDocument();
    expect(screen.getByText("Jerome")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Team" })).toHaveAttribute("href", "/team");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "New project" })).toHaveAttribute("href", "/projects/new");
  });

  it("hides the Team link from members", async () => {
    setup(MEMBER);
    expect(await screen.findByText("home page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Team" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("shows the Activity link to every member", async () => {
    const { unmount } = setup();
    expect(await screen.findByRole("link", { name: "Activity" })).toHaveAttribute("href", "/activity");
    unmount();
    setup(MEMBER);
    expect(await screen.findByRole("link", { name: "Activity" })).toHaveAttribute("href", "/activity");
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

describe("AppShell theme switch", () => {
  it("cross-fades through a view transition where the browser has one", async () => {
    const start = vi.fn((update: () => void) => {
      update();
      return {};
    });
    Object.defineProperty(document, "startViewTransition", { value: start, configurable: true });
    try {
      setup();
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: /Ann/ }));
      await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
      expect(start).toHaveBeenCalledTimes(1);
      expect(document.documentElement).toHaveClass("dark");
    } finally {
      Reflect.deleteProperty(document, "startViewTransition");
    }
  });

  it("switches at once for a person who asked for less motion", async () => {
    const start = vi.fn();
    Object.defineProperty(document, "startViewTransition", { value: start, configurable: true });
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }));
    try {
      setup();
      const user = userEvent.setup();
      await user.click(await screen.findByRole("button", { name: /Ann/ }));
      await user.click(await screen.findByRole("menuitemradio", { name: "Light" }));
      expect(start).not.toHaveBeenCalled();
      expect(localStorage.getItem("jerome-theme")).toBe("light");
    } finally {
      Reflect.deleteProperty(document, "startViewTransition");
    }
  });
});

describe("AppShell look", () => {
  function Go() {
    const navigate = useNavigate();
    return (
      <>
        {["/projects/7/review/3", "/projects/7/review/4", "/activity"].map((to) => (
          <button key={to} onClick={() => navigate(to)}>{`go ${to}`}</button>
        ))}
      </>
    );
  }

  function setupRoutes(entry: string) {
    mockApi({ "GET /v1/auth/me": json(200, ME) });
    return renderApp(
      <>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<p>home page</p>} />
            <Route path="/projects/:id/review/:chapterId?" element={<p>review page</p>} />
            <Route path="/activity" element={<p>activity page</p>} />
          </Route>
        </Routes>
        <Go />
      </>,
      entry,
    );
  }

  it("shows the logo in the sidebar, under the same link name", async () => {
    const { container } = setup();
    const home = await screen.findByRole("link", { name: "Jerome" });
    expect(home).toHaveAttribute("href", "/");
    expect(home).toHaveAttribute("title", "Jerome");
    expect(home.querySelector("[data-logo-mark]")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelectorAll("[data-logo-mark]")).toHaveLength(1);
  });

  it("marks the active navigation link and no other", async () => {
    setupRoutes("/activity");
    const activity = await screen.findByRole("link", { name: "Activity" });
    expect(activity).toHaveAttribute("data-active", "true");
    expect(activity).toHaveAttribute("aria-current", "page");
    for (const name of ["Projects", "Team", "Settings", "Feedback"]) {
      const link = screen.getByRole("link", { name });
      expect(link).not.toHaveAttribute("data-active");
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("marks Projects only on the projects list", async () => {
    setupRoutes("/projects/7/review/3");
    expect(await screen.findByText("review page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).not.toHaveAttribute("data-active");
  });

  it("plays the page-enter animation on a new page without remounting anything", async () => {
    const reads = vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(0);
    try {
      const { container } = setupRoutes("/projects/7/review/3");
      const user = userEvent.setup();
      expect(await screen.findByText("review page")).toBeInTheDocument();
      const page = container.querySelector("[data-page]")!;
      const content = screen.getByText("review page");
      expect(page).toHaveClass("page-enter");
      expect(page).toHaveAttribute("data-route-key", "/projects/7/review");
      reads.mockClear();

      // Another chapter of the same book: the same page, no replay, nothing remounted.
      await user.click(screen.getByRole("button", { name: "go /projects/7/review/4" }));
      expect(container.querySelector("[data-page]")).toBe(page);
      expect(screen.getByText("review page")).toBe(content);
      expect(page).toHaveAttribute("data-route-key", "/projects/7/review");
      expect(reads).not.toHaveBeenCalled();

      // Another page: the animation restarts on the same wrapper.
      await user.click(screen.getByRole("button", { name: "go /activity" }));
      expect(await screen.findByText("activity page")).toBeInTheDocument();
      expect(container.querySelector("[data-page]")).toBe(page);
      expect(page).toHaveAttribute("data-route-key", "/activity");
      expect(page).toHaveClass("page-enter");
      expect(reads).toHaveBeenCalledTimes(1);
    } finally {
      reads.mockRestore();
    }
  });
});
