import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, apiFetchPage } from "@/lib/api";
import { detailOf } from "@/lib/jobs";
import { PROJECTS_KEY } from "@/hooks/useProjects";

export const ACTIVITY_PAGE_SIZE = 50;
export const ACTIVITY_KEY = ["activity"] as const;

export type Who = "people" | "agent" | "automatic" | "api";
export const WHO_LABELS: Record<Who, string> = {
  people: "People",
  agent: "Agent",
  automatic: "Automatic",
  api: "API keys",
};

export interface ActivityItem {
  kind: "decision" | "agent";
  id: number;
  at: string;
  actor: string;
  channel: string;
  book_id: number | null;
  book_title: string | null;
  chapter_id: number | null;
  chapter_title: string | null;
  summary: string;
  action: string | null;
  suggestion_id: number | null;
  original: string | null;
  replacement: string | null;
  tool: string | null;
  job_id: number | null;
  before: unknown;
  after: unknown;
  revertable: boolean;
  revert_blocked: string | null;
}

/** Who did it, in words. */
export function whoLabel(item: Pick<ActivityItem, "actor" | "channel">): string {
  switch (item.channel) {
    case "mcp":
      return `Janus (${item.actor})`;
    case "auto":
      return "Automatic";
    case "word":
      return `${item.actor} (Word)`;
    case "api":
      return `${item.actor} (API)`;
    default:
      return item.actor;
  }
}

export interface ActivityFilters {
  book?: string;
  chapter?: string;
  who?: Who;
}

/** One page (0-based) of everything done, newest first. */
export function useActivity(filters: ActivityFilters, page: number) {
  const params = new URLSearchParams();
  if (filters.book) params.set("book_id", filters.book);
  if (filters.chapter) params.set("chapter_id", filters.chapter);
  if (filters.who) params.set("who", filters.who);
  params.set("limit", String(ACTIVITY_PAGE_SIZE));
  params.set("offset", String(page * ACTIVITY_PAGE_SIZE));
  const path = `/v1/activity?${params}`;
  return useQuery({
    queryKey: [...ACTIVITY_KEY, path],
    queryFn: () => apiFetchPage<ActivityItem[]>(path),
    placeholderData: keepPreviousData,
  });
}

/** Revert one correction; the activity, review pages and counts refetch. */
export function useRevert() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (decisionId: number) =>
      apiFetch(`/v1/activity/decisions/${decisionId}/revert`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Reverted");
      for (const queryKey of [ACTIVITY_KEY, ["review"], ["chapter-summary"], ["open-count"], PROJECTS_KEY]) {
        void client.invalidateQueries({ queryKey });
      }
    },
    onError: (e) => toast.error(detailOf(e)),
    onSettled: () => void client.invalidateQueries({ queryKey: ACTIVITY_KEY }),
  });
}
