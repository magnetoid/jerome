import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Paragraph from "@/components/review/Paragraph";
import FocusCard from "@/components/review/FocusCard";
import ReviewToolbar from "@/components/review/ReviewToolbar";
import BulkAccept from "@/components/review/BulkAccept";
import NextChapterPrompt from "@/components/review/NextChapterPrompt";
import { useChapterReview, useChapterSummary, useOnVisible } from "@/hooks/useChapterReview";
import { useDecide, type Action } from "@/hooks/useDecide";
import { useReviewFocus } from "@/hooks/useReviewFocus";
import { useReviewKeys } from "@/hooks/useReviewKeys";
import { detailOf, type ChapterSummary } from "@/lib/jobs";
import {
  canAccept, canEdit, canReject, defaultChapter, inView, isDecided, layoutMarks, viewItems,
  type Filters, type Suggestion, type View,
} from "@/lib/review";

const MemoParagraph = memo(Paragraph);
/** How many of this session's decisions `u` can step back through. */
const UNDO_DEPTH = 50;

const EMPTY: Record<View, string> = {
  needs: "Nothing in this chapter needs you.",
  ai: "The AI applied no changes in this chapter.",
  all: "No suggestions in this chapter.",
};

/** Whether keyboard focus is in the text: on a mark, in the card, or nowhere in particular. */
function inText(): boolean {
  const active = document.activeElement;
  return !active || active === document.body || active.hasAttribute("data-sid") || active.closest("[data-focus-card]") !== null;
}

/** Move keyboard focus to the focused mark (or its card) unless the person is in a field. */
function focusMark(id: number) {
  const active = document.activeElement;
  if (active?.closest("input, textarea, select, [role=dialog], [role=alertdialog]")) return;
  const el =
    document.querySelector<HTMLElement>(`button[data-sid="${id}"]`) ??
    document.querySelector<HTMLElement>("[data-focus-card]");
  el?.focus({ preventScroll: true });
  el?.scrollIntoView?.({ block: "center" });
}

