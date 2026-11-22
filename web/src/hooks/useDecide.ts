import { useCallback } from "react";
import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, ApiError } from "@/lib/api";
import { detailOf, summaryKey } from "@/lib/jobs";
import { PROJECTS_KEY } from "@/hooks/useProjects";
import {
  openCountKey, reviewKey, spansOverlap, type ReviewPageData, type Suggestion,
} from "@/lib/review";

export type Action = "accept" | "reject" | "edit" | "undo";
export interface DecideVars {
  id: number;
  action: Action;
  replacement?: string;
}

/** Refetch what a decision changes: the book's review pages, counts and project stage. */
export function useRefreshReview(bookId: string) {
  const client = useQueryClient();
  return useCallback(() => {
    for (const queryKey of [reviewKey(bookId), summaryKey(bookId), openCountKey(bookId), PROJECTS_KEY]) {
      void client.invalidateQueries({ queryKey });
    }
  }, [client, bookId]);
}

/**
 * The block's suggestions with a decided row in place. An accept or edit also supersedes
 * the open suggestions it overlaps, as the API does, so focus does not land on one of them
 * before the refetch; what an undo or reversal brings back arrives with the refetch.
 */
export function withDecision(suggestions: Suggestion[], row: Suggestion): Suggestion[] {
  const inText = row.status === "accepted" || row.status === "edited";
  return suggestions.map((s) => {
    if (s.id === row.id) return row;
    if (inText && s.status === "open" && spansOverlap(s, row)) return { ...s, status: "superseded" as const };
    return s;
  });
}

function patch(data: InfiniteData<ReviewPageData> | undefined, row: Suggestion) {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((p) => ({
      ...p,
      blocks: p.blocks.map((b) =>
        b.id === row.block_id ? { ...b, suggestions: withDecision(b.suggestions, row) } : b,
      ),
    })),
  };
}

interface Handlers {
  onDecided?: (row: Suggestion, vars: DecideVars) => void;
  onConflict?: (vars: DecideVars) => void;
}

/**
 * Accept, reject, edit or undo one suggestion. Errors (a teammate deciding first, overlapping
 * text, a bad edit) show the API's message as a toast; the refetch then clears stale marks.
 */
export function useDecide(bookId: string, { onDecided, onConflict }: Handlers = {}) {
  const client = useQueryClient();
  const refresh = useRefreshReview(bookId);
  return useMutation({
    mutationFn: ({ id, action, replacement }: DecideVars) =>
      apiFetch<Suggestion>(`/v1/suggestions/${id}/${action}`, {
        method: "POST",
        json: action === "edit" ? { replacement } : undefined,
      }),
    onSuccess: (row, vars) => {
      client.setQueriesData<InfiniteData<ReviewPageData>>({ queryKey: reviewKey(bookId) }, (d) => patch(d, row));
      onDecided?.(row, vars);
    },
    onError: (err, vars) => {
      toast.error(detailOf(err));
      if (err instanceof ApiError && err.status === 409) onConflict?.(vars);
    },
    onSettled: () => refresh(),
  });
}
