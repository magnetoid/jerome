import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import StartPanel from "@/components/project/StartPanel";
import { PROJECTS_KEY, type Project } from "@/hooks/useProjects";
import { money, variantLabel } from "@/lib/format";
import {
  bookKey, chaptersKey, detailOf, jobsKey, translationKey, type Chapter, type Job,
  type TranslatedBlock, type TranslationState,
} from "@/lib/jobs";

interface Estimate {
  chunks: number;
  total: { low: string; high: string };
}

interface Props {
  project: Project;
  /** The book's file format: only a DOCX block can be kept as the original. */
  format: string;
}

/**
 * The project page's main panel for a translate + proofread project before proofreading:
 * the translation's estimate and Start while blocks wait for it (`set_up`), then the result
 * and the proofreading Start once it has finished (`translated`).
 */
export default function TranslationPanel({ project, format }: Props) {
  return project.stage === "translated"
    ? <TranslatedSummary project={project} format={format} />
    : <TranslationStart project={project} />;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function TranslationStart({ project }: { project: Project }) {
  const bookId = String(project.id);
  const client = useQueryClient();
  const [limit, setLimit] = useState<string | null>(null);

  const estimate = useQuery({
    queryKey: ["estimate", bookId, "translate"],
    queryFn: () =>
      apiFetch<Estimate>(`/v1/books/${bookId}/estimate`, {
        method: "POST",
        json: { type: "translate" },
      }),
  });

  const suggested = estimate.data
    ? String(Math.max(1, Math.ceil(Number(estimate.data.total.high))))
    : "";
  const shown = limit ?? suggested;
  const maxCost = Number(shown);
  const limitValid = shown.trim() !== "" && Number.isFinite(maxCost) && maxCost > 0;

  const start = useMutation({
    mutationFn: () =>
      apiFetch<Job>(`/v1/books/${bookId}/jobs`, {
        method: "POST",
        json: { type: "translate", max_cost: maxCost },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      void client.invalidateQueries({ queryKey: jobsKey(bookId) });
    },
  });

  const canStart = limitValid && !start.isPending;
  const error = estimate.isError ? detailOf(estimate.error) : start.isError ? detailOf(start.error) : null;
  const pending = project.translation?.pending;
  const from = project.source_variant ? variantLabel(project.source_variant) : "the source language";

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (canStart) start.mutate();
      }}
    >
      <p className="text-sm">
        {pending !== undefined
          ? `${plural(pending, "paragraph", "paragraphs")} to translate`
          : "Jerome translates the book"}
        {` from ${from} to ${variantLabel(project.variant)}, chapter by chapter. `}
        Proofreading waits for you to start it once the translation is done.
      </p>

      <p className="text-sm" aria-live="polite">
        {estimate.data
          ? `Estimated cost: ${money(estimate.data.total.low)} to ${money(estimate.data.total.high)}`
          : estimate.isError ? "" : "Estimating cost…"}
      </p>

      <div className="max-w-xs space-y-2">
        <Label htmlFor="translation-cost-limit">Cost limit (dollars)</Label>
        <Input
          id="translation-cost-limit"
          type="number"
          min="0"
          step="any"
          inputMode="decimal"
          value={shown}
          onChange={(e) => setLimit(e.target.value)}
        />
        <p className="text-sm text-muted-foreground">The translation pauses if it reaches this.</p>
      </div>

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      <Button type="submit" disabled={!canStart}>
        {start.isPending ? "Starting…" : "Start translation"}
      </Button>
    </form>
  );
}

function useTranslationState(bookId: string) {
  return useQuery({
    queryKey: translationKey(bookId),
    queryFn: () => apiFetch<TranslationState>(`/v1/books/${bookId}/translation`),
  });
}

function TranslatedSummary({ project, format }: Props) {
  const bookId = String(project.id);
  const state = useTranslationState(bookId);
  const counts = state.data?.counts ?? {};
  const parts = [plural(counts.done ?? 0, "paragraph translated", "paragraphs translated")];
  if (counts.flagged) parts.push(`${counts.flagged} flagged`);
  if (counts.kept) parts.push(`${counts.kept} kept as the original`);

  return (
    <div className="space-y-6">
      {state.isPending && <p className="text-sm text-muted-foreground">Loading the translation…</p>}
      {state.isError && <p role="alert" className="text-sm text-destructive">{detailOf(state.error)}</p>}
      {state.isSuccess && <p className="text-lg font-medium">{parts.join(" · ")}</p>}

      <FlaggedParagraphs project={project} format={format} />

      <section className="space-y-4 border-t pt-4">
        <h2 className="text-lg font-medium">Proofreading</h2>
        <p className="text-sm text-muted-foreground">
          Proofreading runs on the translated text, in {variantLabel(project.variant)}.
        </p>
        <StartPanel bookId={bookId} openCount={project.open_count} flaggedCount={state.data?.flagged.length ?? 0} />
      </section>
    </div>
  );
}

