import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type Stage =
  | "importing" | "set_up" | "translating" | "translated" | "proofreading" | "needs_attention" | "to_review" | "done";

const STAGES: Record<
  Stage,
  { label: string; variant: "secondary" | "default" | "destructive" | "outline"; className?: string }
> = {
  importing: { label: "Importing", variant: "secondary" },
  set_up: { label: "Set up", variant: "outline" },
  translating: { label: "Translating", variant: "secondary" },
  translated: { label: "Translated", variant: "outline", className: "border-sky-600 text-sky-700 dark:border-sky-500 dark:text-sky-400" },
  proofreading: { label: "Proofreading", variant: "secondary" },
  needs_attention: { label: "Needs attention", variant: "destructive" },
  to_review: { label: "To review", variant: "default" },
  done: { label: "Done", variant: "outline", className: "border-green-600 text-green-700 dark:text-green-400" },
};

/** Stages in which Jerome is working right now: their badge carries a pulsing dot. */
const RUNNING: ReadonlySet<Stage> = new Set(["importing", "translating", "proofreading"]);

export default function StageBadge({ stage }: { stage: Stage }) {
  const s = STAGES[stage];
  return (
    <Badge variant={s.variant} className={cn(RUNNING.has(stage) && "gap-1.5", s.className)} data-stage={stage}>
      {RUNNING.has(stage) && (
        <span data-running-dot aria-hidden className="pulse-dot size-1.5 shrink-0 rounded-full bg-brand" />
      )}
      {s.label}
    </Badge>
  );
}
