import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ACTIVITY_PAGE_SIZE, useAgentActions, type AgentAction } from "@/lib/activity";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const compact = (v: unknown) => JSON.stringify(v);

function Changes({ a }: { a: AgentAction }) {
  if (a.before == null && a.after == null) return <span className="text-muted-foreground">-</span>;
  return (
    <code className="break-all text-xs">
      {compact(a.before ?? null)} → {compact(a.after ?? null)}
    </code>
  );
}

function Where({ a }: { a: AgentAction }) {
  return (
    <div className="space-x-2 text-xs">
      {a.book_id != null && (
        <Link className="underline underline-offset-4" to={`/projects/${a.book_id}`}>
          Book {a.book_id}
        </Link>
      )}
      {a.book_id != null && a.chapter_id != null && (
        <Link className="underline underline-offset-4" to={`/projects/${a.book_id}/review/${a.chapter_id}`}>
          Chapter {a.chapter_id}
        </Link>
      )}
      {a.job_id != null && <span className="text-muted-foreground">Job {a.job_id}</span>}
    </div>
  );
}

export default function ActivityPage() {
  const [params] = useSearchParams();
  const book = params.get("book") || undefined;
  const [page, setPage] = useState(0);
  const actions = useAgentActions(book, page);
  const rows = actions.data?.data ?? [];
  const total = actions.data?.total ?? 0;

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Janus activity</h1>
        {book && (
          <Link to={`/projects/${book}`} className="text-sm underline underline-offset-4">
            Back to the book
          </Link>
        )}
      </header>

      {actions.isError ? (
        <p role="alert">Could not load Janus activity.</p>
      ) : actions.isPending ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground">Janus hasn't changed anything yet.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Who</TableHead>
                <TableHead>What</TableHead>
                <TableHead>Changes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((a) => (
                <TableRow key={a.id}>
                  <TableCell className="whitespace-nowrap">{new Date(a.at).toLocaleString()}</TableCell>
                  <TableCell>Janus ({a.actor})</TableCell>
                  <TableCell className="space-y-1 whitespace-normal">
                    <div>{a.summary}</div>
                    <Where a={a} />
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <Changes a={a} />
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