function ChapterReview({ bookId, chapterId, chapters }: {
  bookId: string; chapterId: string; chapters: ChapterSummary[];
}) {
  const navigate = useNavigate();
  const [view, setView] = useState<View>("needs");
  const [filters, setFilters] = useState<Filters>({ category: "", severity: "" });
  const [editing, setEditing] = useState<number | null>(null);
  // This page session's decisions, newest last, for `u` with nothing decided in focus.
  const [history, setHistory] = useState<{ id: number; chapterId: string }[]>([]);
  // The chapter in which this session last decided something (for the next-chapter prompt).
  const [decidedIn, setDecidedIn] = useState<string | null>(null);
  // Keyboard focus follows j/k, and follows the focus after a decision while the reader is
  // in the text; it is not pulled away from the toolbar when a view or filter changes.
  const followKeys = useRef(false);

  const { query, blocks } = useChapterReview(bookId, chapterId);
  const items = useMemo(() => viewItems(blocks, view, filters), [blocks, view, filters]);
  const { current: item, focus, next, prev } = useReviewFocus(items);
  const current = item?.s ?? null;

  const [shownChapter, setShownChapter] = useState(chapterId);
  if (shownChapter !== chapterId) {
    setShownChapter(chapterId);
    focus(null);
    setEditing(null);
  }

  const decide = useDecide(bookId, {
    onDecided: (row, vars) => {
      setHistory((h) => {
        const rest = h.filter((x) => x.id !== row.id);
        return vars.action === "undo" ? rest : [...rest, { id: row.id, chapterId: String(row.chapter_id) }].slice(-UNDO_DEPTH);
      });
      setDecidedIn(String(row.chapter_id));
      if (vars.action === "edit") setEditing(null);
      if (vars.action === "undo") {
        const ordinal = blocks.find((b) => b.id === row.block_id)?.ordinal;
        if (ordinal !== undefined) focus({ s: row, ordinal });
      }
    },
    onConflict: (vars) => {
      setEditing(null);
      if (vars.action === "undo") setHistory((h) => h.filter((x) => x.id !== vars.id));
    },
  });

  const run = (id: number, action: Action, replacement?: string) => {
    if (!decide.isPending) decide.mutate({ id, action, replacement });
  };
  const lastHere = history.findLast((h) => h.chapterId === chapterId);
  const canUndo = (current !== null && isDecided(current)) || lastHere !== undefined;

  const actions = {
    accept: () => current && canAccept(current) && run(current.id, "accept"),
    reject: () => current && canReject(current) && run(current.id, "reject"),
    edit: () => current && canEdit(current) && setEditing(current.id),
    undo: () => {
      if (current && isDecided(current)) run(current.id, "undo");
      else if (lastHere) run(lastHere.id, "undo");
    },
    next: () => {
      setEditing(null);
      if (next()) followKeys.current = true;
      else {
        if (current) focusMark(current.id);
        if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
      }
    },
    prev: () => {
      setEditing(null);
      if (prev()) followKeys.current = true;
      else if (current) focusMark(current.id);
    },
    escape: () => setEditing(null),
  };
  useReviewKeys(actions);

  const currentId = current?.id;
  useEffect(() => {
    if (currentId !== undefined && (followKeys.current || inText())) focusMark(currentId);
    followKeys.current = false;
  }, [currentId]);

  const isActive = useCallback((s: Suggestion) => inView(s, view, filters), [view, filters]);
  const select = useCallback(
    (id: number) => {
      const it = items.find((x) => x.s.id === id);
      if (it) focus(it);
      setEditing(null);
    },
    [items, focus],
  );
  const loadMore = useOnVisible(() => void query.fetchNextPage(), query.hasNextPage && !query.isFetchingNextPage);

  const here = chapters.find((c) => String(c.chapter_id) === chapterId);
  const finished = decidedIn === chapterId && here?.open === 0;
  const done = !query.isPending && !query.hasNextPage;

  return (
    <main className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
      <ReviewToolbar
        bookId={bookId}
        chapters={chapters}
        chapterId={chapterId}
        onChapter={(id) => navigate(`/projects/${bookId}/review/${id}`)}
        view={view}
        onView={setView}
        filters={filters}
        onFilters={setFilters}
        left={here?.open}
      />
      <div className="space-y-3 py-4">
        {filters.category && (
          <BulkAccept bookId={bookId} chapterId={chapterId} category={filters.category} onDone={() => setDecidedIn(chapterId)} />
        )}
        {finished && <NextChapterPrompt bookId={bookId} chapters={chapters} chapterId={chapterId} />}
        {query.isError && <p role="alert" className="text-sm text-destructive">{detailOf(query.error)}</p>}
        {done && items.length === 0 && !query.isError && (
          <p role="status" className="text-sm text-muted-foreground">
            {filters.category || filters.severity ? "Nothing in this view matches the filters." : EMPTY[view]}
          </p>
        )}
      </div>
      {query.isPending && <p className="text-sm text-muted-foreground">Loading chapter…</p>}
      <div className="space-y-4">
        {blocks.map((b, i) => (
          <div key={b.id} data-block-id={b.id} ref={i === blocks.length - 1 ? loadMore : undefined}>
            <MemoParagraph
              block={b}
              isActive={isActive}
              focusedId={current?.block_id === b.id ? current.id : null}
              onSelect={select}
            />
            {current?.block_id === b.id && (
              <FocusCard
                s={current}
                inline={layoutMarks(b.suggestions).some((m) => m.id === current.id)}
                editing={editing === current.id}
                busy={decide.isPending}
                canUndo={canUndo}
                onAccept={actions.accept}
                onReject={actions.reject}
                onEdit={actions.edit}
                onUndo={actions.undo}
                onSave={(text) => run(current.id, "edit", text)}
                onCancelEdit={() => {
                  setEditing(null);
                  focusMark(current.id);
                }}
              />
            )}
          </div>
        ))}
      </div>
      {query.isFetchingNextPage && <p className="py-4 text-sm text-muted-foreground">Loading more…</p>}
    </main>
  );
}

/**
 * /projects/:id/review/:chapterId?. A ?chapter= query (the project page's links) becomes
 * the path form; without either, the first chapter with open items opens.
 */
export default function ReviewPage() {
  const { id: bookId = "", chapterId } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const summary = useChapterSummary(bookId);

  const fromQuery = search.get("chapter");
  const fallback = summary.data ? defaultChapter(summary.data) : undefined;
  const target = chapterId ? undefined : (fromQuery ?? (fallback === undefined ? undefined : String(fallback)));
  useEffect(() => {
    if (target) navigate(`/projects/${bookId}/review/${target}`, { replace: true });
  }, [target, bookId, navigate]);

  if (chapterId) return <ChapterReview bookId={bookId} chapterId={chapterId} chapters={summary.data ?? []} />;
  if (summary.isError) {
    return <main className="p-8"><p role="alert" className="text-destructive">{detailOf(summary.error)}</p></main>;
  }
  if (summary.data?.length === 0) {
    return <main className="mx-auto max-w-3xl p-8"><p>This book has no chapters to review.</p></main>;
  }
  return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
}
