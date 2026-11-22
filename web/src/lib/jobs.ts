import { ApiError } from "@/lib/api";

export type Passes = string[];

export const FULL_PASSES: Passes = ["languagetool", "ai", "consistency"];
export const FREE_PASSES: Passes = ["languagetool"];

export function passesLabel(passes: string[]): string {
  return passes.includes("ai") ? "AI + LanguageTool" : "Free check";
}

export interface JobTask {
  id: number;
  kind: "chapter" | "consistency";
  chapter_id: number | null;
  status: "queued" | "running" | "done" | "failed";
  chunks_done: number;
  chunks_total: number;
  attempts: number;
  error: string | null;
}

export interface Job {
  id: number;
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

export function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : "Something went wrong. Try again.";
}
