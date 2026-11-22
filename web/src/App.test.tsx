import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("applies the stored theme on the login page, outside the router", async () => {
    localStorage.setItem("jerome-theme", "dark");
    window.history.pushState({}, "", "/login");
    render(<App />);
    expect(await screen.findByRole("button", { name: /sign in|log in/i })).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
  });
});
