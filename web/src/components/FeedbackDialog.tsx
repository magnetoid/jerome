import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { BugIcon, LightbulbIcon, SparklesIcon, type LucideIcon } from "lucide-react";
import { ApiError, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type FeedbackKind = "bug" | "feature" | "recommendation";

export const FEEDBACK_KINDS: Record<FeedbackKind, { label: string; icon: LucideIcon; hint: string }> = {
  bug: {
    label: "Bug",
    icon: BugIcon,
    hint: "What happened, and what did you expect to happen?",
  },
  feature: {
    label: "Feature",
    icon: SparklesIcon,
    hint: "What would you like Jerome to do?",
  },
  recommendation: {
    label: "Recommendation",
    icon: LightbulbIcon,
    hint: "What would make Jerome better or easier to use?",
  },
};

const MAX = 5000;

/** The feedback form: a kind, a message and the screenshot taken when it was opened. */
export default function FeedbackDialog({
  open,
  onOpenChange,
  screenshot,
  page,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  screenshot: Blob | null;
  page: string;
}) {
  const [kind, setKind] = useState<FeedbackKind>("bug");
  const [message, setMessage] = useState("");
  const [attach, setAttach] = useState(true);
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => {
    if (!screenshot) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(screenshot);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [screenshot]);

  useEffect(() => {
    if (open) {
      setKind("bug");
      setMessage("");
      setAttach(true);
    }
  }, [open]);

  const send = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.set("kind", kind);
      form.set("message", message.trim());
      form.set("page", page);
      form.set("viewport", `${window.innerWidth}x${window.innerHeight}`);
      form.set("user_agent", navigator.userAgent);
      if (attach && screenshot) form.set("screenshot", screenshot, "screenshot.png");
      return apiFetch("/v1/feedback", { method: "POST", body: form });
    },
    onSuccess: () => {
      toast.success("Thank you! Your feedback was sent.");
      onOpenChange(false);
    },
    onError: (err) => toast.error(err instanceof ApiError ? err.detail : "Could not send the feedback"),
  });

  const ready = message.trim().length > 0 && message.length <= MAX && !send.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Send feedback</DialogTitle>
          <DialogDescription>
            Found a bug, or have an idea? Tell us, and we will use it to improve Jerome.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) send.mutate();
          }}
        >
          <div role="radiogroup" aria-label="Kind of feedback" className="grid grid-cols-3 gap-2">
            {(Object.keys(FEEDBACK_KINDS) as FeedbackKind[]).map((k) => {
              const { label, icon: Icon } = FEEDBACK_KINDS[k];
              const on = kind === k;
              return (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => setKind(k)}
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-md border px-2 py-3 text-xs font-medium transition-colors",
                    on
                      ? "border-primary bg-primary/5 text-foreground ring-1 ring-primary"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" aria-hidden />
                  {label}
                </button>
              );
            })}
          </div>
          <div className="space-y-2">
            <Label htmlFor="feedback-message">Your message</Label>
            <Textarea
              id="feedback-message"
              rows={5}
              autoFocus
              value={message}
              maxLength={MAX}
              placeholder={FEEDBACK_KINDS[kind].hint}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && ready) {
                  e.preventDefault();
                  send.mutate();
                }
              }}
            />
          </div>
          {preview ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="feedback-attach"
                  checked={attach}
                  onCheckedChange={(v) => setAttach(v === true)}
                />
                <Label htmlFor="feedback-attach">Attach a screenshot of this page</Label>
              </div>
              <img
                src={preview}
                alt="Screenshot of the page"
                className={cn("max-h-40 w-full rounded-md border object-cover object-top", !attach && "opacity-40")}
              />
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No screenshot could be taken of this page.</p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready}>
              {send.isPending ? "Sending…" : "Send feedback"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
