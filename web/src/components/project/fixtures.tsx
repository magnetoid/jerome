// Shared test fixtures for the project page tests.
import { Route, Routes } from "react-router-dom";
import { json, mockApi, renderApp, Where } from "@/test-utils";
import ProjectPage from "@/pages/ProjectPage";

export type P = Record<string, unknown>;

export const CHAPTERS = [
  { id: 11, ordinal: 1, title: "Arrival", proofread: true, source_ref: "a.xhtml" },
  { id: 12, ordinal: 2, title: "Harbour", proofread: true, source_ref: "b.xhtml" },
  { id: 13, ordinal: 3, title: "Appendix", proofread: false, source_ref: "c.xhtml" },
];

export const estimate = (high = "3.2") => ({
  chunks: 10,
  per_pass: { ai: { low: "1.0", high } },
  total: { low: "1.0", high },
  chapter_chunks: {},
});

export const BOOK = {
  id: 1, title: "Sea Book", format: "docx", local_only: false,
  original_filename: "sea.docx", created_at: "2026-11-01T10:00:00Z",
};

export const SUMMARY = [
  { chapter_id: 11, ordinal: 1, title: "Arrival", proofread: true, open: 20, auto_applied: 100, decided: 4 },
  { chapter_id: 12, ordinal: 2, title: "Harbour", proofread: true, open: 18, auto_applied: 114, decided: 8 },
];

export function project(over: P = {}): P {
  return {
    id: 1, title: "Sea Book", format: "docx", variant: "en-GB", stage: "set_up",
    open_count: 0, auto_applied_count: 0, decided_count: 0, active_job: null,
    last_activity: null, created_at: "2026-11-01T10:00:00Z", ...over,
  };
}

export const activeJob = (over: P = {}) => ({
  id: 9, status: "running", chapters_done: 1, chapters_total: 2,
  cost_actual: "0.40", max_cost: "4", ...over,
});

export function job(over: P = {}): P {
  return {
    id: 9, book_id: 1, type: "proofread", status: "running",
    passes: ["languagetool", "ai", "consistency"], chapter_ids: [11, 12],
    max_cost: "4", cost_actual: "0.40", estimate: {}, stats: {},
    created_at: "2026-11-02T09:00:00Z", finished_at: null,
    tasks: [
      { id: 1, kind: "chapter", chapter_id: 11, status: "done", chunks_done: 5, chunks_total: 5, attempts: 1, error: null },
      { id: 2, kind: "chapter", chapter_id: 12, status: "running", chunks_done: 2, chunks_total: 6, attempts: 1, error: null },
    ],
    ...over,
  };
}

/** A translate + proofread project: book 1 is the German translation of hidden book 2. */
export const TRANSLATED_BOOK = { ...BOOK, title: "Seebuch", work: "translate", source_book_id: 2, translated_title: "Seebuch" };
export const SOURCE_BOOK = { ...BOOK, id: 2, title: "Sea Book", work: "proofread", source_book_id: null, translated_title: null };

export function translateProject(over: P = {}): P {
  return project({
    title: "Seebuch", work: "translate", source_variant: "en-GB", variant: "de-DE",
    translation: { pending: 40 }, ...over,
  });
}

export const TRANSLATION = {
  counts: { done: 37, flagged: 2, kept: 1 },
  flagged: [
    { block_id: 501, chapter_id: 11, source: "The ship came in at dawn." },
    { block_id: 502, chapter_id: 12, source: "Gulls circled the harbour wall." },
  ],
};

export const STYLE = { variant: "en-GB", quote_style: "‘’“”", house_spellings: {}, ignore_words: [] };
export const AUTO = { enabled: true, categories: ["typo", "punctuation"], min_severity: "high", house_rules: true };

/** Every read the page makes, with sensible defaults; override per test. */
export function pageApi(over: Record<string, Parameters<typeof mockApi>[0][string]> = {}, proj: P = project()) {
  return mockApi({
    "GET /v1/projects": () => json(200, [proj]),
    "GET /v1/books/1": () => json(200, BOOK),
    "GET /v1/books/1/chapters": () => json(200, CHAPTERS),
    "GET /v1/books/1/chapter-summary": () => json(200, SUMMARY),
    "GET /v1/books/1/jobs": () => json(200, []),
    "GET /v1/books/1/style-sheet": () => json(200, STYLE),
    "GET /v1/books/1/auto-apply": () => json(200, AUTO),
    "POST /v1/books/1/estimate": () => json(200, estimate()),
    ...over,
  });
}

export function renderPage(entry = "/projects/1") {
  return renderApp(
    <>
      <Routes>
        <Route path="/projects/:id" element={<ProjectPage />} />
        <Route path="/" element={<div>home</div>} />
      </Routes>
      <Where />
    </>,
    entry,
  );
}

export const callsTo = (f: ReturnType<typeof mockApi>, method: string, path: string) =>
  f.mock.calls.filter(([p, i]) => p === path && ((i as RequestInit | undefined)?.method ?? "GET") === method);

export const bodyOf = (call: unknown[]) => JSON.parse(((call[1] as RequestInit).body as string) ?? "null");
