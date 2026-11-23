import { useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { errorToast, SourceNote, useRefreshSettings, type SecretRow, type SystemInfo } from "./shared";

const LABELS: Record<string, string> = {
  anthropic_api_key: "Anthropic API key",
  webhook_secret: "Webhook secret",
  openai_compat_api_key: "OpenAI-compatible API key",
};

function statusOf(row: SecretRow): string {
  if (row.unreadable) return "Can't be read, enter again";
  if (!row.is_set) return "Not set";
  return row.source === "app" ? "Set (app)" : "Set (environment)";
}

/**
 * The typed secret lives only in this component's state and in the request body. It is sent
 * with a plain fetch, not a react-query mutation, so it never lands in a query or mutation cache.
 */
function SecretItem({ row, storageReady }: { row: SecretRow; storageReady: boolean }) {
  const refresh = useRefreshSettings();
  const label = LABELS[row.key] ?? row.key;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function send(value: string | null) {
    setBusy(true);
    try {
      await apiFetch(`/v1/settings/${row.key}`, { method: "PUT", json: { value } });
      setText("");
      toast.success(value === null ? "Cleared" : "Saved");
    } catch (err) {
      errorToast(err);
    } finally {
      setBusy(false);
      void refresh();
    }
  }

  async function reset() {
    setBusy(true);
    try {
      await apiFetch(`/v1/settings/${row.key}`, { method: "DELETE" });
      setText("");
      toast.success("Reset to the fallback value");
    } catch (err) {
      errorToast(err);
    } finally {
      setBusy(false);
      void refresh();
    }
  }

  return (
    <div role="group" aria-label={label} className="grid gap-2 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label}</span>
        <span className="text-sm text-muted-foreground">{statusOf(row)}</span>
      </div>
      <Input
        type="password"
        autoComplete="new-password"
        aria-label={`New ${label}`}
        value={text}
        disabled={!storageReady}
        onChange={(e) => setText(e.target.value)}
      />
      <SourceNote row={{ ...row, unreadable: false }} />
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!storageReady || busy || text === ""}
          onClick={() => void send(text)}
        >
          Save
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          Clear
        </Button>
        {row.source === "app" && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void reset()}>
            Reset
          </Button>
        )}
      </div>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear {label}?</AlertDialogTitle>
            <AlertDialogDescription>
              The stored value is removed and Jerome stops using it, even if the environment
              provides one.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void send(null)}>Clear</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function SecretsSection({
  secrets,
  system,
}: {
  secrets: SecretRow[];
  system: SystemInfo;
}) {
  const ready = system.secret_storage === "ready";
  return (
    <section className="space-y-3" aria-labelledby="h-secrets">
      <h2 id="h-secrets" className="text-lg font-semibold">
        Secrets
      </h2>
      {!ready && (
        <p role="status" className="text-sm text-muted-foreground">
          Secret storage is off. Set JEROME_SECRET_KEY on the server, then reload.
        </p>
      )}
      {secrets.map((s) => (
        <SecretItem key={s.key} row={s} storageReady={ready} />
      ))}
    </section>
  );
}
