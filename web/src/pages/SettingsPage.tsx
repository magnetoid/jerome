import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/hooks/useMe";
import ModelSettings from "@/components/settings/ModelSettings";
import PricesSettings from "@/components/settings/PricesSettings";
import { NumberSetting, UrlSetting } from "@/components/settings/SimpleSettings";
import SecretsSection from "@/components/settings/SecretsSection";
import ApiKeysSection from "@/components/settings/ApiKeysSection";
import { AuditSection, SystemSection } from "@/components/settings/SystemAndAudit";
import { SETTINGS_KEY, useForbiddenWatch, type SettingsBody } from "@/components/settings/shared";

const LIMITS: [string, string][] = [
  ["text_daily_cost_cap", "Daily text cost cap"],
  ["text_rpm", "Text requests per minute"],
  ["max_upload_mb", "Max upload (MB)"],
  ["session_days", "Session length (days)"],
];
const SERVICES: [string, string][] = [
  ["languagetool_url", "LanguageTool URL"],
  ["public_base_url", "Public base URL"],
];

function Heading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="text-lg font-semibold">
      {children}
    </h2>
  );
}

export default function SettingsPage() {
  const me = useMe().data;
  const isSuperAdmin = me?.role === "super_admin";
  const settings = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => apiFetch<SettingsBody>("/v1/settings"),
    enabled: isSuperAdmin,
  });
  useForbiddenWatch(settings.error);

  const data = settings.data;
  const row = (key: string) => data?.settings.find((s) => s.key === key);
  // Remount a control when its stored value changes so it shows what was saved or reset.
  const stamp = (key: string) => {
    const r = row(key);
    return `${key}:${r?.source}:${JSON.stringify(r?.value)}`;
  };

  return (
    <main className="w-full space-y-8 p-6">
      <h1 className="text-2xl font-semibold">Settings</h1>

      {me && !isSuperAdmin ? (
        <p className="text-muted-foreground">Only the super admin can change settings.</p>
      ) : settings.isError ? (
        <p role="alert">Could not load the settings.</p>
      ) : !data ? null : (
        <>
          <section className="space-y-3" aria-labelledby="h-ai">
            <Heading id="h-ai">AI model</Heading>
            <ModelSettings
              key={stamp("models.ai")}
              row={row("models.ai")!}
              label="Proofreading model"
              idPrefix="ai"
            />
            <ModelSettings
              key={stamp("models.translate")}
              row={row("models.translate")!}
              label="Translation model"
              idPrefix="translate"
            />
          </section>

          <section className="space-y-3" aria-labelledby="h-limits">
            <Heading id="h-limits">Limits</Heading>
            {LIMITS.map(([key, label]) => (
              <NumberSetting key={stamp(key)} row={row(key)!} label={label} />
            ))}
            <PricesSettings key={stamp("prices")} row={row("prices")!} />
          </section>

          <section className="space-y-3" aria-labelledby="h-services">
            <Heading id="h-services">Services</Heading>
            {SERVICES.map(([key, label]) => (
              <UrlSetting key={stamp(key)} row={row(key)!} label={label} />
            ))}
          </section>

          <SecretsSection secrets={data.secrets} system={data.system} />
          <ApiKeysSection />
          <SystemSection system={data.system} />
          <AuditSection />
        </>
      )}
    </main>
  );
}
