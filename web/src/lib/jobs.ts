import { ApiError } from "@/lib/api";

export type Passes = string[];

export const FULL_PASSES: Passes = ["languagetool", "ai", "consistency"];
export const FREE_PASSES: Passes = ["languagetool"];

export function passesLabel(passes: string[]): string {
  if (passes.includes("translate")) return "Translation";
  return passes.includes("ai") ? "AI + LanguageTool" : "Free check";
}

/** "Translation" or "Proofreading", for copy that names what a job does. */
export const jobNoun = (j: { type: string }) => (j.type === "translate" ? "Translation" : "Proofreading");

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
  kind: "chapter" | "consistency";
  chapter_id: number | null;
  status: "queued" | "running" | "done" | "failed";
  chunks_done: number;
  /** Null for the consistency check, which is not split into chunks. */
  chunks_total: number | null;
  attempts: number;
  error: string | null;
}

export interface Job {
  id: number;
  /** "proofread" or "translate". */
  book_id: number;
  type: string;
  status: "queued" | "running" | "paused_cost_cap" | "cancelled" | "failed" | "completed";
  passes: string[];
  chapter_ids: number[];
  max_cost: string;
  cost_actual: string;
  stats: { auto_applied?: number } & Record<string, unknown>;
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
