import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { json } from "@/test-utils";
import { callsTo } from "@/components/project/fixtures";
import {
  aiSug, block, firstPage, page, renderReview, reviewServer, sug, summaryRow,
} from "@/components/review/fixtures";

let toastError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  toastError = vi.spyOn(toast, "error").mockImplementation(() => "t");
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const s1 = sug({ id: 1, block_id: 100, start: 2, end: 5, original: "teh", replacement: "the" });
const s2 = sug({ id: 2, block_id: 101, start: 2, end: 5, original: "dgo", replacement: "dog" });
const s4 = sug({
  id: 4, block_id: 102, start: 4, end: 8, original: "said", replacement: "says",
  category: "style", status: "accepted", decided_by: "bob@example.com",
});
const BLOCKS = [
  block(100, 1, "𝔄 teh cat sat.", [s1]),
  block(101, 2, "A dgo barked.", [s2]),
  block(102, 3, "Bob said hi.", [s4]),
];
const SUMMARY = [summaryRow(11, 1, "Arrival", 2), summaryRow(12, 2, "Harbour", 3), summaryRow(13, 3, "Appendix", 0)];

const card = () => screen.getByRole("region", { name: /focused suggestion/i });
const where = () => screen.getByTestId("where").textContent;
const posts = (f: ReturnType<typeof reviewServer>["fetchMock"], path: string) => callsTo(f, "POST", path);

describe("review route (R23)", () => {
  it("replaces a ?chapter= query with the path form", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY, {
      [firstPage(12)]: () => json(200, page([block(200, 1, "Harbour text.")], 1, 0, 12)),
    });
    renderReview("/projects/1/review?chapter=12");
    await waitFor(() => expect(where()).toBe("/projects/1/review/12"));
    expect(await screen.findByText("Harbour text.")).toBeInTheDocument();
    expect(callsTo(fetchMock, "GET", firstPage(11).slice(4))).toHaveLength(0);
  });

  it("without a chapter opens the first chapter with open items", async () => {
    reviewServer(BLOCKS, [summaryRow(11, 1, "Arrival", 0), summaryRow(12, 2, "Harbour", 3)], {
      [firstPage(12)]: () => json(200, page([block(200, 1, "Harbour text.")], 1, 0, 12)),
    });
    renderReview("/projects/1/review");
    await waitFor(() => expect(where()).toBe("/projects/1/review/12"));
  });

  it("without a chapter and nothing open opens the first chapter", async () => {
    reviewServer(BLOCKS, [summaryRow(11, 1, "Arrival", 0), summaryRow(12, 2, "Harbour", 0)]);
    renderReview("/projects/1/review");
    await waitFor(() => expect(where()).toBe("/projects/1/review/11"));
  });

  it("the chapter selector shows each chapter's open count and switches chapter", async () => {
    reviewServer(BLOCKS, SUMMARY, {
      [firstPage(12)]: () => json(200, page([block(200, 1, "Harbour text.")], 1, 0, 12)),
    });
    renderReview();
    await screen.findByText("2 left");
    const select = screen.getByLabelText("Chapter");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "1. Arrival (2 open)", "2. Harbour (3 open)", "3. Appendix (0 open)",
    ]);
    await userEvent.selectOptions(select, "12");
    await waitFor(() => expect(where()).toBe("/projects/1/review/12"));
    expect(await screen.findByText("3 left")).toBeInTheDocument();
  });
});

