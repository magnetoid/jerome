import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useMe } from "@/hooks/useMe";
import { FEEDBACK_KINDS, type FeedbackKind } from "@/components/FeedbackDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";

export const FEEDBACK_KEY = ["feedback"] as const;

export type FeedbackItem = {
  id: number;
  kind: FeedbackKind;
  message: string;
  page: string | null;
  viewport: string | null;
  user_agent: string | null;
  has_screenshot: boolean;
  status: "new" | "done";
  created_by: string;
  created_at: string;
};

type Show = "new" | "done" | "all";
const SHOW: Record<Show, string> = { new: "New", done: "Done", all: "All" };

function errorToast(err: unknown) {
  toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
}

/** What people sent with Send feedback: for the super admin to read and act on. */
export default function FeedbackPage() {
  const me = useMe();
  const client = useQueryClient();
  const admin = me.data?.role === "super_admin";
  const list = useQuery({
    queryKey: FEEDBACK_KEY,
    queryFn: () => apiFetch<FeedbackItem[]>("/v1/feedback"),
    enabled: admin,
  });
  const [show, setShow] = useState<Show>("new");
  const [deleting, setDeleting] = useState<FeedbackItem | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: FEEDBACK_KEY });
  const status = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "new" | "done" }) =>
      apiFetch(`/v1/feedback/${id}`, { method: "PATCH", json: { status } }),
    onError: errorToast,
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/v1/feedback/${id}`, { method: "DELETE" }),
    onError: errorToast,
    onSettled: refresh,
  });

  if (me.data && !admin) return <Navigate to="/" replace />;

  const items = list.data ?? [];
  const shown = items.filter((f) => show === "all" || f.status === show);
  const count = (s: Show) => (s === "all" ? items.length : items.filter((f) => f.status === s).length);

  return (
    <main className="w-full max-w-4xl space-y-6 p-4 sm:p-8">
      <header className="space-y-1">
        <h1 className="font-display text-3xl font-normal tracking-tight">Feedback</h1>
        <p className="text-sm text-muted-foreground">
          Bug reports, feature requests and recommendations sent with Send feedback, newest first.
        </p>
      </header>
      <div role="tablist" aria-label="Show" className="flex gap-2">
        {(Object.keys(SHOW) as Show[]).map((s) => (
          <Button
            key={s}
            role="tab"
            aria-selected={show === s}
            size="sm"
            variant={show === s ? "secondary" : "ghost"}
            onClick={() => setShow(s)}
          >
            {SHOW[s]} <span className="text-muted-foreground">{count(s)}</span>
          </Button>
        ))}
      </div>
      {list.isError ? (
        <p role="alert">Could not load the feedback.</p>
      ) : list.isPending ? null : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {show === "new" ? "No new feedback." : "No feedback here."}
        </p>
      ) : (
        <ul className="space-y-4">
          {shown.map((f) => {
            const { label, icon: Icon } = FEEDBACK_KINDS[f.kind] ?? FEEDBACK_KINDS.recommendation;
            return (
              <li
                key={f.id}
                aria-label={`${label} from ${f.created_by}`}
                className={cn("rounded-lg border bg-card p-4 shadow-xs", f.status === "done" && "opacity-70")}
              >
                <div className="flex flex-col gap-4 sm:flex-row">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={f.kind === "bug" ? "destructive" : "secondary"}>
                        <Icon aria-hidden />
                        {label}
                      </Badge>
                      {f.status === "done" && <Badge variant="outline">Done</Badge>}
                      <span className="text-xs text-muted-foreground">
                        {f.created_by} · {formatDateTime(f.created_at)}
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap text-sm">{f.message}</p>
                    <p className="space-x-2 text-xs text-muted-foreground">
                      {f.page && (
                        <Link to={f.page} className="underline underline-offset-4">
                          {f.page}
                        </Link>
                      )}
                      {f.viewport && <span>{f.viewport}</span>}
                      {f.user_agent && (
                        <span className="break-all" title={f.user_agent}>
                          {f.user_agent.length > 60 ? `${f.user_agent.slice(0, 60)}…` : f.user_agent}
                        </span>
                      )}
                    </p>
                  </div>
                  {f.has_screenshot && (
                    <a
                      href={`/v1/feedback/${f.id}/screenshot`}
                      target="_blank"
                      rel="noreferrer"
                      className="block shrink-0 sm:w-56"
                      title="Open the screenshot"
                    >
                      <img
                        src={`/v1/feedback/${f.id}/screenshot`}
                        alt={`Screenshot sent with feedback ${f.id}`}
                        loading="lazy"
                        className="max-h-36 w-full rounded-md border object-cover object-top"
                      />
                    </a>
                  )}
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={status.isPending}
                    onClick={() => status.mutate({ id: f.id, status: f.status === "new" ? "done" : "new" })}
                  >
                    {f.status === "new" ? "Mark done" : "Reopen"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDeleting(f)}>
                    Delete
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this feedback?</AlertDialogTitle>
            <AlertDialogDescription>
              It and its screenshot are removed for good.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id);
                setDeleting(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
