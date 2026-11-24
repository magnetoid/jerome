import { Fragment } from "react";
import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { money } from "@/lib/format";
import { STATUS_LABELS, detailOf, isJobActive, jobsKey, passesLabel, type Job } from "@/lib/jobs";

/** The book's jobs, newest first, from the page's `useBookJobs` query. */
export default function JobsList({ jobs }: { jobs: UseQueryResult<Job[]> }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">Jobs</h2>
      {jobs.isError && <p role="alert" className="text-sm text-destructive">{detailOf(jobs.error)}</p>}
      {jobs.data && jobs.data.length === 0 && <p className="text-sm text-muted-foreground">No jobs yet.</p>}
      {jobs.data && jobs.data.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Cost</TableHead>
              <TableHead>AI applied</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.data.map((j) => (
              <Fragment key={j.id}>
                <TableRow>
                  <TableCell>{new Date(j.created_at).toLocaleString()}</TableCell>
                  <TableCell>{passesLabel(j.passes)}</TableCell>
                  <TableCell>{STATUS_LABELS[j.status] ?? j.status}</TableCell>
                  <TableCell>{j.type === "publish" ? "" : `${money(j.cost_actual)} of ${money(j.max_cost)}`}</TableCell>
                  <TableCell>{j.stats.auto_applied ?? ""}</TableCell>
                </TableRow>
                {j.type === "publish" && <PublishDetails job={j} />}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

/**
 * A publish job's file, warnings and, when it failed, why. While it runs it holds the book's
 * one job slot, so it can be cancelled here (a worker restart mid-render leaves it running).
 */
function PublishDetails({ job }: { job: Job }) {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => apiFetch<Job>(`/v1/jobs/${job.id}/cancel`, { method: "POST" }),
    onSettled: () => void client.invalidateQueries({ queryKey: jobsKey(String(job.book_id)) }),
  });
  const active = isJobActive(job);
  const file = job.status === "completed" ? job.stats.file : undefined;
  const warnings = job.stats.warnings ?? [];
  const errors = job.status === "failed" ? job.tasks.map((t) => t.error).filter(Boolean) : [];
  if (!active && !file && warnings.length === 0 && errors.length === 0) return null;
  return (
    <TableRow>
      <TableCell colSpan={5} className="space-y-1 whitespace-normal">
        {active && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">Making the file…</span>
            <Button size="sm" variant="outline" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              Cancel
            </Button>
          </div>
        )}
        {cancel.isError && <p role="alert" className="text-sm text-destructive">{detailOf(cancel.error)}</p>}
        {file && (
          /* A plain GET: the browser sends the session cookie, so no custom header is needed. */
          <a
            className="text-sm underline underline-offset-4"
            href={`/v1/jobs/${job.id}/file`}
            download
            aria-label={`Download ${file}`}
          >
            Download {file}
          </a>
        )}
        {errors.map((e) => (
          <p key={e} className="text-sm text-destructive">{e}</p>
        ))}
        {warnings.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
            {warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        )}
      </TableCell>
    </TableRow>
  );
}
