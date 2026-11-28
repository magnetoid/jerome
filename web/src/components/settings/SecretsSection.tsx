import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { countModels, fetchProviderModels, listError, PROVIDER_MODELS_KEY } from "@/lib/providerModels";
import { errorToast, SourceNote, useRefreshSettings, type SecretRow, type SystemInfo } from "./shared";

const LABELS: Record<string, string> = {
  anthropic_api_key: "Anthropic API key",
  webhook_secret: "Webhook secret",
  openai_compat_api_key: "Own server / other API key",
  openai_api_key: "OpenAI API key",
  gemini_api_key: "Google Gemini API key",
  mistral_api_key: "Mistral API key",
  openrouter_api_key: "OpenRouter API key",
  xai_api_key: "xAI (Grok) API key",
  deepl_api_key: "DeepL API key",
  languagetool_api_key: "LanguageTool Premium API key",
};

/** Keys of model providers: saving one fetches its model list to check it. The own-server key
 * is left out, since its URL is set under Models. */
const CHECKED: Record<string, string> = {
  anthropic_api_key: "anthropic",
  openai_api_key: "openai",
  gemini_api_key: "gemini",
  mistral_api_key: "mistral",
  openrouter_api_key: "openrouter",
  xai_api_key: "xai",
};
/** Every key a model list is fetched with: any change to one makes its cached lists stale. */
const LISTED: Record<string, string> = { ...CHECKED, openai_compat_api_key: "openai_compat" };

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
  const [check, setCheck] = useState<string | null>(null);
  const queryClient = useQueryClient();

  async function checkKey(provider: string) {
    setCheck("Checking the key…");
    try {
      const list = await fetchProviderModels(provider);
      setCheck(`Key works: ${countModels(list.models.length)}`);
    } catch (err) {
      setCheck(listError(err));
    }
    staleLists();
  }

  /** The model lists fetched with this key's old value. */
  function staleLists() {
    const provider = LISTED[row.key];
    if (provider) void queryClient.invalidateQueries({ queryKey: [PROVIDER_MODELS_KEY, provider] });
  }

  async function send(value: string | null) {
    setBusy(true);
    setCheck(null);
    let saved = false;
    try {
      await apiFetch(`/v1/settings/${row.key}`, { method: "PUT", json: { value } });
      setText("");
      toast.success(value === null ? "Cleared" : "Saved");
      saved = true;
    } catch (err) {
      errorToast(err);
    } finally {
      setBusy(false);
      void refresh();
    }
    const provider = CHECKED[row.key];
    if (saved && value !== null && provider) await checkKey(provider);
    else if (saved) staleLists();
  }

  async function reset() {
    setBusy(true);
    setCheck(null); // the check was of the value just removed
    try {
      await apiFetch(`/v1/settings/${row.key}`, { method: "DELETE" });
      setText("");
      toast.success("Reset to the fallback value");
      staleLists();
    } catch (err) {
      errorToast(err);
    } finally {
      setBusy(false);
      void refresh();
    }
  }

  return (
    <div role="group" aria-label={label} className="grid gap-2 rounded-lg border bg-card p-4 shadow-xs">
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
      {check && (
        <p role="status" className="text-sm text-muted-foreground">
          {check}
        </p>
      )}
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
