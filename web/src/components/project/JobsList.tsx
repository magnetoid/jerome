import type { UseQueryResult } from "@tanstack/react-query";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { money } from "@/lib/format";
import { STATUS_LABELS, detailOf, passesLabel, type Job } from "@/lib/jobs";

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
              <TableRow key={j.id}>
                <TableCell>{new Date(j.created_at).toLocaleString()}</TableCell>
                <TableCell>{passesLabel(j.passes)}</TableCell>
                <TableCell>{STATUS_LABELS[j.status] ?? j.status}</TableCell>
                <TableCell>{money(j.cost_actual)} of {money(j.max_cost)}</TableCell>
                <TableCell>{j.stats.auto_applied ?? ""}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
