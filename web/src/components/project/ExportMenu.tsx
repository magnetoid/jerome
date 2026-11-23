const ALL = [
  { format: "docx-clean", label: "Word clean" },
  { format: "docx-tracked", label: "Word with tracked changes" },
  { format: "docx-review", label: "Word review copy" },
  { format: "epub-clean", label: "EPUB clean" },
];

const ITEM = "inline-flex h-9 items-center rounded-md border px-3 text-sm";

interface Props {
  bookId: string;
  format: string;
  /**
   * Paragraphs of a translated book still pending or flagged. The server refuses the export
   * until there are none, so the menu is disabled and says why.
   */
  untranslated?: number;
}

export default function ExportMenu({ bookId, format, untranslated = 0 }: Props) {
  const allowed = format === "epub" ? ALL.filter((e) => e.format === "epub-clean") : ALL;
  const waiting = untranslated > 0;
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Export</h2>
      <ul className="flex flex-wrap gap-2">
        {allowed.map((e) => (
          <li key={e.format}>
            {waiting ? (
              <span
                aria-disabled="true"
                aria-describedby="export-waiting"
                className={`${ITEM} cursor-not-allowed text-muted-foreground opacity-60`}
              >
                {e.label}
              </span>
            ) : (
              /* A plain GET: the browser sends the session cookie, so no custom header is needed. */
              <a
                className={`${ITEM} hover:bg-accent`}
                href={`/v1/books/${bookId}/export?format=${e.format}`}
                download
              >
                {e.label}
              </a>
            )}
          </li>
        ))}
      </ul>
      {waiting && (
        <p id="export-waiting" className="text-sm text-muted-foreground">
          {`Export waits for ${untranslated} untranslated ${untranslated === 1 ? "paragraph" : "paragraphs"}.`}
        </p>
      )}
    </section>
  );
}
