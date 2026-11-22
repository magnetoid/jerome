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
 * A callback ref that calls `onVisible` while its element is in view and `enabled` is true.
 * One observer stays attached per element; `enabled` is read from a ref, so turning it off
 * and on (a fetch starting, failing) never re-creates the observer, whose first report on
 * a visible element would otherwise ask again at once. When `enabled` comes back on while
 * the element is still in view, it asks once. Without IntersectionObserver nothing loads
 * on scroll.
 */
export function useOnVisible(onVisible: () => void, enabled: boolean) {
  const latest = useRef(onVisible);
  const on = useRef(enabled);
  const visible = useRef(false);
  useEffect(() => {
    latest.current = onVisible;
    on.current = enabled;
  });
  useEffect(() => {
    if (enabled && visible.current) latest.current();
  }, [enabled]);
  return useCallback((el: Element | null) => {
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      visible.current = entries.some((e) => e.isIntersecting);
      if (visible.current && on.current) latest.current();
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      visible.current = false;
    };
  }, []);
}
