import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import type { ChapterSummary } from "@/lib/jobs";
import { CATEGORIES, SEVERITIES, VIEWS, type Filters, type View } from "@/lib/review";
import { cn } from "@/lib/utils";

interface Props {
  bookId: string;
  chapters: ChapterSummary[];
  chapterId: string;
  onChapter: (id: string) => void;
  view: View;
  onView: (v: View) => void;
  filters: Filters;
  onFilters: (f: Filters) => void;
  /** Open suggestions in this chapter, from the chapter summary. */
  left: number | undefined;
  /** The bulk action for the category filter, kept in view with the other controls. */
  bulk?: ReactNode;
}

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

export default function ReviewToolbar(p: Props) {
  return (
    <div data-review-toolbar className="sticky top-0 z-10 -mx-4 border-b bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
      <div className="flex flex-wrap items-end gap-3">
        <Field id="review-chapter" label="Chapter">
          <select
            id="review-chapter"
            className={cn(NATIVE_SELECT, "w-56 max-w-full")}
            value={p.chapterId}
            onChange={(e) => p.onChapter(e.target.value)}
          >
            {p.chapters.map((c) => (
              <option key={c.chapter_id} value={String(c.chapter_id)}>
                {`${c.ordinal}. ${c.title} (${c.open} open)`}
              </option>
            ))}
          </select>
        </Field>
        <Field id="review-category" label="Category">
          <select
            id="review-category"
            className={cn(NATIVE_SELECT, "w-40")}
            value={p.filters.category}
            onChange={(e) => p.onFilters({ ...p.filters, category: e.target.value })}
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field id="review-severity" label="Severity">
          <select
            id="review-severity"
            className={cn(NATIVE_SELECT, "w-36")}
            value={p.filters.severity}
            onChange={(e) => p.onFilters({ ...p.filters, severity: e.target.value })}
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <div role="group" aria-label="View" className="flex rounded-md border p-0.5">
          {VIEWS.map((v) => (
            <Button
              key={v.value}
              type="button"
              size="sm"
              variant={p.view === v.value ? "secondary" : "ghost"}
              aria-pressed={p.view === v.value}
              onClick={() => p.onView(v.value)}
            >
              {v.label}
            </Button>
          ))}
        </div>
        {p.bulk}
        <p aria-live="polite" className="ml-auto text-lg font-semibold tabular-nums">
          {p.left === undefined ? "" : `${p.left} left`}
        </p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <Link to={`/projects/${p.bookId}`} className="underline-offset-2 hover:underline">
          Project overview
        </Link>
        <span>Keys: j / k move · a accept · r reject · e edit · u undo · Esc close</span>
      </div>
    </div>
  );
}