/**
 * The paragraphs the translation flagged, with Translate again and (on DOCX) Keep original.
 * Renders nothing while none are flagged. Shown at `translated` and at every later stage, since
 * flagged paragraphs hold up the export until they are resolved.
 */
export function FlaggedParagraphs({ project, format }: Props) {
  const bookId = String(project.id);
  const client = useQueryClient();
  const state = useTranslationState(bookId);
  const chapters = useQuery({
    queryKey: chaptersKey(bookId),
    queryFn: () => apiFetch<Chapter[]>(`/v1/books/${bookId}/chapters`),
  });

  function refresh() {
    void client.invalidateQueries({ queryKey: translationKey(bookId) });
    void client.invalidateQueries({ queryKey: PROJECTS_KEY });
  }

  const again = useMutation({
    mutationFn: (blockId: number) =>
      apiFetch<TranslatedBlock>(`/v1/blocks/${blockId}/translate`, { method: "POST" }),
    onSuccess: (b) => {
      if (b.translation_status === "flagged") {
        toast.error("The paragraph still could not be translated. Try again, or keep the original.");
      } else {
        toast.success("Paragraph translated");
      }
      refresh();
    },
    onError: (e) => toast.error(detailOf(e)),
  });

  const keep = useMutation({
    mutationFn: (blockId: number) =>
      apiFetch<TranslatedBlock>(`/v1/blocks/${blockId}/keep-source`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Original kept. Fix it in the Word round trip.");
      refresh();
    },
    onError: (e) => toast.error(detailOf(e)),
  });

  const flagged = state.data?.flagged ?? [];
  if (flagged.length === 0) return null;

  const chapterName = (id: number) => {
    const c = chapters.data?.find((x) => x.id === id);
    return c ? `${c.ordinal}. ${c.title}` : "";
  };
  const busy = (id: number) =>
    (again.isPending && again.variables === id) || (keep.isPending && keep.variables === id);
  const docx = format === "docx";

  return (
    <section aria-labelledby="flagged-heading" className="space-y-3">
      <h2 id="flagged-heading" className="text-lg font-medium">Paragraphs that could not be translated</h2>
      <p className="text-sm text-muted-foreground">
        {docx
          ? "Each keeps its source text until it is translated again, or kept as the original to fix in the Word round trip."
          : "Each keeps its source text until it is translated again."}
      </p>
      <ul aria-label="Flagged paragraphs" className="space-y-3">
        {flagged.map((f) => (
          <li key={f.block_id} className="space-y-2 rounded-md border p-3">
            <p className="text-xs text-muted-foreground">{chapterName(f.chapter_id)}</p>
            <blockquote
              lang={project.source_variant ?? undefined}
              className="border-l-2 pl-3 text-sm whitespace-pre-wrap"
            >
              {f.source}
            </blockquote>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={busy(f.block_id)} onClick={() => again.mutate(f.block_id)}>
                {again.isPending && again.variables === f.block_id ? "Translating…" : "Translate again"}
              </Button>
              {docx && (
                <Button size="sm" variant="ghost" disabled={busy(f.block_id)} onClick={() => keep.mutate(f.block_id)}>
                  Keep original
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface BookDetails {
  id: number;
  title: string;
  format: string;
  original_filename: string | null;
  created_at: string;
}

/** The header's link to the hidden, read-only source book of a translation. */
export function SourceBookLink({ sourceId, variant }: { sourceId: number; variant?: string | null }) {
  const source = useQuery({
    queryKey: bookKey(String(sourceId)),
    queryFn: () => apiFetch<BookDetails>(`/v1/books/${sourceId}`),
  });
  if (!source.data) return null;
  const b = source.data;
  const language = variant ? variantLabel(variant) : null;
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Translated from {b.title}{language ? ` · ${language}` : ""}
        </button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Source book</DialogTitle>
          <DialogDescription>
            The book this translation was made from. Jerome keeps it read-only; it is not a project of its own.
          </DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Title</dt>
          <dd>{b.title}</dd>
          {language && (
            <>
              <dt className="text-muted-foreground">Language</dt>
              <dd>{language}</dd>
            </>
          )}
          {b.original_filename && (
            <>
              <dt className="text-muted-foreground">File</dt>
              <dd className="break-all">{b.original_filename}</dd>
            </>
          )}
          <dt className="text-muted-foreground">Imported</dt>
          <dd>{new Date(b.created_at).toLocaleDateString()}</dd>
        </dl>
      </DialogContent>
    </Dialog>
  );
}
