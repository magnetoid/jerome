import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { Stage } from "@/components/StageBadge";

export interface ActiveJob {
  id: number | string;
  status: "queued" | "running" | "paused_cost_cap" | string;
  chapters_done: number;
  chapters_total: number;
  cost_actual: string;
  max_cost: string;
}

export interface Project {
  id: number | string;
  title: string;
  format: string;
  /** The book's language: the target language of a translation. */
  variant: string;
  /** Absent from older servers; treated as "proofread". */
  work?: "proofread" | "translate";
  /** The source language of a translation, else null. */
  source_variant?: string | null;
  /** Blocks per translation status ("pending", "done", "flagged", "kept") of a translation. */
  translation?: Record<string, number> | null;
  stage: Stage;
  open_count: number;
  auto_applied_count: number;
  decided_count: number;
  active_job: ActiveJob | null;
  last_activity: { actor: string; at: string; channel?: string | null } | null;
  created_at: string;
}

export const PROJECTS_KEY = ["projects"] as const;
export const POLL_MS = 3000;

export const isProgressing = (p: Project) =>
  p.active_job?.status === "queued" || p.active_job?.status === "running";

export function useProjects() {
  return useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => apiFetch<Project[]>("/v1/projects"),
    refetchInterval: (q) => (q.state.data?.some(isProgressing) ? POLL_MS : false),
  });
}
