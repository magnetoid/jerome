import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { detailOf } from "@/lib/jobs";
import { reviewKey, type ReviewPageData, type Suggestion } from "@/lib/review";
import { patchRow, useRefreshReview } from "@/hooks/useDecide";

export interface ChapterAiCheck {
  checked: number;
  remaining: number;
  failed: number;
  suggestions: Suggestion[];
}

/** Ask the AI about one LanguageTool mark; its verdict is put on the card at once. */
export function useAiCheck(bookId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiFetch<Suggestion>(`/v1/suggestions/${id}/ai-check`, { method: "POST" }),
    onSuccess: (row) => {
      client.setQueriesData<InfiniteData<ReviewPageData>>({ queryKey: reviewKey(bookId) }, (d) => patchRow(d, row));
    },
    onError: (err) => toast.error(detailOf(err)),
  });
}

/** What a chapter check did, in one line. */
export function chapterCheckText({ checked, remaining, failed }: ChapterAiCheck): string {
  if (checked + failed + remaining === 0) return "No LanguageTool marks left to check";
  const total = checked + failed + remaining;
  const head = remaining > 0 ? `Checked ${checked} of ${total}` : `Checked ${checked} LanguageTool mark${checked === 1 ? "" : "s"}`;
  const fail = failed > 0 ? `; ${failed} could not be checked` : "";
  const more = remaining > 0 ? "; click again for the rest" : "";
  return `${head}${fail}${more}`;
}

/** Ask the AI about the chapter's unchecked LanguageTool marks (up to 40 per click). */
export function useChapterAiCheck(bookId: string, chapterId: string) {
  const refresh = useRefreshReview(bookId);
  return useMutation({
    mutationFn: () =>
      apiFetch<ChapterAiCheck>(`/v1/books/${bookId}/chapters/${chapterId}/ai-check`, { method: "POST" }),
    onSuccess: (r) => toast.success(chapterCheckText(r)),
    onError: (err) => toast.error(detailOf(err)),
    onSettled: () => refresh(),
  });
}
