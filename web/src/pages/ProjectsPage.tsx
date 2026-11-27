import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { BookOpenIcon, SearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import StageBadge from "@/components/StageBadge";
import RunningJobsStrip from "@/components/RunningJobsStrip";
import Loading from "@/components/Loading";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjects, type Project } from "@/hooks/useProjects";
import { actorLabel } from "@/lib/review";
import { relativeTime, variantLabel } from "@/lib/format";

function nextStep(p: Project): { label: string; to: string } {
  const base = `/projects/${p.id}`;
  switch (p.stage) {
    case "importing": return { label: "View progress", to: base };
    case "set_up":
      return { label: p.work === "translate" ? "Start translation" : "Start proofreading", to: base };
    case "translating": return { label: "View progress", to: base };
    case "translated": return { label: "Start proofreading", to: base };
    case "proofreading": return { label: "View progress", to: base };
    case "needs_attention": return { label: "Resume", to: base };
    case "to_review": return { label: `Review (${p.open_count} left)`, to: `${base}/review` };
    case "done": return { label: "Export", to: base };
  }
}

/** Only the first rows of a list come in one after another; a long list must not take long. */
const STAGGERED = 12;

/** The book's language, or the language pair of a translation (`en-GB → de-DE`). */
function languageOf(p: Project): string {
  if (p.work === "translate" && p.source_variant) return `${p.source_variant} → ${p.variant}`;
  return variantLabel(p.variant);
}

export default function ProjectsPage() {
  const q = useProjects();
  const [search, setSearch] = useState("");

  return (
    <main className="w-full p-4 sm:p-8">
      <div className="mb-6 flex items-center gap-4">
        <h1 className="font-display text-3xl font-normal tracking-tight">Projects</h1>
        <div className="relative ml-auto">
          <Label htmlFor="project-search" className="sr-only">Search projects</Label>
          <SearchIcon aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="project-search"
            type="search"
            placeholder="Search projects"
            className="w-64 bg-card pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {q.isError && <p role="alert">Could not load projects. Try reloading the page.</p>}
      {q.isPending && (
        <Loading className="space-y-2">
          <Skeleton className="h-10 w-full" />
          {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </Loading>
      )}
      {q.data && <ProjectsBody projects={q.data} search={search} />}
    </main>
  );
}

function ProjectsBody({ projects, search }: { projects: Project[]; search: string }) {
  if (projects.length === 0) {
    return (
      <div className="flex flex-col items-center rounded-xl border border-dashed bg-card p-12 text-center">
        <span aria-hidden className="mb-4 grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
          <BookOpenIcon className="size-6" />
        </span>
        <p className="mb-4 font-display text-2xl">No projects yet</p>
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
          {shown.map((p, i) => {
            const step = nextStep(p);
            const staggered = i < STAGGERED;
            return (
              <TableRow
                key={p.id}
                className={staggered ? "row-enter" : undefined}
                style={staggered ? ({ "--i": i } as CSSProperties) : undefined}
              >
                <TableCell>
                  <Link to={`/projects/${p.id}`} className="font-display text-lg leading-tight underline-offset-4 hover:underline">{p.title}</Link>
                </TableCell>
                <TableCell>{languageOf(p)}</TableCell>
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
                  {p.last_activity ? `${actorLabel(p.last_activity.actor, p.last_activity.channel)}, ${relativeTime(p.last_activity.at)}` : "—"}
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
