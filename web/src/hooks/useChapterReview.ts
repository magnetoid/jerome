import { useCallback, useEffect, useMemo, useRef } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { summaryKey, type ChapterSummary } from "@/lib/jobs";
import { PAGE_SIZE, reviewKey, type ReviewPageData } from "@/lib/review";

export function useChapterSummary(bookId: string) {
  return useQuery({
    queryKey: summaryKey(bookId),
    queryFn: () => apiFetch<ChapterSummary[]>(`/v1/books/${bookId}/chapter-summary`),
  });
}

/** A chapter's paragraphs with their suggestions, PAGE_SIZE paragraphs per page. */
export function useChapterReview(bookId: string, chapterId: string) {
  const query = useInfiniteQuery({
    queryKey: reviewKey(bookId, chapterId),
    queryFn: ({ pageParam }) =>
      apiFetch<ReviewPageData>(
        `/v1/chapters/${chapterId}/review?offset=${pageParam}&limit=${PAGE_SIZE}`,
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.blocks.length;
      return last.blocks.length > 0 && next < last.total_blocks ? next : undefined;
    },
  });
  const blocks = useMemo(() => query.data?.pages.flatMap((p) => p.blocks) ?? [], [query.data]);
  return { query, blocks };
}

/**
 * A callback ref that calls `onVisible` when its element scrolls into view, while `enabled`.
 * Without IntersectionObserver (old browsers) nothing loads on scroll.
 */
export function useOnVisible(onVisible: () => void, enabled: boolean) {
  const latest = useRef(onVisible);
  useEffect(() => {
    latest.current = onVisible;
  });
  return useCallback(
    (el: Element | null) => {
      if (!el || !enabled || typeof IntersectionObserver === "undefined") return;
      const observer = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) latest.current();
      });
      observer.observe(el);
      return () => observer.disconnect();
    },
    [enabled],
  );
}
