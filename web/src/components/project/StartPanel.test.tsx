import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StartPanel from "./StartPanel";
import { json, renderApp } from "@/test-utils";
import { bodyOf, callsTo, CHAPTERS, estimate, job, pageApi } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

const FULL = ["languagetool", "ai", "consistency"];
const limit = () => screen.getByLabelText(/cost limit/i) as HTMLInputElement;

async function ready() {
  await screen.findByRole("checkbox", { name: /Arrival/ });
  await waitFor(() => expect(limit().value).not.toBe(""));
}

describe("StartPanel", () => {
  it("ticks the proofread chapters and sends them with the full passes", async () => {
    const f = pageApi();
    renderApp(<StartPanel bookId="1" />);
    await ready();
    expect(screen.getByRole("checkbox", { name: /Arrival/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Appendix/ })).not.toBeChecked();
    const body = bodyOf(callsTo(f, "POST", "/v1/books/1/estimate")[0]);
    expect(body).toEqual({ passes: FULL, chapters: [11, 12] });
  });

  it.each([["3.2", "4"], ["0", "1"], ["2", "2"]])("defaults the limit for a high estimate of %s to %s", async (high, want) => {
    pageApi({ "POST /v1/books/1/estimate": () => json(200, estimate(high)) });
    renderApp(<StartPanel bookId="1" />);
    await screen.findByRole("checkbox", { name: /Arrival/ });
    await waitFor(() => expect(limit().value).toBe(want));
  });

  it("re-fetches with LanguageTool only for the free check", async () => {
    const f = pageApi();
    renderApp(<StartPanel bookId="1" />);
    await ready();
    await userEvent.click(screen.getByRole("radio", { name: /Free check/ }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/estimate")).toHaveLength(2));
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/estimate")[1])).toEqual({
      passes: ["languagetool"], chapters: [11, 12],
    });
  });

  it("ticking an unmarked chapter marks it proofread first; unticking never PATCHes", async () => {
    const f = pageApi({
      "PATCH /v1/chapters/13": () => json(200, { ...CHAPTERS[2], proofread: true }),
    });
    renderApp(<StartPanel bookId="1" />);
    await ready();
    await userEvent.click(screen.getByRole("checkbox", { name: /Appendix/ }));
    await waitFor(() => expect(callsTo(f, "PATCH", "/v1/chapters/13")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "PATCH", "/v1/chapters/13")[0])).toEqual({ proofread: true });
    await userEvent.click(screen.getByRole("checkbox", { name: /Arrival/ }));
    await waitFor(() => {
      const calls = callsTo(f, "POST", "/v1/books/1/estimate");
      expect(bodyOf(calls[calls.length - 1])).toEqual({ passes: FULL, chapters: [12, 13] });
    });
    expect(callsTo(f, "PATCH", "/v1/chapters/11")).toHaveLength(0);
    expect(callsTo(f, "PATCH", "/v1/chapters/13")).toHaveLength(1);
  });

  it("starts a job with the ticked chapters, passes and the edited limit", async () => {
    const f = pageApi({ "POST /v1/books/1/jobs": () => json(201, job()) });
    const { client } = renderApp(<StartPanel bookId="1" />);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await ready();
    await userEvent.clear(limit());
    await userEvent.type(limit(), "7.5");
    await userEvent.click(screen.getByRole("button", { name: /start proofreading/i }));
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/jobs")).toHaveLength(1));
    expect(bodyOf(callsTo(f, "POST", "/v1/books/1/jobs")[0])).toEqual({
      type: "proofread", passes: FULL, chapters: [11, 12], max_cost: 7.5,
    });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects"] }));
  });

  it("disables Start when nothing is ticked or the limit is not above zero", async () => {
    pageApi();
    renderApp(<StartPanel bookId="1" />);
    await ready();
    const start = screen.getByRole("button", { name: /start proofreading/i });
    await userEvent.clear(limit());
    await userEvent.type(limit(), "0");
    expect(start).toBeDisabled();
    await userEvent.clear(limit());
    await userEvent.type(limit(), "2");
    expect(start).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Arrival/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Harbour/ }));
    expect(start).toBeDisabled();
  });

  it("shows the API detail when a job is already active", async () => {
    pageApi({ "POST /v1/books/1/jobs": () => json(409, { detail: "a job is already active" }) });
    renderApp(<StartPanel bookId="1" />);
    await ready();
    await userEvent.click(screen.getByRole("button", { name: /start proofreading/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a job is already active");
  });
});