describe("review keys and focus", () => {
  it("an overlapping suggestion is not marked but is reachable with j/k", async () => {
    const later = sug({ id: 7, block_id: 100, start: 4, end: 9, original: "h cat", replacement: "h dog" });
    reviewServer([block(100, 1, "𝔄 teh cat sat.", [s1, later]), BLOCKS[1]], SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    expect(document.querySelector('[data-sid="7"]')).toBeNull();
    await userEvent.keyboard("jj");
    expect(within(card()).getByText("h dog")).toBeInTheDocument();
    expect(within(card()).getByText(
      "Not marked in the text: it overlaps another open suggestion marked there.",
    )).toBeInTheDocument();
    await userEvent.keyboard("j");
    expect(within(card()).getByText("dgo")).toBeInTheDocument();
    await userEvent.keyboard("k");
    expect(within(card()).getByText("h dog")).toBeInTheDocument();
  });

  it("an open suggestion is marked over an earlier rejected one it overlaps (R29)", async () => {
    const rejected = sug({ id: 5, block_id: 100, start: 2, end: 5, status: "rejected", decided_by: "bob@example.com" });
    const open = sug({ id: 7, block_id: 100, start: 4, end: 9, original: "h cat", replacement: "h dog" });
    reviewServer([block(100, 1, "𝔄 teh cat sat.", [rejected, open]), BLOCKS[1]], SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    const mark = screen.getByRole("button", { name: /^Needs you: “h cat”/ });
    expect(mark).toHaveTextContent(/^h cat$/);
    expect(mark.className).toMatch(/decoration-amber-500/);
    expect(document.querySelector('[data-sid="5"]')).toBeNull();
    await userEvent.keyboard("j");
    expect(within(card()).getByText("h dog")).toBeInTheDocument();
    expect(within(card()).queryByText(/not marked in the text/i)).not.toBeInTheDocument();
    // In All, the rejected one is reachable, and the card says what took its place.
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.keyboard("k");
    expect(within(card()).getByText("Rejected by bob@example.com")).toBeInTheDocument();
    expect(within(card()).getByText(
      "Not marked in the text: it overlaps another open suggestion marked there.",
    )).toBeInTheDocument();
  });

  it("a suggestion hidden by an applied change says so on its card", async () => {
    const rejected = sug({ id: 5, block_id: 100, start: 2, end: 5, status: "rejected", decided_by: "bob@example.com" });
    const accepted = sug({
      id: 7, block_id: 100, start: 4, end: 9, original: "h cat", replacement: "h dog",
      status: "accepted", decided_by: "bob@example.com",
    });
    reviewServer([block(100, 1, "𝔄 teh cat sat.", [rejected, accepted]), BLOCKS[1]], SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.keyboard("j");
    expect(within(card()).getByText("Rejected by bob@example.com")).toBeInTheDocument();
    expect(within(card()).getByText(
      "Not marked in the text: it overlaps a change already applied there.",
    )).toBeInTheDocument();
  });

  it("keyboard focus follows j/k onto the mark, but stays on a view toggle that was clicked", async () => {
    const ai = aiSug({ id: 3, block_id: 101, start: 6, end: 12, original: "barked", replacement: "barks" });
    reviewServer([BLOCKS[0], block(101, 2, "A dgo barked.", [s2, ai])], SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.keyboard("j");
    expect(screen.getByRole("button", { name: /^Needs you: “teh”/ })).toHaveFocus();
    // The focus moves to the AI change (the view's next suggestion), keyboard focus does not.
    await userEvent.click(screen.getByRole("button", { name: "Applied by AI" }));
    expect(within(card()).getByText("barks")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Applied by AI" })).toHaveFocus();
    await userEvent.keyboard("k");
    expect(screen.getByRole("button", { name: /^Applied by AI: “barked”/ })).toHaveFocus();
  });

  it("u undoes a teammate's decision focused in the All view", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.click(screen.getByRole("button", { name: /^Accepted by bob@example.com/ }));
    expect(within(card()).getByText("Accepted by bob@example.com")).toBeInTheDocument();
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/4/undo")).toHaveLength(1));
    expect(await screen.findByText("3 left")).toBeInTheDocument();
  });

  it("u with nothing decided in focus undoes the last decision made on this page", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    await userEvent.keyboard("ja");
    await waitFor(() => expect(within(card()).getByText("dgo")).toBeInTheDocument());
    await userEvent.keyboard("u");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/undo")).toHaveLength(1));
    expect(posts(fetchMock, "/v1/suggestions/2/undo")).toHaveLength(0);
    // The undone suggestion needs a person again and takes the focus back.
    await waitFor(() => expect(within(card()).getByText("teh")).toBeInTheDocument());
    expect(await screen.findByText("2 left")).toBeInTheDocument();
  });

  it("r on a person's accept sends /reject and shows the API's 409 message", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY, {
      "POST /v1/suggestions/4/reject": () => json(409, { detail: "cannot reject a accepted suggestion" }),
    });
    renderReview();
    await screen.findByText("2 left");
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.click(screen.getByRole("button", { name: /^Accepted by bob@example.com/ }));
    await userEvent.keyboard("r");
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("cannot reject a accepted suggestion"));
    expect(posts(fetchMock, "/v1/suggestions/4/reject")).toHaveLength(1);
    expect(posts(fetchMock, "/v1/suggestions/4/undo")).toHaveLength(0);
  });

  it("ignores keys held with ctrl, meta or alt, and keys typed in a select", async () => {
    const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
    renderReview();
    await screen.findByText("2 left");
    screen.getByLabelText("Category").focus();
    await userEvent.keyboard("j");
    expect(screen.queryByRole("region", { name: /focused suggestion/i })).not.toBeInTheDocument();
    (document.activeElement as HTMLElement).blur();

    await userEvent.keyboard("j");
    await userEvent.keyboard("{Control>}a{/Control}{Meta>}a{/Meta}{Alt>}a{/Alt}");
    await userEvent.keyboard("{Control>}j{/Control}");
    expect(within(card()).getByText("teh")).toBeInTheDocument();
    expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(0);
    await userEvent.keyboard("a");
    await waitFor(() => expect(posts(fetchMock, "/v1/suggestions/1/accept")).toHaveLength(1));
  });
});

