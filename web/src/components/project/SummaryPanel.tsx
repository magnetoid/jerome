import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Project } from "@/hooks/useProjects";
import { detailOf, summaryKey, type ChapterSummary } from "@/lib/jobs";

export default function SummaryPanel({ project }: { project: Project }) {
  const id = String(project.id);
  const summary = useQuery({
    queryKey: summaryKey(id),
    queryFn: () => apiFetch<ChapterSummary[]>(`/v1/books/${id}/chapter-summary`),
  });

  return (
    <div className="space-y-4">
      <p className="text-lg font-medium">
        {`AI applied ${project.auto_applied_count} · ${project.open_count} need you · ${project.decided_count} decided by the team`}
      </p>
      <Button asChild>
        <Link to={`/projects/${id}/review`}>Review</Link>
      </Button>
      {summary.isError && <p role="alert" className="text-sm text-destructive">{detailOf(summary.error)}</p>}
      {summary.data && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Chapter</TableHead>
              <TableHead>Need you</TableHead>
              <TableHead>AI applied</TableHead>
              <TableHead>Decided</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {summary.data.map((c) => (
              <TableRow key={c.chapter_id}>
                <TableCell>
                  <Link className="underline-offset-2 hover:underline" to={`/projects/${id}/review?chapter=${c.chapter_id}`}>
                    {c.ordinal}. {c.title}
                  </Link>
                </TableCell>
                <TableCell>{c.open}</TableCell>
                <TableCell>{c.auto_applied}</TableCell>
                <TableCell>{c.decided}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
