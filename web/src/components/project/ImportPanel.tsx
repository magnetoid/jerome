import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { POLL_MS, PROJECTS_KEY } from "@/hooks/useProjects";
import { money } from "@/lib/format";
import {
  bookKey, detailOf, jobKey, jobsKey, latestImportJob, type ImportReport, type Job,
} from "@/lib/jobs";

interface ImportBook {
  import_status?: "importing" | "failed" | null;
  import_error?: string | null;
  import_report?: ImportReport | null;
}

const pagesOf = (n: number) => `${n} ${n === 1 ? "page" : "pages"}`;

/** Which engine read the scanned pages, and how many the AI failed on; empty if none were OCR'd. */
function engineLines(report: ImportReport): string[] {
  const ai = report.engine_pages?.ai ?? 0;
  const tesseract = report.engine_pages?.tesseract ?? 0;
  const lines: string[] = [];
  if (ai + tesseract > 0) {
    lines.push(`Read by AI: ${pagesOf(ai)} · Tesseract: ${pagesOf(tesseract)}`);
  }
  const { cost_cap: capped = 0, ...failed } = report.ai_fallbacks ?? {};
  const fallbacks = Object.values(failed).reduce((a, b) => a + b, 0);
  if (fallbacks > 0) {
    lines.push(`Tesseract read ${pagesOf(fallbacks)} because the AI failed on them.`);
  }
  if (capped > 0) {
    lines.push(`Tesseract read ${pagesOf(capped)} after the maximum cost was reached.`);
  }
  return lines;
}

/**
 * A PDF import's state on the project page: its progress, the OCR estimate to confirm, a failure
 * with Retry, and, after it, which pages could not be read. Renders nothing for any other book.
 */
export default function ImportPanel({ bookId }: { bookId: string }) {
  const client = useQueryClient();
  const book = useQuery({
    queryKey: bookKey(bookId),
    queryFn: () => apiFetch<ImportBook>(`/v1/books/${bookId}`),
    refetchInterval: (q) => (q.state.data?.import_status === "importing" ? POLL_MS : false),
  });
  const status = book.data?.import_status ?? null;
  const jobs = useQuery({
    queryKey: jobsKey(bookId),
    queryFn: () => apiFetch<Job[]>(`/v1/books/${bookId}/jobs`),
    enabled: status !== null,
    refetchInterval: status === "importing" ? POLL_MS : false,
  });
  const job = latestImportJob(jobs.data);

  // The projects list (stage, active job) changes when an import ends; it does not poll then.
  const was = useRef(status);
  useEffect(() => {
    if (was.current === status) return;
    was.current = status;
    void client.invalidateQueries({ queryKey: PROJECTS_KEY });
    void client.invalidateQueries({ queryKey: jobsKey(bookId) });
  }, [status, client, bookId]);

  const [typed, setTyped] = useState<string | null>(null);
  const estimate = job?.stats.awaiting_estimate ? job.estimate : null;
  // confirmed, yet paused at the cap (an older import): raise the cap or let Tesseract read on
  const capped = !estimate && job?.status === "paused_cost_cap";
  const maxCost = typed ?? (capped ? job?.max_cost : estimate?.cost) ?? "";

  function changed(j: Job) {
    client.setQueryData(jobKey(j.id), j);
    void client.invalidateQueries({ queryKey: bookKey(bookId) });
    void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    void client.invalidateQueries({ queryKey: PROJECTS_KEY });
  }
  const confirm = useMutation({
    mutationFn: () =>
      apiFetch<Job>(`/v1/books/${bookId}/import/confirm`, {
        method: "POST", json: { max_cost: Number(maxCost) },
      }),
    onSuccess: changed,
  });
  const tesseract = useMutation({
    mutationFn: () => apiFetch<Job>(`/v1/books/${bookId}/import/use-tesseract`, { method: "POST" }),
    onSuccess: changed,
  });
  const retry = useMutation({
    mutationFn: () => apiFetch<Job>(`/v1/jobs/${job!.id}/resume`, { method: "POST", json: {} }),
    onSuccess: changed,
  });

  const report = book.data?.import_report ?? null;
  const failedPages = report?.failed_pages ?? [];
  const error = [confirm, tesseract, retry].find((m) => m.isError)?.error;

  if (status === "importing") {
    return (
      <div className="space-y-4">
        {estimate || capped ? (
          <div className="space-y-3 rounded-lg border bg-card p-4 shadow-xs">
            {estimate ? (
              <p>
                Estimate ready: {estimate.ocr_pages} pages need AI OCR, about {money(estimate.cost ?? "0")}
              </p>
            ) : (
              <p>Paused at the cost limit</p>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label htmlFor="import-max-cost">Max cost (dollars)</Label>
                <Input
                  id="import-max-cost"
                  type="number"
                  min="0"
                  step="0.01"
                  className="w-32"
                  value={maxCost}
                  onChange={(e) => setTyped(e.target.value)}
                />
              </div>
              <Button
                disabled={!(Number(maxCost) > 0) || confirm.isPending || tesseract.isPending}
                onClick={() => confirm.mutate()}
              >
                Confirm
              </Button>
              <Button
                variant="outline"
                disabled={confirm.isPending || tesseract.isPending}
                onClick={() => tesseract.mutate()}
              >
                Use Tesseract instead
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {job?.stats.pages
              ? `Importing… page ${job.stats.pages_done ?? 0} of ${job.stats.pages}`
              : "Importing…"}
          </p>
        )}
        {error && <p role="alert" className="text-sm text-destructive">{detailOf(error)}</p>}
      </div>
    );
  }

  if (status === "failed") {
    return (
      <div className="space-y-3">
        <p role="alert" className="text-destructive">Import failed: {book.data?.import_error ?? "unknown error"}</p>
        <Button disabled={!job || retry.isPending} onClick={() => retry.mutate()}>Retry</Button>
        {error && <p role="alert" className="text-sm text-destructive">{detailOf(error)}</p>}
      </div>
    );
  }

  if (!report) return null;
  const lines = engineLines(report);
  const blank = report.blank_pages ?? 0;
  const pictures = report.image_pages ?? 0;
  if (blank + pictures > 0) {
    const n = (count: number, one: string) => `${count} ${one}${count === 1 ? "" : "s"}`;
    lines.push(
      [blank && n(blank, "blank page"), pictures && n(pictures, "picture page")].filter(Boolean).join(", "),
    );
  }
  if (failedPages.length === 0 && lines.length === 0 && !report.no_text) return null;
  return (
    <div className="space-y-1 text-sm">
      {report.no_text && (
        <p role="alert" className="text-amber-700 dark:text-amber-400">No text was found in this PDF</p>
      )}
      {failedPages.length > 0 && (
        <p role="status" className="text-amber-700 dark:text-amber-400">
          {failedPages.length === 1
            ? `1 page could not be read: ${failedPages[0]}`
            : `${failedPages.length} pages could not be read: ${failedPages.join(", ")}`}
        </p>
      )}
      {lines.map((l) => <p key={l} className="text-muted-foreground">{l}</p>)}
    </div>
  );
}
