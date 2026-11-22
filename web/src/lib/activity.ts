import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiFetchPage } from "@/lib/api";

export const ACTIVITY_PAGE_SIZE = 50;

export interface AgentAction {
  id: number;
  at: string;
  actor: string;
  channel: string;
  tool: string;
  book_id: number | null;
  chapter_id: number | null;
  job_id: number | null;
  summary: string;
  before: unknown;
  after: unknown;
}

/** One page (0-based) of what Janus changed, newest first, optionally for one book. */
export function useAgentActions(bookId: string | undefined, page: number) {
  const params = new URLSearchParams();
  if (bookId) params.set("book_id", bookId);
  params.set("limit", String(ACTIVITY_PAGE_SIZE));
  params.set("offset", String(page * ACTIVITY_PAGE_SIZE));
  const path = `/v1/agent-actions?${params}`;
  return useQuery({
    queryKey: ["agent-actions", path],
    queryFn: () => apiFetchPage<AgentAction[]>(path),
    placeholderData: keepPreviousData,
  });
}
