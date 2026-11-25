import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Paragraph from "@/components/review/Paragraph";
import FocusCard from "@/components/review/FocusCard";
import ReviewToolbar from "@/components/review/ReviewToolbar";
import CheckLtMarks from "@/components/review/CheckLtMarks";
import { useAiCheck, useRemember } from "@/hooks/useAiCheck";
import BulkAccept from "@/components/review/BulkAccept";
import NextChapterPrompt from "@/components/review/NextChapterPrompt";
import { Button } from "@/components/ui/button";
import { useChapterReview, useChapterSummary, useOnVisible } from "@/hooks/useChapterReview";
import { useDecide, type Action } from "@/hooks/useDecide";
import { useReviewFocus } from "@/hooks/useReviewFocus";
import { NOT_REVIEW_KEYS, useReviewKeys } from "@/hooks/useReviewKeys";
import { detailOf, type ChapterSummary } from "@/lib/jobs";
import {
  canAccept, canEdit, canReject, defaultChapter, hiddenBy, inView, isDecided, viewItems,
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

/** Move keyboard focus to the focused mark (or its card) unless the person is in a field or menu. */
function focusMark(id: number) {
  const active = document.activeElement;
  if (active?.closest(NOT_REVIEW_KEYS)) return;
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
  // This page session's decisions, newest last, for `u` when nothing decided is in focus
  // or the focus moved on by itself.
  const [history, setHistory] = useState<{ id: number; chapterId: string }[]>([]);
  // After a decision the focus moves on by itself, to a suggestion the person did not pick.
  // Until they move it themselves (j/k, a click, Tab), `u` steps back through their own
  // decisions rather than undoing whatever decided suggestion the focus landed on.
  const [movedOn, setMovedOn] = useState(false);
  // The chapter in which this session last decided something (for the next-chapter prompt).
  const [decidedIn, setDecidedIn] = useState<string | null>(null);
  // Keyboard focus follows j/k, and follows the focus after a decision while the reader is
  // in the text; it is not pulled away from the toolbar when a view or filter changes.
  const followKeys = useRef(false);

  const { query, blocks } = useChapterReview(bookId, chapterId);
  const items = useMemo(() => viewItems(blocks, view, filters), [blocks, view, filters]);
  // Decided marks outside the view stay clickable, to see who decided them and revert.
  const pickable = useMemo(
    () => viewItems(blocks, "all", { category: "", severity: "" }).filter((it) => isDecided(it.s)),
    [blocks],
  );
  const { current: item, focus, pick, next, prev } = useReviewFocus(items, pickable);
  const current = item?.s ?? null;

  const [shownChapter, setShownChapter] = useState(chapterId);
  if (shownChapter !== chapterId) {
    setShownChapter(chapterId);
    focus(null);
    setEditing(null);
    setMovedOn(false);
  }

  const decide = useDecide(bookId, {
    onDecided: (row, vars) => {
      setHistory((h) => {
        const rest = h.filter((x) => x.id !== row.id);
        return vars.action === "undo" ? rest : [...rest, { id: row.id, chapterId: String(row.chapter_id) }].slice(-UNDO_DEPTH);
      });
      setDecidedIn(String(row.chapter_id));
      setMovedOn(true);
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

  const aiCheck = useAiCheck(bookId);
  const remember = useRemember(bookId, {
    onDone: (marks) => {
      // Newest last: `u` undoes the remembered mark first, then the others it rejected.
      const done = [...marks].reverse().map((m) => ({ id: m.id, chapterId: String(m.chapter_id) }));
      const ids = new Set(done.map((x) => x.id));
      setHistory((h) => [...h.filter((x) => !ids.has(x.id)), ...done].slice(-UNDO_DEPTH));
      setDecidedIn(chapterId);
      setMovedOn(true);
    },
  });

  // A failed next page waits for "Try again" rather than being asked for again on its own.
  const canLoadMore = query.hasNextPage && !query.isFetchingNextPage && !query.isFetchNextPageError;

  const run = (id: number, action: Action, replacement?: string) => {
    if (!decide.isPending) decide.mutate({ id, action, replacement });
  };
  const lastHere = history.findLast((h) => h.chapterId === chapterId);
  const undoId = !movedOn && current && isDecided(current) ? current.id : lastHere?.id;
  const canUndo = undoId !== undefined;

  const actions = {
    accept: () => current && canAccept(current) && run(current.id, "accept"),
    reject: () => current && canReject(current) && run(current.id, "reject"),
    edit: () => current && canEdit(current) && setEditing(current.id),
    undo: () => {
      if (undoId !== undefined) run(undoId, "undo");
    },
    next: () => {
      setEditing(null);
      setMovedOn(false);
      if (next()) followKeys.current = true;
      else {
        if (current) focusMark(current.id);
        if (canLoadMore) void query.fetchNextPage();
      }
    },
    prev: () => {
      setEditing(null);
      setMovedOn(false);
      if (prev()) followKeys.current = true;
      else if (current) focusMark(current.id);
    },
    escape: () => setEditing(null),
  };
  useReviewKeys(actions);
  useEffect(() => {
    // Tab moves keyboard focus by hand, so `u` follows the focus again.
    const onTab = (e: KeyboardEvent) => {
      if (e.key === "Tab") setMovedOn(false);
    };
    window.addEventListener("keydown", onTab);
    return () => window.removeEventListener("keydown", onTab);
  }, []);

  const currentId = current?.id;
  useEffect(() => {
    if (currentId !== undefined && (followKeys.current || inText())) focusMark(currentId);
    followKeys.current = false;
  }, [currentId]);

  const isActive = useCallback((s: Suggestion) => inView(s, view, filters) || isDecided(s), [view, filters]);
  const select = useCallback(
    (id: number) => {
      const it = items.find((x) => x.s.id === id);
      const other = it ? undefined : pickable.find((x) => x.s.id === id);
      if (it) focus(it);
      else if (other) pick(other);
      setEditing(null);
      setMovedOn(false);
    },
    [items, pickable, focus, pick],
  );
  const loadMore = useOnVisible(() => void query.fetchNextPage(), canLoadMore);

  const here = chapters.find((c) => String(c.chapter_id) === chapterId);
  const finished = decidedIn === chapterId && here?.open === 0;
  const done = !query.isPending && !query.hasNextPage;

  return (
    <main className="w-full px-4 pb-24 sm:px-6">
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
        bulk={
          <>
            {filters.category && (
              <BulkAccept bookId={bookId} chapterId={chapterId} category={filters.category} onDone={() => setDecidedIn(chapterId)} />
            )}
            {(here?.open ?? 0) > 0 && <CheckLtMarks bookId={bookId} chapterId={chapterId} />}
          </>
        }
      />
      <div className="space-y-3 py-4">
        {finished && <NextChapterPrompt bookId={bookId} chapters={chapters} chapterId={chapterId} />}
        {query.isError && !query.isFetchNextPageError && (
          <p role="alert" className="text-sm text-destructive">{detailOf(query.error)}</p>
        )}
        {done && items.length === 0 && !query.isError && (
          <p role="status" className="text-sm text-muted-foreground">
            {filters.category || filters.severity ? "Nothing in this view matches the filters." : EMPTY[view]}
          </p>
        )}
      </div>
      {query.isPending && <p className="text-sm text-muted-foreground">Loading chapter…</p>}
      <div className="space-y-4">
        {blocks.map((b, i) => (
          // One container per paragraph and its card: the sticky card's limits.
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
                source={b.source}
                hiddenBy={hiddenBy(b.suggestions, current)}
                editing={editing === current.id}
                busy={decide.isPending || remember.isPending}
                canUndo={canUndo}
                reverts={undoId === current.id}
                onAccept={actions.accept}
                onReject={actions.reject}
                onEdit={actions.edit}
                onUndo={actions.undo}
                onSave={(text) => run(current.id, "edit", text)}
                asking={aiCheck.isPending && aiCheck.variables === current.id}
                onAskAi={() => {
                  if (!aiCheck.isPending) aiCheck.mutate(current.id);
                }}
                onUseAiFix={() => {
                  if (current.ai_replacement !== null) run(current.id, "edit", current.ai_replacement);
                }}
                remembering={remember.isPending}
                onRemember={() => {
                  if (!remember.isPending) remember.mutate(current.id);
                }}
                onUseOption={(text) => {
                  if (text === current.replacement) actions.accept();
                  else run(current.id, "edit", text);
                }}
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
      {query.isFetchNextPageError && !query.isFetchingNextPage && (
        <div role="alert" className="flex flex-wrap items-center gap-3 py-4 text-sm text-destructive">
          <span>{`Could not load more paragraphs: ${detailOf(query.error)}`}</span>
          <Button size="sm" variant="outline" onClick={() => void query.fetchNextPage()}>Try again</Button>
        </div>
      )}
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
    return <main className="w-full p-8"><p>This book has no chapters to review.</p></main>;
  }
  return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
}
