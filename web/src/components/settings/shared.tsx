import { useEffect, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch } from "@/lib/api";
import { ME_KEY } from "@/hooks/useMe";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export const SETTINGS_KEY = ["settings"] as const;
export const AUDIT_KEY = ["settings-audit"] as const;
export const KEYS_KEY = ["api-keys"] as const;

export type Source = "default" | "environment" | "app";
export type SettingRow = {
  key: string;
  value: unknown;
  source: Source;
  updated_by: string | null;
  updated_at: string | null;
  unreadable: boolean;
};
export type SecretRow = {
  key: string;
  is_set: boolean;
  source: Source;
  updated_by: string | null;
  updated_at: string | null;
  unreadable: boolean;
};
export type SystemInfo = {
  database_url: string;
  data_dir: string;
  web_dir: string | null;
  trusted_proxies: string[];
  cookie_secure: boolean;
  secret_storage: "ready" | "missing" | "invalid";
};
export type SettingsBody = { settings: SettingRow[]; secrets: SecretRow[]; system: SystemInfo };

export const SECRET_KEYS = ["anthropic_api_key", "webhook_secret", "openai_compat_api_key"];

export function errorToast(err: unknown) {
  toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
}

export function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export { formatDateTime } from "@/lib/format";

/** Re-read everything the Settings page shows. */
export function useRefreshSettings() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: SETTINGS_KEY }),
      client.invalidateQueries({ queryKey: AUDIT_KEY }),
      client.invalidateQueries({ queryKey: KEYS_KEY }),
    ]);
}

/** A 403 "super admin only" means my role changed: re-read who I am. */
export function useForbiddenWatch(error: unknown) {
  const client = useQueryClient();
  const forbidden = error instanceof ApiError && error.detail === "super admin only";
  useEffect(() => {
    if (forbidden) client.invalidateQueries({ queryKey: ME_KEY });
  }, [forbidden, client]);
}

/** Save and reset for non-secret settings. Secrets never go through react-query. */
export function useSettingActions() {
  const refresh = useRefreshSettings();
  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) =>
      apiFetch<SettingsBody>(`/v1/settings/${key}`, { method: "PUT", json: { value } }),
    onSuccess: () => toast.success("Saved"),
    onError: errorToast,
    onSettled: refresh,
  });
  const reset = useMutation({
    mutationFn: (key: string) => apiFetch<void>(`/v1/settings/${key}`, { method: "DELETE" }),
    onSuccess: () => toast.success("Reset to the fallback value"),
    onError: errorToast,
    onSettled: refresh,
  });
  return { save, reset };
}

const SOURCE_LABEL: Record<Source, string> = {
  default: "Default",
  environment: "Environment",
  app: "App",
};

export function SourceNote({
  row,
}: {
  row: { source: Source; updated_by: string | null; updated_at: string | null; unreadable: boolean };
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <Badge variant={row.source === "app" ? "default" : "outline"}>
        {SOURCE_LABEL[row.source]}
      </Badge>
      {row.source === "app" && row.updated_by && (
        <span>
          Changed by {row.updated_by} on {formatDate(row.updated_at)}
        </span>
      )}
      {row.unreadable && <span>Can't be read; using the fallback</span>}
    </div>
  );
}

/** One setting: its control, where the value comes from, Save and (for app values) Reset. */
export function SettingFrame({
  label,
  row,
  children,
  onSave,
  onReset,
  busy,
  saveLabel = "Save",
}: {
  label: string;
  row: SettingRow;
  children: ReactNode;
  onSave: () => void;
  onReset: () => void;
  busy: boolean;
  saveLabel?: string;
}) {
  return (
    <div role="group" aria-label={label} className="grid gap-2 rounded-lg border p-4">
      <div className="font-medium">{label}</div>
      {children}
      <SourceNote row={row} />
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={onSave}>
          {saveLabel}
        </Button>
        {row.source === "app" && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onReset}>
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}
