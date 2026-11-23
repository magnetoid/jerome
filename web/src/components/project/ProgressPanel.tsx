import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { POLL_MS, PROJECTS_KEY } from "@/hooks/useProjects";
import { money } from "@/lib/format";
import {
  chaptersKey, detailOf, isJobActive, jobKey, jobNoun, jobsKey, summaryKey, type Chapter, type Job,
  type JobTask,
} from "@/lib/jobs";

/** A task's state in words, for one without a chunk count (the consistency check). */
const TASK_STATUS: Record<JobTask["status"], string> = {
  queued: "Waiting",
  running: "Running",
  done: "Done",
  failed: "Failed",
};

function taskProgress(t: JobTask): { text: string; pct: number } {
  if (t.chunks_total === null) {
    return { text: TASK_STATUS[t.status] ?? t.status, pct: t.status === "done" ? 100 : 0 };
  }
  return {
    text: `${t.status === "queued" ? "Waiting " : ""}${t.chunks_done}/${t.chunks_total}`,
    pct: t.chunks_total > 0 ? (t.chunks_done / t.chunks_total) * 100 : 0,
  };
}

export default function ProgressPanel({ bookId, jobId }: { bookId: string; jobId: number | string }) {
  const client = useQueryClient();
  const job = useQuery({
    queryKey: jobKey(jobId),
    queryFn: () => apiFetch<Job>(`/v1/jobs/${jobId}`),
    refetchInterval: (q) => (isJobActive(q.state.data) ? POLL_MS : false),
  });
  const chapters = useQuery({
    queryKey: chaptersKey(bookId),
    queryFn: () => apiFetch<Chapter[]>(`/v1/books/${bookId}/chapters`),
  });

  const wasActive = useRef(false);
  const status = job.data?.status;
  useEffect(() => {
    if (!status) return;
    if (isJobActive({ status })) {
      wasActive.current = true;
    } else if (wasActive.current) {
      wasActive.current = false;
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: summaryKey(bookId) });
      void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    }
  }, [status, client, bookId]);

  const cancel = useMutation({
    mutationFn: () => apiFetch<Job>(`/v1/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: (j) => {
      client.setQueryData(jobKey(jobId), j);
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: summaryKey(bookId) });
      void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    },
  });

  if (job.isPending) return <p className="text-sm text-muted-foreground">Loading progress…</p>;
  if (job.isError) return <p role="alert" className="text-sm text-destructive">{detailOf(job.error)}</p>;

  const j = job.data;
  const title = (id: number | null) => chapters.data?.find((c) => c.id === id);
  const spent = Number(j.cost_actual);
  const cap = Number(j.max_cost);
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0;
  const noun = jobNoun(j).toLowerCase();

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span>Cost so far</span>
          <span>{money(j.cost_actual)} of {money(j.max_cost)}</span>
        </div>
        <Progress value={pct} aria-label="Cost so far" />
      </div>

      <ul className="space-y-3">
        {j.tasks.map((t) => {
          const c = title(t.chapter_id);
          const name = t.kind === "consistency" ? "Consistency check" : c ? `${c.ordinal}. ${c.title}` : `Chapter ${t.chapter_id}`;
          const progress = taskProgress(t);
          return (
            <li key={t.id} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span>{name}</span>
                <span className="text-muted-foreground">{progress.text}</span>
              </div>
              <Progress value={progress.pct} aria-label={name} />
            </li>
          );
        })}
      </ul>

      {/* A translation has nothing to review until proofreading has run. */}
      {j.type !== "translate" && (
        <p className="text-sm text-muted-foreground">
          Chapters that are already done can be reviewed while the rest runs.{" "}
          <Link className="underline" to={`/projects/${bookId}/review`}>Review</Link>
        </p>
      )}

      {cancel.isError && <p role="alert" className="text-sm text-destructive">{detailOf(cancel.error)}</p>}

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="outline">Cancel</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel {noun}?</AlertDialogTitle>
            <AlertDialogDescription>
              Work already finished is kept. You can resume later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep going</AlertDialogCancel>
            <AlertDialogAction onClick={() => cancel.mutate()}>Cancel {noun}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
