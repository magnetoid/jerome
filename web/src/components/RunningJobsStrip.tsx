import { Link } from "react-router-dom";
import { Progress } from "@/components/ui/progress";
import { isProgressing, type Project } from "@/hooks/useProjects";
import { money } from "@/lib/format";

export default function RunningJobsStrip({ projects }: { projects: Project[] }) {
  const running = projects.filter(isProgressing);
  if (running.length === 0) return null;
  return (
    <section aria-label="Running jobs" className="mb-6 space-y-3 rounded-lg border p-4">
      {running.map((p) => {
        const j = p.active_job!;
        const pct = j.chapters_total > 0 ? (j.chapters_done / j.chapters_total) * 100 : 0;
        return (
          <div key={p.id} className="grid items-center gap-x-4 gap-y-1 sm:grid-cols-[1fr_2fr_auto]">
            <Link to={`/projects/${p.id}`} className="font-medium hover:underline">
              {p.title}
            </Link>
            <div className="flex items-center gap-3">
              <Progress value={pct} aria-label={`Progress for ${p.title}`} />
              <span className="text-sm whitespace-nowrap text-muted-foreground">
                {j.chapters_done} / {j.chapters_total}
              </span>
            </div>
            <span className="text-sm text-muted-foreground">
              {money(j.cost_actual)} of {money(j.max_cost)}
            </span>
          </div>
        );
      })}
    </section>
  );
}
