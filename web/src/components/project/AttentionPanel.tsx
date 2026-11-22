import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PROJECTS_KEY } from "@/hooks/useProjects";
import { money } from "@/lib/format";
import { chaptersKey, detailOf, jobKey, jobsKey, type Chapter, type Job } from "@/lib/jobs";

interface Props {
  bookId: string;
  /** The project's active (paused) job, if the list reports one. */
  activeJobId: number | string | null;
}

export default function AttentionPanel({ bookId, activeJobId }: Props) {
  const client = useQueryClient();
  // A paused job is the project's active job; otherwise the latest job explains what happened.
  const jobs = useQuery({
    queryKey: jobsKey(bookId),
    queryFn: () => apiFetch<Job[]>(`/v1/books/${bookId}/jobs`),
    enabled: activeJobId === null,
  });
  const active = useQuery({
    queryKey: jobKey(activeJobId ?? "none"),
    queryFn: () => apiFetch<Job>(`/v1/jobs/${activeJobId}`),
    enabled: activeJobId !== null,
  });
  const chapters = useQuery({
    queryKey: chaptersKey(bookId),
    queryFn: () => apiFetch<Chapter[]>(`/v1/books/${bookId}/chapters`),
  });
  const [raised, setRaised] = useState("");

  const source = activeJobId !== null ? active : jobs;
  const job = activeJobId !== null ? active.data : jobs.data?.[0];

  const resume = useMutation({
    mutationFn: () => {
      const max = Number(raised);
      const body = raised.trim() !== "" && max > 0 ? { max_cost: max } : {};
      return apiFetch<Job>(`/v1/jobs/${job!.id}/resume`, { method: "POST", json: body });
    },
    onSuccess: (j) => {
      client.setQueryData(jobKey(j.id), j);
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    },
  });

  if (source.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (source.isError) return <p role="alert" className="text-sm text-destructive">{detailOf(source.error)}</p>;
  if (!job) return <p className="text-sm">Something needs your attention, but no job was found.</p>;

  const failed = job.tasks.filter((t) => t.status === "failed");
  const nameOf = (id: number | null) => {
    const c = chapters.data?.find((x) => x.id === id);
    return c ? `${c.ordinal}. ${c.title}` : "Consistency check";
  };
  const capped = job.status === "paused_cost_cap";

  return (
    <div className="space-y-4">
      {capped ? (
        <p>
          Proofreading paused at its cost limit ({money(job.cost_actual)} of {money(job.max_cost)}).
          Raise the limit to carry on.
        </p>
      ) : (
        <p>
          Proofreading {job.status === "cancelled" ? "was cancelled" : "stopped because something went wrong"}.
        </p>
      )}
      {failed.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {failed.map((t) => (
            <li key={t.id}>{nameOf(t.chapter_id)}: {t.error ?? "failed"}</li>
          ))}
        </ul>
      )}
      <div className="max-w-xs space-y-2">
        <Label htmlFor="new-limit">New cost limit (dollars, optional)</Label>
        <Input
          id="new-limit"
          type="number"
          min="0"
          step="any"
          placeholder={`Currently ${money(job.max_cost)}`}
          value={raised}
          onChange={(e) => setRaised(e.target.value)}
        />
      </div>
      {resume.isError && <p role="alert" className="text-sm text-destructive">{detailOf(resume.error)}</p>}
      <Button onClick={() => resume.mutate()} disabled={resume.isPending}>
        {resume.isPending ? "Resuming…" : "Resume"}
      </Button>
    </div>
  );
}
