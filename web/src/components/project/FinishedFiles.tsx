import { Button } from "@/components/ui/button";
import { detailOf } from "@/lib/jobs";
import { usePublication, usePublish, type PublishFormat } from "@/lib/publication";

const FORMATS: { format: PublishFormat; label: string }[] = [
  { format: "epub-book", label: "Make EPUB" },
  { format: "pdf-book", label: "Make PDF" },
];

interface Props {
  bookId: string;
  /** Paragraphs of a translated book still pending or flagged: the server refuses until none. */
  untranslated?: number;
}

/**
 * Start the finished EPUB or PDF. Each runs as a job; the jobs list shows its warnings and,
 * once it is done, the download.
 */
export default function FinishedFiles({ bookId, untranslated = 0 }: Props) {
  const publication = usePublication(bookId);
  const publish = usePublish(bookId);
  const noAuthor = publication.data !== undefined && publication.data.authors.length === 0;
  const waiting = untranslated > 0;
  const blocked = !publication.data || noAuthor || waiting;

  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Finished files</h2>
      <p className="text-sm text-muted-foreground">
        An EPUB and a PDF for ebook shops, made from the reviewed text with the details above.
      </p>
      <div className="flex flex-wrap gap-2">
        {FORMATS.map((f) => (
          <Button
            key={f.format}
            variant="outline"
            disabled={blocked || publish.isPending}
            aria-describedby={blocked ? "finished-why" : undefined}
            onClick={() => publish.mutate(f.format)}
          >
            {f.label}
          </Button>
        ))}
      </div>
      {noAuthor && (
        <p id="finished-why" className="text-sm text-muted-foreground">Set the author first, under Publication.</p>
      )}
      {!noAuthor && waiting && (
        <p id="finished-why" className="text-sm text-muted-foreground">
          {`Publishing waits for ${untranslated} untranslated ${untranslated === 1 ? "paragraph" : "paragraphs"}.`}
        </p>
      )}
      {publish.isSuccess && !publish.isPending && (
        <p className="text-sm text-muted-foreground">Started. The file appears under Jobs when it is ready.</p>
      )}
      {publish.isError && <p role="alert" className="text-sm text-destructive">{detailOf(publish.error)}</p>}
    </section>
  );
}
