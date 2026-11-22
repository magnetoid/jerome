const ALL = [
  { format: "docx-clean", label: "Word clean" },
  { format: "docx-tracked", label: "Word with tracked changes" },
  { format: "docx-review", label: "Word review copy" },
  { format: "epub-clean", label: "EPUB clean" },
];

export default function ExportMenu({ bookId, format }: { bookId: string; format: string }) {
  const allowed = format === "epub" ? ALL.filter((e) => e.format === "epub-clean") : ALL;
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Export</h2>
      <ul className="flex flex-wrap gap-2">
        {allowed.map((e) => (
          <li key={e.format}>
            {/* A plain GET: the browser sends the session cookie, so no custom header is needed. */}
            <a
              className="inline-flex h-9 items-center rounded-md border px-3 text-sm hover:bg-accent"
              href={`/v1/books/${bookId}/export?format=${e.format}`}
              download
            >
              {e.label}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
