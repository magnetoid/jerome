import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { summaryKey, type ChapterSummary } from "@/lib/jobs";
import {
  ACTIVITY_PAGE_SIZE,
  WHO_LABELS,
  useActivity,
  useRevert,
  whoLabel,
  type ActivityItem,
  type Who,
} from "@/lib/activity";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const compact = (v: unknown) => JSON.stringify(v);

function Changes({ a }: { a: ActivityItem }) {
  if (a.kind === "decision" || (a.before == null && a.after == null)) {
    return <span className="text-muted-foreground">-</span>;
  }
  return (
    <code className="break-all text-xs">
      {compact(a.before ?? null)} → {compact(a.after ?? null)}
    </code>
  );
}

function Where({ a, showBook }: { a: ActivityItem; showBook: boolean }) {
  return (
    <div className="space-x-2 text-xs">
      {showBook && a.book_id != null && (
        <Link className="underline underline-offset-4" to={`/projects/${a.book_id}`}>
          {a.book_title ?? `Book ${a.book_id}`}
        </Link>
      )}
      {a.book_id != null && a.chapter_id != null && (
        <Link className="underline underline-offset-4" to={`/projects/${a.book_id}/review/${a.chapter_id}`}>
          {a.chapter_title || `Chapter ${a.chapter_id}`}
        </Link>
      )}
      {a.job_id != null && <span className="text-muted-foreground">Job {a.job_id}</span>}
    </div>
  );
}

function Revert({ a }: { a: ActivityItem }) {
  const revert = useRevert();
  if (a.kind !== "decision" || a.action === "undo") return null;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="outline"
        size="sm"
        disabled={!a.revertable || revert.isPending}
        aria-describedby={a.revert_blocked ? `revert-why-${a.id}` : undefined}
        onClick={() => revert.mutate(a.id)}
      >
        Revert
      </Button>
      {a.revert_blocked && <span id={`revert-why-${a.id}`} className="max-w-48 text-right text-xs leading-snug text-muted-foreground">{a.revert_blocked}</span>}
    </div>
  );
}

const WHO = Object.keys(WHO_LABELS) as Who[];

export default function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const book = params.get("book") || undefined;
  const chapter = (book && params.get("chapter")) || undefined;
  const whoParam = params.get("who");
  const who = WHO.includes(whoParam as Who) ? (whoParam as Who) : undefined;
  const [page, setPage] = useState(0);
  const actions = useActivity({ book, chapter, who }, page);
  const chapters = useQuery({
    queryKey: summaryKey(book ?? ""),
    queryFn: () => apiFetch<ChapterSummary[]>(`/v1/books/${book}/chapter-summary`),
    enabled: book !== undefined,
  });
  const rows = actions.data?.data ?? [];
  const total = actions.data?.total ?? 0;

  function setFilter(key: "who" | "chapter", value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
    setPage(0);
  }

  return (
    <main className="w-full space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Activity</h1>
        {book && (
          <Link to={`/projects/${book}`} className="text-sm underline underline-offset-4">
            Back to the book
          </Link>
        )}
      </header>

      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="activity-who">Who</Label>
          <select
            id="activity-who"
            className={NATIVE_SELECT}
            value={who ?? ""}
            onChange={(e) => setFilter("who", e.target.value)}
          >
            <option value="">Everyone</option>
            {WHO.map((w) => (
              <option key={w} value={w}>
                {WHO_LABELS[w]}
              </option>
            ))}
          </select>
        </div>
        {book && (
          <div className="grid gap-2">
            <Label htmlFor="activity-chapter">Chapter</Label>
            <select
              id="activity-chapter"
              className={NATIVE_SELECT}
              value={chapter ?? ""}
              onChange={(e) => setFilter("chapter", e.target.value)}
            >
              <option value="">All chapters</option>
              {(chapters.data ?? []).map((c) => (
                <option key={c.chapter_id} value={String(c.chapter_id)}>
                  {c.title || `Chapter ${c.ordinal}`}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {actions.isError ? (
        <p role="alert">Could not load activity.</p>
      ) : actions.isPending ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground">Nothing has been changed yet.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Who</TableHead>
                <TableHead>What</TableHead>
                <TableHead>Changes</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Revert</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((a) => (
                <TableRow key={`${a.kind}-${a.id}`}>
                  <TableCell className="whitespace-nowrap">{new Date(a.at).toLocaleString()}</TableCell>
                  <TableCell>{whoLabel(a)}</TableCell>
                  <TableCell className="space-y-1 whitespace-normal">
                    <div>{a.summary}</div>
                    <Where a={a} showBook={!book} />
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <Changes a={a} />
                  </TableCell>
                  <TableCell className="whitespace-normal text-right">
                    <Revert a={a} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="flex items-center justify-between">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              {page * ACTIVITY_PAGE_SIZE + 1}–{page * ACTIVITY_PAGE_SIZE + rows.length} of {total}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={(page + 1) * ACTIVITY_PAGE_SIZE >= total}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        </>
      )}
    </main>
  );
}
