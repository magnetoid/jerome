import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import StageBadge from "@/components/StageBadge";
import RunningJobsStrip from "@/components/RunningJobsStrip";
import { useProjects, type Project } from "@/hooks/useProjects";
import { relativeTime, variantLabel } from "@/lib/format";

function nextStep(p: Project): { label: string; to: string } {
  const base = `/projects/${p.id}`;
  switch (p.stage) {
    case "set_up": return { label: "Start proofreading", to: base };
    case "proofreading": return { label: "View progress", to: base };
    case "needs_attention": return { label: "Resume", to: base };
    case "to_review": return { label: `Review (${p.open_count} left)`, to: `${base}/review` };
    case "done": return { label: "Export", to: base };
  }
}

export default function ProjectsPage() {
  const q = useProjects();
  const [search, setSearch] = useState("");

  return (
    <main className="mx-auto max-w-6xl p-4 sm:p-8">
      <div className="mb-6 flex items-center gap-4">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <div className="ml-auto flex items-center gap-2">
          <Label htmlFor="project-search" className="sr-only">Search projects</Label>
          <Input
            id="project-search"
            type="search"
            placeholder="Search projects"
            className="w-64"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {q.isError && <p role="alert">Could not load projects. Try reloading the page.</p>}
      {q.isPending && <p className="text-muted-foreground">Loading…</p>}
      {q.data && <ProjectsBody projects={q.data} search={search} />}
    </main>
  );
}

function ProjectsBody({ projects, search }: { projects: Project[]; search: string }) {
  if (projects.length === 0) {
    return (
      <div className="rounded-lg border p-12 text-center">
        <p className="mb-4 text-lg">No projects yet</p>
        <Button asChild><Link to="/projects/new">+ New project</Link></Button>
      </div>
    );
  }
  const needle = search.trim().toLowerCase();
  const shown = needle ? projects.filter((p) => p.title.toLowerCase().includes(needle)) : projects;
  return (
    <>
      <RunningJobsStrip projects={projects} />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Language</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Progress</TableHead>
            <TableHead>Next step</TableHead>
            <TableHead>Last activity</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((p) => {
            const step = nextStep(p);
            return (
              <TableRow key={p.id}>
                <TableCell className="font-medium">{p.title}</TableCell>
                <TableCell>{variantLabel(p.variant)}</TableCell>
                <TableCell><StageBadge stage={p.stage} /></TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {p.open_count} left · {p.auto_applied_count} applied by AI · {p.decided_count} decided
                </TableCell>
                <TableCell>
                  <Button asChild size="sm" variant={p.stage === "to_review" ? "default" : "outline"}>
                    <Link to={step.to}>{step.label}</Link>
                  </Button>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {p.last_activity ? `${p.last_activity.actor}, ${relativeTime(p.last_activity.at)}` : "—"}
                </TableCell>
              </TableRow>
            );
          })}
          {shown.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-muted-foreground">
                No projects match your search
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </>
  );
}
