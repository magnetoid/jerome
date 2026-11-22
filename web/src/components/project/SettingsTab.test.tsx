import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import SettingsTab from "./SettingsTab";
import { json, renderApp } from "@/test-utils";
import { AUTO, bodyOf, callsTo, pageApi, STYLE } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("SettingsTab", () => {
  it("round-trips auto-apply through PUT", async () => {
    const success = vi.spyOn(toast, "success").mockImplementation(() => "t");
    const f = pageApi({
      "PUT /v1/books/1/auto-apply": (init) => json(200, JSON.parse(init.body as string)),
    });
    renderApp(<SettingsTab bookId="1" />);
    const enabled = await screen.findByRole("checkbox", { name: /apply clear-cut fixes/i });
    await waitFor(() => expect(enabled).toBeChecked());
    expect(screen.getByRole("checkbox", { name: /typo/i })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /grammar/i })).not.toBeChecked();
    expect(screen.getByLabelText(/minimum severity/i)).toHaveValue("high");

    await userEvent.click(screen.getByRole("checkbox", { name: /grammar/i }));
    await userEvent.selectOptions(screen.getByLabelText(/minimum severity/i), "medium");
    await userEvent.click(screen.getByRole("checkbox", { name: /house style rules/i }));
    await userEvent.click(screen.getByRole("button", { name: /save auto-apply/i }));

    await waitFor(() => expect(callsTo(f, "PUT", "/v1/books/1/auto-apply")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "PUT", "/v1/books/1/auto-apply")[0])).toEqual({
      enabled: true, categories: ["grammar", "punctuation", "typo"], min_severity: "medium", house_rules: false,
    });
    await waitFor(() => expect(success).toHaveBeenCalled());
    expect(screen.getByText(/changes take effect from the next run/i)).toBeInTheDocument();
  });

  it("saves the house style and shows the API detail on errors", async () => {
    const f = pageApi({
      "GET /v1/books/1/style-sheet": () => json(200, { ...STYLE, house_spellings: { colour: ["color"] }, ignore_words: ["Aberdeen"] }),
      "PUT /v1/books/1/style-sheet": () => json(422, { detail: "bad quote style" }),
    });
    renderApp(<SettingsTab bookId="1" />);
    await userEvent.click(await screen.findByRole("button", { name: /^house style$/i }));
    expect(await screen.findByLabelText(/house spellings/i)).toHaveValue("colour: color");
    await userEvent.click(screen.getByRole("button", { name: /save house style/i }));
    expect(await screen.findByText("bad quote style")).toBeInTheDocument();
    expect(bodyOf(callsTo(f, "PUT", "/v1/books/1/style-sheet")[0])).toEqual({
      quote_style: "‘’“”", house_spellings: { colour: ["color"] }, ignore_words: ["Aberdeen"],
    });
    void AUTO;
  });
});
