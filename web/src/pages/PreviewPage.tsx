import { useEffect, useMemo, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PreviewToolbar from "@/components/preview/PreviewToolbar";
import TextCell from "@/components/preview/TextCell";
import { Button } from "@/components/ui/button";
import { useChapterSummary, useOnVisible } from "@/hooks/useChapterReview";
import { detailOf, type ChapterSummary } from "@/lib/jobs";
import {
  STATE_LABELS, normalize, prefsSearch, readPrefs, resolvePrefs, usePreview, writePrefs,
  type PreviewBlock, type PreviewState, type PreviewView, type Prefs,
} from "@/lib/preview";

const muted = (text: string) => <p className="text-sm italic text-muted-foreground">{text}</p>;

/** One paragraph in one state, drawn for the chosen view. */
export function cellFor(view: PreviewView, state: PreviewState, block: PreviewBlock, prefs: Prefs): ReactNode {
  const text = block[state];
  if (state === "translation" && text?.status === "pending") return muted("Not translated yet");
  if (!text) return null;
  void view; // the Book view is drawn the same until it has its own cell
  return <TextCell text={text} kind={block.kind} changes={prefs.changes && state === "proofread"} />;
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
  return (
    <main className="w-full space-y-4 p-4 sm:p-6">
      <header className="space-y-1">
        <Link to={`/projects/${bookId}`} className="text-sm text-muted-foreground underline-offset-4 hover:underline">
          Back to the project
        </Link>
        <h1 className="text-2xl font-semibold">Preview</h1>
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
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <div role="table" aria-label={`Preview of ${first?.title ?? "the chapter"}`} className="text-sm">
          <div role="row" className="sticky top-[4.75rem] z-10 grid gap-6 border-b bg-background py-2" style={columns}>
            {prefs.states.map((s) => (
              <div key={s} role="columnheader" className="font-medium">
                {STATE_LABELS[s]}
                {s === "proofread" && first && !first.proofread && (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">Not proofread yet</span>
                )}
              </div>
            ))}
          </div>
          {blocks.map((b) => (
            <div key={b.id} role="row" className="grid gap-6 border-b border-border/50 py-2" style={columns}>
              {prefs.states.map((s) => (
                <div key={s} role="cell" className="min-w-0">{cellFor(prefs.view, s, b, prefs)}</div>
              ))}
            </div>
          ))}
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
  return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
}
