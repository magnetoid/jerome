// Shared test fixtures for the review screen tests. Text is short invented sentences.
import { Route, Routes } from "react-router-dom";
import { json, ME, mockApi, renderApp, Where } from "@/test-utils";
import ReviewPage from "@/pages/ReviewPage";
import type { ReviewBlock, ReviewPageData, Suggestion } from "@/lib/review";

export function sug(over: Partial<Suggestion> = {}): Suggestion {
  return {
    id: 1, book_id: 1, block_id: 100, chapter_id: 11, start: 2, end: 5,
    original: "teh", replacement: "the", category: "typo", severity: "high",
    source: "model", explanation: "Misspelling of the.", status: "open", final_text: null,
    job_id: 9, model: "m-1", created_at: "2026-11-02T09:00:00Z", agreed: false,
    disputed: false, alternative: null, rule: null, auto_applied: false, decided_by: null,
    ...over,
  };
}

/** An AI-applied change: accepted, and the AI's decision stands. */
export const aiSug = (over: Partial<Suggestion> = {}) =>
  sug({ status: "accepted", auto_applied: true, decided_by: "ai", ...over });

export function block(
  id: number, ordinal: number, original: string, suggestions: Suggestion[] = [], kind = "paragraph",
): ReviewBlock {
  return { id, ordinal, kind, original, current: original, suggestions };
}

export function page(blocks: ReviewBlock[], total = blocks.length, offset = 0, chapter_id = 11): ReviewPageData {
  return { chapter_id, book_id: 1, total_blocks: total, offset, blocks };
}

export const summaryRow = (chapter_id: number, ordinal: number, title: string, open: number) => ({
  chapter_id, ordinal, title, proofread: true, open, auto_applied: 0, decided: 0,
});

export const firstPage = (chapter: number) => `GET /v1/chapters/${chapter}/review?offset=0&limit=50`;

type Routes = Parameters<typeof mockApi>[0];
type Summary = ReturnType<typeof summaryRow>;

/**
 * A tiny in-memory API for one chapter (11): the review page and summary reflect decisions
 * made through `decide`, so refetches after a decision see the new state.
 */
export function reviewServer(blocks: ReviewBlock[], summary: Summary[], over: Routes = {}) {
  const state = { blocks: structuredClone(blocks), summary: structuredClone(summary) };
  const all = () => state.blocks.flatMap((b) => b.suggestions);
  const find = (id: number) => {
    const s = all().find((x) => x.id === id);
    if (!s) throw new Error(`no suggestion ${id}`);
    return s;
  };
  const recount = () => {
    const row = state.summary.find((c) => c.chapter_id === 11);
    if (row) row.open = all().filter((s) => s.status === "open").length;
  };
  /** Apply a change to a suggestion as the server would, and return the updated row. */
  const set = (id: number, patch: Partial<Suggestion>) => {
    Object.assign(find(id), patch);
    recount();
    return find(id);
  };
  const byMe = (status: Suggestion["status"]) => ({ status, auto_applied: false, decided_by: ME.email });
  const decisionRoutes: Routes = {};
  for (const s of all()) {
    decisionRoutes[`POST /v1/suggestions/${s.id}/accept`] = () => json(200, set(s.id, byMe("accepted")));
    decisionRoutes[`POST /v1/suggestions/${s.id}/reject`] = () => json(200, set(s.id, byMe("rejected")));
    decisionRoutes[`POST /v1/suggestions/${s.id}/undo`] = () =>
      json(200, set(s.id, { ...byMe("open"), final_text: null }));
    decisionRoutes[`POST /v1/suggestions/${s.id}/edit`] = (init) =>
      json(200, set(s.id, { ...byMe("edited"), final_text: JSON.parse(init.body as string).replacement }));
  }
  const fetchMock = mockApi({
    "GET /v1/books/1/chapter-summary": () => json(200, state.summary),
    [firstPage(11)]: () => json(200, page(state.blocks)),
    ...decisionRoutes,
    ...over,
  });
  return { fetchMock, state, set, find };
}

export function renderReview(entry = "/projects/1/review/11") {
  return renderApp(
    <>
      <Routes>
        <Route path="/projects/:id/review/:chapterId?" element={<ReviewPage />} />
        <Route path="/projects/:id" element={<div>project page</div>} />
      </Routes>
      <Where />
    </>,
    entry,
  );
}
