import { useEffect, useMemo, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import BookCell from "@/components/preview/BookCell";
import PreviewToolbar from "@/components/preview/PreviewToolbar";
import TextCell from "@/components/preview/TextCell";
import { Button } from "@/components/ui/button";
import { useChapterSummary, useOnVisible } from "@/hooks/useChapterReview";
import { detailOf, type ChapterSummary } from "@/lib/jobs";
import {
  STATE_LABELS, TRIM_WIDTHS, normalize, prefsSearch, readPrefs, resolvePrefs, usePreview, writePrefs,
  type PreviewBlock, type PreviewPageData, type PreviewState, type PreviewView, type Prefs,
} from "@/lib/preview";
import Loading from "@/components/Loading";
import { Skeleton } from "@/components/ui/skeleton";

const muted = (text: string) => <p className="text-sm italic text-muted-foreground">{text}</p>;

/** Where a cell sits: the language of its column, and whether it follows a heading. */
export type CellContext = { lang: string | undefined; sourceLang?: string; firstAfterHeading: boolean };

/** The language of a state's column: the source's for a translated book's Original. */
export function langFor(state: PreviewState, page: PreviewPageData | undefined): string | undefined {
  if (!page) return undefined;
  return (state === "original" && page.source_variant) || page.variant || undefined;
}

/** Whether a translation or proofread cell has no finished translation behind it. */
function untranslated(state: PreviewState, block: PreviewBlock): "pending" | "flagged" | null {
  const status = block.translation?.status;
  return state !== "original" && (status === "pending" || status === "flagged") ? status : null;
}
const UNTRANSLATED = { pending: "Not translated yet", flagged: "Flagged: not translated" } as const;

/** One paragraph in one state, drawn for the chosen view. */
export function cellFor(view: PreviewView, state: PreviewState, block: PreviewBlock, prefs: Prefs, ctx?: CellContext): ReactNode {
  const text = block[state];
  const missing = untranslated(state, block);
  // Until a paragraph is translated, neither its translation nor its proofread text exists.
  if (missing === "pending") return muted(UNTRANSLATED.pending);
  if (!text) return null;
  // A flagged paragraph keeps the source text: say so, and set it in the source language.
  const flagged = missing === "flagged";
  const cell =
    view === "book" ? (
      <BookCell block={block} state={state} lang={flagged ? ctx?.sourceLang : ctx?.lang} firstAfterHeading={ctx?.firstAfterHeading ?? false} />
    ) : (
      <TextCell text={text} kind={block.kind} changes={prefs.changes && state === "proofread"} />
    );
  return flagged ? <>{muted(UNTRANSLATED.flagged)}{cell}</> : cell;
}

function ChapterPreview({ bookId, chapterId, chapters }: { bookId: string; chapterId: string; chapters: ChapterSummary[] }) {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const { query, blocks, first } = usePreview(chapterId);
  const translated = first?.work === "translate";
  const prefs = useMemo(() => resolvePrefs(search, readPrefs(), translated), [search, translated]);
  const more = useOnVisible(() => void query.fetchNextPage(), query.hasNextPage && !query.isFetchingNextPage);

  const onPrefs = (next: Prefs) => {
    const p = normalize(next, translated);
    writePrefs(p);
    setSearch(prefsSearch(p), { replace: true });
  };
  const onChapter = (id: string) => navigate({ pathname: `/projects/${bookId}/preview/${id}`, search: search.toString() });

  const columns = { gridTemplateColumns: `repeat(${prefs.states.length}, minmax(0, 1fr))` };
  const book = prefs.view === "book";
  // In the Book view the notes go under the chapter, as in the printed book.
  const rows = book ? blocks.filter((b) => b.kind !== "footnote") : blocks;
  const notes = book ? blocks.filter((b) => b.kind === "footnote") : [];
  const width = book && prefs.layout === "single" && first?.trim_size ? TRIM_WIDTHS[first.trim_size] : undefined;
  return (
    <main className="w-full space-y-4 p-4 sm:p-6">
      <header className="space-y-1">
        <Link to={`/projects/${bookId}`} className="text-sm text-muted-foreground underline-offset-4 hover:underline">
          Back to the project
        </Link>
        <h1 className="font-display text-3xl font-normal tracking-tight">Preview</h1>
      </header>
      <PreviewToolbar
        chapters={chapters}
        chapterId={chapterId}
        onChapter={onChapter}
        prefs={prefs}
        onPrefs={onPrefs}
        translated={translated}
      />
      {query.isError ? (
        <p role="alert" className="text-destructive">{detailOf(query.error)}</p>
      ) : query.isPending ? (
        <Loading className="space-y-4 pt-2">
          {["w-full", "w-11/12", "w-full", "w-4/5", "w-full", "w-2/3"].map((w, i) => (
            <Skeleton key={i} className={`h-4 ${w}`} />
          ))}
        </Loading>
      ) : (
        <div
          role="table"
          aria-label={`Preview of ${first?.title ?? "the chapter"}`}
          className="mx-auto text-sm"
          style={width ? { maxWidth: width } : undefined}
        >
          <div role="row" className="sticky top-0 z-10 grid gap-6 border-b bg-background/95 py-2 backdrop-blur" style={columns}>
            {prefs.states.map((s) => (
              <div key={s} role="columnheader" className="font-medium">
                {STATE_LABELS[s]}
                {s === "proofread" && first && !first.proofread && (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">Not proofread yet</span>
                )}
              </div>
            ))}
          </div>
          {rows.map((b, i) => (
            <div
              key={b.id}
              role="row"
              className={book ? "grid gap-10 py-0.5" : "grid gap-6 border-b border-border/50 py-2"}
              style={columns}
            >
              {prefs.states.map((s) => (
                <div key={s} role="cell" className="min-w-0">
                  {cellFor(prefs.view, s, b, prefs, {
                    lang: langFor(s, first),
                    sourceLang: langFor("original", first),
                    firstAfterHeading: i === 0 || rows[i - 1].kind === "heading",
                  })}
                </div>
              ))}
            </div>
          ))}
          {notes.length > 0 && (
            <div className="mt-6 grid gap-10 border-t pt-4" style={columns}>
              {prefs.states.map((s) => (
                <section key={s} className="book min-w-0" lang={langFor(s, first)}>
                  <h3 id={`notes-${s}`} className="mb-2 text-sm font-semibold">Notes</h3>
                  <ol aria-labelledby={`notes-${s}`} className="book-notes">
                    {notes.map((b) => {
                      const missing = untranslated(s, b);
                      return (
                        <BookCell
                          key={b.id}
                          block={b}
                          state={s}
                          lang={langFor(missing === "flagged" ? "original" : s, first)}
                          firstAfterHeading={false}
                          note={missing ? UNTRANSLATED[missing] : undefined}
                        />
                      );
                    })}
                  </ol>
                </section>
              ))}
            </div>
          )}
          {blocks.length === 0 && muted("This chapter has no paragraphs.")}
          {query.hasNextPage && (
            <div className="py-4 text-center">
              <Button ref={more} variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}

/** The book as it reads now: original, translation and proofread, alone or side by side. */
export default function PreviewPage() {
  const { id: bookId = "", chapterId } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const summary = useChapterSummary(bookId);
  const target = chapterId ? undefined : summary.data?.[0]?.chapter_id;
  useEffect(() => {
    if (target !== undefined) {
      navigate({ pathname: `/projects/${bookId}/preview/${target}`, search: search.toString() }, { replace: true });
    }
  }, [target, bookId, navigate, search]);

  if (chapterId) return <ChapterPreview bookId={bookId} chapterId={chapterId} chapters={summary.data ?? []} />;
  if (summary.isError) {
    return <main className="p-8"><p role="alert" className="text-destructive">{detailOf(summary.error)}</p></main>;
  }
  if (summary.data?.length === 0) return <main className="w-full p-8"><p>This book has no chapters.</p></main>;
  return (
    <main className="w-full p-4 sm:p-8">
      <Loading className="mx-auto max-w-3xl space-y-4">
        <Skeleton className="h-8 w-64 max-w-full" />
        {["w-full", "w-11/12", "w-full", "w-4/5", "w-full", "w-2/3"].map((w, i) => (
          <Skeleton key={i} className={`h-4 ${w}`} />
        ))}
      </Loading>
    </main>
  );
}
