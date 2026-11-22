import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PROJECTS_KEY } from "@/hooks/useProjects";
import { money } from "@/lib/format";
import {
  FREE_PASSES, FULL_PASSES, chaptersKey, detailOf, jobsKey, type Chapter,
} from "@/lib/jobs";

interface Estimate {
  chunks: number;
  total: { low: string; high: string };
}

const DEBOUNCE_MS = 300;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function StartPanel({ bookId }: { bookId: string }) {
  const client = useQueryClient();
  const chapters = useQuery({
    queryKey: chaptersKey(bookId),
    queryFn: () => apiFetch<Chapter[]>(`/v1/books/${bookId}/chapters`),
  });
  const [ticks, setTicks] = useState<number[] | null>(null);
  const [free, setFree] = useState(false);
  const [limit, setLimit] = useState<string | null>(null);

  const ticked = ticks ?? (chapters.data ?? []).filter((c) => c.proofread).map((c) => c.id);
  const settled = useDebounced(ticked.join(","), DEBOUNCE_MS);
  const settledIds = settled ? settled.split(",").map(Number) : [];
  const passes = free ? FREE_PASSES : FULL_PASSES;
  // Wait for the debounce, so the estimate never runs for ticks that are already stale.
  const inSync = settled === ticked.join(",");

  const mark = useMutation({
    mutationFn: (id: number) =>
      apiFetch<Chapter>(`/v1/chapters/${id}`, { method: "PATCH", json: { proofread: true } }),
    onSuccess: (c) =>
      client.setQueryData<Chapter[]>(chaptersKey(bookId), (old) =>
        old?.map((x) => (x.id === c.id ? c : x)),
      ),
  });

  const estimate = useQuery({
    queryKey: ["estimate", bookId, passes, settledIds],
    queryFn: () =>
      apiFetch<Estimate>(`/v1/books/${bookId}/estimate`, {
        method: "POST",
        json: { passes, chapters: settledIds },
      }),
    enabled: chapters.isSuccess && settledIds.length > 0 && !mark.isPending,
  });

  const suggested = estimate.data
    ? String(Math.max(1, Math.ceil(Number(estimate.data.total.high))))
    : "";
  const shown = limit ?? suggested;
  const maxCost = Number(shown);
  const limitValid = shown.trim() !== "" && Number.isFinite(maxCost) && maxCost > 0;

  const start = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/books/${bookId}/jobs`, {
        method: "POST",
        json: { type: "proofread", passes, chapters: ticked, max_cost: maxCost },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    },
  });

  const canStart = ticked.length > 0 && inSync && !mark.isPending && limitValid && !start.isPending;

  function toggle(c: Chapter, on: boolean) {
    setTicks(on ? [...ticked, c.id] : ticked.filter((id) => id !== c.id));
    // The estimate and job endpoints refuse unmarked chapters. Unticking never un-marks.
    if (on && !c.proofread) mark.mutate(c.id);
  }

  const error = mark.isError ? detailOf(mark.error)
    : estimate.isError ? detailOf(estimate.error)
    : start.isError ? detailOf(start.error)
    : null;

  if (chapters.isPending) return <p className="text-sm text-muted-foreground">Loading chapters…</p>;
  if (chapters.isError) return <p role="alert" className="text-sm text-destructive">{detailOf(chapters.error)}</p>;

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (canStart) start.mutate();
      }}
    >
      <fieldset className="space-y-2">
        <legend className="mb-1 text-sm font-medium">Chapters</legend>
        {chapters.data.map((c) => (
          <div key={c.id} className="flex items-center gap-2">
            <Checkbox
              id={`chapter-${c.id}`}
              checked={ticked.includes(c.id)}
              onCheckedChange={(v) => toggle(c, v === true)}
            />
            <Label htmlFor={`chapter-${c.id}`} className="font-normal">{c.ordinal}. {c.title}</Label>
          </div>
        ))}
      </fieldset>

      <div role="radiogroup" aria-label="Proofreading type" className="space-y-2">
        {[
          { free: false, label: "AI + LanguageTool" },
          { free: true, label: "Free check (LanguageTool only)" },
        ].map((o) => (
          <label key={o.label} className="flex items-center gap-2 text-sm">
            <input type="radio" name="passes" checked={free === o.free} onChange={() => setFree(o.free)} />
            {o.label}
          </label>
        ))}
      </div>

      <p className="text-sm" aria-live="polite">
        {ticked.length === 0 ? "Tick at least one chapter."
          : estimate.data
            ? `Estimated cost: ${money(estimate.data.total.low)} to ${money(estimate.data.total.high)}`
            : estimate.isError ? "" : "Estimating cost…"}
      </p>

      <div className="max-w-xs space-y-2">
        <Label htmlFor="cost-limit">Cost limit (dollars)</Label>
        <Input
          id="cost-limit"
          type="number"
          min="0"
          step="any"
          inputMode="decimal"
          value={shown}
          onChange={(e) => setLimit(e.target.value)}
        />
        <p className="text-sm text-muted-foreground">Proofreading pauses if it reaches this.</p>
      </div>

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      <Button type="submit" disabled={!canStart}>
        {start.isPending ? "Starting…" : "Start proofreading"}
      </Button>
    </form>
  );
}