describe("review keys inside a menu or list (M5)", () => {
  it.each([["menu", "menuitem"], ["listbox", "option"]])(
    "keys pressed in an open %s are not shortcuts and focus stays there",
    async (role, itemRole) => {
      const { fetchMock } = reviewServer(BLOCKS, SUMMARY);
      renderReview();
      await screen.findByText("2 left");
      await userEvent.keyboard("j");
      expect(within(card()).getByText("teh")).toBeInTheDocument();

      const menu = document.body.appendChild(document.createElement("div"));
      menu.setAttribute("role", role);
      const item = menu.appendChild(document.createElement("button"));
      item.setAttribute("role", itemRole);
      item.focus();
      await userEvent.keyboard("jkaru");
      expect(item).toHaveFocus();
      expect(within(card()).getByText("teh")).toBeInTheDocument();
      expect(fetchMock.mock.calls.filter(([, i]) => (i as RequestInit | undefined)?.method === "POST")).toHaveLength(0);
      menu.remove();
    },
  );
});

describe("review paging", () => {
  type Callback = (entries: Array<{ isIntersecting: boolean; target: Element }>) => void;
  const observers: Array<{ callback: Callback; targets: Element[] }> = [];

  beforeEach(() => {
    observers.length = 0;
    vi.stubGlobal("IntersectionObserver", class {
      targets: Element[] = [];
      callback: Callback;
      constructor(callback: Callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(el: Element) { this.targets.push(el); }
      unobserve() {}
      disconnect() { this.targets = []; }
    });
  });

  it("loads the next 50 paragraphs when the last loaded one scrolls into view", async () => {
    const blocks = Array.from({ length: 50 }, (_, i) => block(1000 + i, i + 1, `Plain line ${i + 1}.`));
    const tail = block(1050, 51, "Last teh line.", [sug({ id: 9, block_id: 1050, start: 5, end: 8 })]);
    const { fetchMock } = reviewServer(blocks, [summaryRow(11, 1, "Arrival", 1)], {
      [firstPage(11)]: () => json(200, page(blocks, 51)),
      "GET /v1/chapters/11/review?offset=50&limit=50": () => json(200, page([tail], 51, 50)),
    });
    renderReview();
    expect(await screen.findByText("Plain line 50.")).toBeInTheDocument();
    expect(screen.queryByText(/Last/)).not.toBeInTheDocument();

    const last = document.querySelector('[data-block-id="1049"]')!;
    const watcher = observers.find((o) => o.targets.includes(last));
    expect(watcher).toBeDefined();
    act(() => watcher!.callback([{ isIntersecting: true, target: last }]));

    await waitFor(() => expect(callsTo(fetchMock, "GET", "/v1/chapters/11/review?offset=50&limit=50")).toHaveLength(1));
    expect(await screen.findByRole("button", { name: /^Needs you: “teh”/ })).toBeInTheDocument();
    // Nothing more to load after the last page.
    const end = document.querySelector('[data-block-id="1050"]')!;
    const endWatcher = observers.find((o) => o.targets.includes(end));
    act(() => endWatcher?.callback([{ isIntersecting: true, target: end }]));
    expect(fetchMock.mock.calls.filter(([p]) => String(p).startsWith("/v1/chapters/11/review"))).toHaveLength(2);
  });
});

describe("review paging when the next page fails", () => {
  beforeEach(() => {
    // Like a real IntersectionObserver for an element already in view: observe() reports it
    // at once, and each new observer reports again.
    vi.stubGlobal("IntersectionObserver", class {
      connected = true;
      callback: (entries: Array<{ isIntersecting: boolean; target: Element }>) => void;
      constructor(callback: (entries: Array<{ isIntersecting: boolean; target: Element }>) => void) {
        this.callback = callback;
      }
      observe(el: Element) {
        queueMicrotask(() => this.connected && this.callback([{ isIntersecting: true, target: el }]));
      }
      unobserve() {}
      disconnect() { this.connected = false; }
    });
  });

  it("asks once, then waits for Try again instead of retrying in a loop", async () => {
    const blocks = Array.from({ length: 50 }, (_, i) => block(1000 + i, i + 1, `Plain line ${i + 1}.`));
    const tail = block(1050, 51, "Last line.");
    let fail = true;
    const slow = (res: Response) => new Promise<Response>((r) => setTimeout(() => r(res), 50));
    const { fetchMock } = reviewServer(blocks, [summaryRow(11, 1, "Arrival", 0)], {
      [firstPage(11)]: () => json(200, page(blocks, 51)),
      "GET /v1/chapters/11/review?offset=50&limit=50": () =>
        slow(fail ? json(500, { detail: "boom" }) : json(200, page([tail], 51, 50))),
    });
    const nextPage = () => callsTo(fetchMock, "GET", "/v1/chapters/11/review?offset=50&limit=50").length;
    renderReview();
    await screen.findByText("Plain line 50.");

    const retry = await screen.findByRole("button", { name: "Try again" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await act(() => new Promise((r) => setTimeout(r, 400)));
    expect(nextPage()).toBe(1);

    fail = false;
    await userEvent.click(retry);
    expect(await screen.findByText("Last line.")).toBeInTheDocument();
    expect(nextPage()).toBe(2);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    await act(() => new Promise((r) => setTimeout(r, 200)));
    expect(nextPage()).toBe(2);
  });
});
