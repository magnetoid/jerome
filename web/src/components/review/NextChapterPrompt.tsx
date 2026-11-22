import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import type { ChapterSummary } from "@/lib/jobs";
import { nextChapter } from "@/lib/review";

interface Props {
  bookId: string;
  chapters: ChapterSummary[];
  chapterId: string;
}

/** Shown once this session's decisions leave nothing open in the chapter. */
export default function NextChapterPrompt({ bookId, chapters, chapterId }: Props) {
  const next = nextChapter(chapters, chapterId);
  return (
    <div role="status" className="space-y-2 rounded-lg border border-green-600/40 bg-green-50 p-4 dark:bg-green-950/30">
      {next ? (
        <>
          <p className="font-medium">Chapter done: nothing here needs you any more.</p>
          <Button asChild size="sm">
            <Link to={`/projects/${bookId}/review/${next.chapter_id}`}>
              {`Next: ${next.ordinal}. ${next.title} (${next.open} left)`}
            </Link>
          </Button>
        </>
      ) : (
        <>
          <p className="font-medium">Nothing left to review in this book.</p>
          <Button asChild size="sm" variant="outline">
            <Link to={`/projects/${bookId}`}>Back to project</Link>
          </Button>
        </>
      )}
    </div>
  );
}
