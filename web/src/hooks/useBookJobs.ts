import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { POLL_MS, type ActiveJob } from "@/hooks/useProjects";
import { isJobActive, jobsKey, type Job } from "@/lib/jobs";

/**
 * A book's jobs, newest first. While the project's active job is queued or running the list
 * is polled like the projects list, and it is refetched whenever that job changes or ends:
 * the projects poll often sees a job finish first, and then nothing else would refresh it.
 * `activeJob` is undefined while the project is not known yet.
 */
export function useBookJobs(bookId: string, activeJob: ActiveJob | null | undefined) {
  const client = useQueryClient();
  const running = activeJob?.status === "queued" || activeJob?.status === "running";
  const jobs = useQuery({
    queryKey: jobsKey(bookId),
    queryFn: () => apiFetch<Job[]>(`/v1/books/${bookId}/jobs`),
    // A publish job is not the project's active job, so the list also polls while one runs.
    refetchInterval: (q) =>
      running || q.state.data?.some((j) => j.type === "publish" && isJobActive(j)) ? POLL_MS : false,
  });

  const marker = activeJob === undefined ? undefined : activeJob ? `${activeJob.id}:${activeJob.status}` : "";
  const seen = useRef(marker);
  useEffect(() => {
    if (marker === undefined || seen.current === marker) return;
    const known = seen.current !== undefined; // the first look at the project is no change
    seen.current = marker;
    if (known) void client.invalidateQueries({ queryKey: jobsKey(bookId) });
  }, [marker, client, bookId]);

  return jobs;
}
