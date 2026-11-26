import { ApiError } from "@/lib/api";

export type Passes = string[];

export const FULL_PASSES: Passes = ["languagetool", "ai", "consistency"];
export const FREE_PASSES: Passes = ["languagetool"];

const FINISHED: Record<string, string> = { "epub-book": "Finished EPUB", "pdf-book": "Finished PDF" };

export function passesLabel(passes: string[]): string {
  if (passes.includes("translate")) return "Translation";
  if (FINISHED[passes[0]]) return FINISHED[passes[0]];
  return passes.includes("ai") ? "AI + LanguageTool" : "Free check";
}

/** "Translation", "Proofreading" or "Finished EPUB"/"Finished PDF", for copy that names what a job does. */
export function jobNoun(j: { type: string; passes?: string[] }): string {
  if (j.type === "translate") return "Translation";
  if (j.type === "import") return "PDF import";
  if (j.type === "publish") return FINISHED[j.passes?.[0] ?? ""] ?? "Finished file";
  return "Proofreading";
}

/** The newest job that is part of the review (proofreading or translation), not a finished file. */
export const latestReviewJob = (jobs: Job[] | undefined) =>
  jobs?.find((j) => j.type !== "publish" && j.type !== "import");

/** The newest PDF import job of a book. */
export const latestImportJob = (jobs: Job[] | undefined) => jobs?.find((j) => j.type === "import");

/** GET /v1/books/{id}'s `import_report`, once a PDF import has run. */
export interface ImportReport {
  pages: number;
  text_pages: number;
  ocr_pages: number;
  engine_pages: { ai: number; tesseract: number };
  image_pages: number;
  blank_pages: number;
  /** 1-based page numbers. */
  failed_pages: number[];
  /** Why the AI did not read a page Tesseract read instead, with a count per reason. */
  ai_fallbacks: Record<string, number>;
}

/** GET /v1/books/{id}/translation. */
export interface TranslationState {
  counts: Partial<Record<"pending" | "done" | "flagged" | "kept", number>>;
  flagged: { block_id: number; chapter_id: number; source: string }[];
  /** Chapters whose title could not be translated; present only when there are any. */
  untranslated_titles?: number[];
}

/** The block a Translate again or Keep original returns. */
export interface TranslatedBlock {
  id: number;
  chapter_id: number;
  text: string;
  translation_status: "pending" | "done" | "flagged" | "kept" | null;
}

export interface JobTask {
  id: number;
  kind: "chapter" | "consistency" | "publish";
  chapter_id: number | null;
  status: "queued" | "running" | "done" | "failed";
  chunks_done: number;
  /** Null for the consistency check and a publish task, which are not split into chunks. */
  chunks_total: number | null;
  attempts: number;
  error: string | null;
}

export interface Job {
  id: number;
  /** "proofread", "translate", "publish" or "import". */
  book_id: number;
  type: string;
  status: "queued" | "running" | "paused_cost_cap" | "cancelled" | "failed" | "completed";
  passes: string[];
  chapter_ids: number[];
  max_cost: string;
  cost_actual: string;
  /** A publish job's `file` (its name, once done) and `warnings`. */
  /** An import job's `pages`, `pages_done` and `awaiting_estimate`, and its `estimate`. */
  estimate?: { ocr_pages?: number; cost?: string } | null;
  stats: {
    auto_applied?: number; file?: string; warnings?: string[];
    pages?: number; pages_done?: number; awaiting_estimate?: boolean;
  } & Record<string, unknown>;
  created_at: string;
  finished_at: string | null;
  tasks: JobTask[];
}

export interface Chapter {
  id: number;
  ordinal: number;
  title: string;
  proofread: boolean;
  source_ref: string;
}

export interface ChapterSummary {
  chapter_id: number;
  ordinal: number;
  title: string;
  proofread: boolean;
  open: number;
  auto_applied: number;
  decided: number;
}

export const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  paused_cost_cap: "Paused at cost limit",
  cancelled: "Cancelled",
  failed: "Failed",
  completed: "Completed",
};

export const isJobActive = (j: { status: string } | undefined) =>
  j?.status === "queued" || j?.status === "running";

export const bookKey = (id: string) => ["book", id] as const;
export const chaptersKey = (id: string) => ["chapters", id] as const;
export const summaryKey = (id: string) => ["chapter-summary", id] as const;
export const jobsKey = (id: string) => ["jobs", id] as const;
export const jobKey = (id: number | string) => ["job", String(id)] as const;
export const translationKey = (id: string) => ["translation", id] as const;

export function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : "Something went wrong. Try again.";
}
