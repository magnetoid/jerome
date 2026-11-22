import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { PROJECTS_KEY } from "@/hooks/useProjects";
import { detailOf, summaryKey } from "@/lib/jobs";

interface Result {
  review_import_id: number;
  counts: Record<string, number>;
  unmatched_block_ids: number[];
  conflicts: number[];
  notes: string[];
}

export default function ReviewUpload({ bookId }: { bookId: string }) {
  const client = useQueryClient();
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiFetch<Result>(`/v1/books/${bookId}/review-imports`, { method: "POST", body: form });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: summaryKey(bookId) });
    },
  });
  const r = upload.data;
  const input = useRef<HTMLInputElement>(null);
  const [rejected, setRejected] = useState<string | null>(null);

  function take(f: File | undefined) {
    if (!f) return;
    if (!/\.docx$/i.test(f.name)) {
      setRejected("Choose a .docx file.");
      return;
    }
    setRejected(null);
    upload.mutate(f);
  }

  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Upload reviewed Word file</h2>
      <p className="text-sm text-muted-foreground">
        Upload the review copy after your editor's changes and decisions.
      </p>
      <Button type="button" variant="outline" disabled={upload.isPending} onClick={() => input.current?.click()}>
        Choose reviewed Word file
      </Button>
      <input
        ref={input}
        type="file"
        accept=".docx"
        aria-label="Reviewed Word file"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {rejected && <p role="alert" className="text-sm text-destructive">{rejected}</p>}
      {upload.isPending && <p className="text-sm">Reading your file…</p>}
      {upload.isError && <p role="alert" className="text-sm text-destructive">{detailOf(upload.error)}</p>}
      {r && (
        <div className="space-y-2 rounded-md border p-3 text-sm" aria-live="polite">
          <p className="font-medium">Review file imported</p>
          <dl className="grid max-w-xs grid-cols-2 gap-x-4">
            {Object.entries(r.counts).map(([k, n]) => (
              <div key={k} className="contents">
                <dt className="capitalize">{k.replace(/_/g, " ")}</dt>
                <dd>{n}</dd>
              </div>
            ))}
          </dl>
          {r.conflicts.length > 0 && (
            <p>{r.conflicts.length} {r.conflicts.length === 1 ? "conflict needs" : "conflicts need"} your attention in review.</p>
          )}
          {r.unmatched_block_ids.length > 0 && (
            <p>{r.unmatched_block_ids.length} {r.unmatched_block_ids.length === 1 ? "paragraph" : "paragraphs"} could not be matched.</p>
          )}
          {r.notes.length > 0 && (
            <ul className="list-disc pl-5">{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}
        </div>
      )}
    </section>
  );
}
