import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { countModels, LIST_LABELS, listError, useProviderModels } from "@/lib/providerModels";
import { SettingFrame, useSettingActions, type SettingRow } from "./shared";

type Ai = { provider?: string; model?: string; effort?: string; base_url?: string };
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

/** Providers in the order they are offered, with the name people know them by. */
export const PROVIDERS: { value: string; label: string; translateOnly?: boolean }[] = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Google Gemini" },
  { value: "mistral", label: "Mistral" },
  { value: "openrouter", label: "OpenRouter (open-weight models)" },
  { value: "xai", label: "xAI (Grok)" },
  { value: "openai_compat", label: "Own server / other (OpenAI-compatible)" },
  { value: "deepl", label: "DeepL", translateOnly: true },
];

/** Well-known model names to pick from; any other name can be typed. */
export const SUGGESTED_MODELS: Record<string, string[]> = {
  anthropic: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
  openai: ["gpt-5", "gpt-5-mini"],
  gemini: ["gemini-2.5-pro", "gemini-2.5-flash"],
  mistral: ["mistral-large-latest", "mistral-medium-latest"],
  openrouter: [
    "meta-llama/llama-3.3-70b-instruct",
    "qwen/qwen3-235b-a22b",
    "deepseek/deepseek-chat",
    "google/gemma-3-27b-it",
    "mistralai/mistral-small-3.1-24b-instruct",
  ],
  xai: ["grok-4", "grok-4-1-fast"],
  openai_compat: ["llama3.3", "qwen3", "gemma3"],
};

// Providers that use the effort setting (OpenAI maps low, medium and high).
const WITH_EFFORT = new Set(["anthropic", "openai"]);

export default function ModelSettings({
  row,
  label,
  idPrefix,
  allowDeepl = false,
  keyed = [],
}: {
  row: SettingRow;
  label: string;
  idPrefix: string;
  allowDeepl?: boolean;
  /** Providers whose API key is set: their live model list is fetched. */
  keyed?: string[];
}) {
  const { save, reset } = useSettingActions();
  const v = (row.value ?? {}) as Ai;
  const [provider, setProvider] = useState(v.provider ?? "anthropic");
  const [model, setModel] = useState(v.model ?? "");
  const [effort, setEffort] = useState(v.effort ?? "");
  const [baseUrl, setBaseUrl] = useState(v.base_url ?? "");
  const deepl = provider === "deepl";
  const own = provider === "openai_compat";
  // an own server lists from the URL it had when Load was pressed
  const [loadUrl, setLoadUrl] = useState<string | null>(null);
  const listable = provider in LIST_LABELS;
  const listing = listable && (own ? loadUrl !== null : keyed.includes(provider));
  const models = useProviderModels(provider, own ? loadUrl : null, listing);
  const live = listing ? models.data?.models ?? null : null;
  const suggestions = live?.length ? live.map((m) => m.id) : SUGGESTED_MODELS[provider] ?? [];
  const listLine = !listing
    ? null
    : models.isFetching
      ? "Loading models…"
      : models.error
        ? listError(models.error)
        : live
          ? `${countModels(live.length)} from ${LIST_LABELS[provider]}`
          : null;

  function loadOwn() {
    const url = baseUrl.trim();
    if (url === loadUrl) void models.refetch();
    else setLoadUrl(url);
  }

  // A model name meant for another provider would not work here: start empty instead.
  function changeProvider(next: string) {
    const name = model.trim();
    const known = [...Object.values(SUGGESTED_MODELS).flat(), ...(live ?? []).map((m) => m.id), "deepl"];
    if (known.includes(name) && !(SUGGESTED_MODELS[next] ?? []).includes(name)) {
      setModel("");
    }
    setProvider(next);
  }

  function onSave() {
    const value: Ai = { provider, model: deepl ? "deepl" : model.trim() };
    if (effort && WITH_EFFORT.has(provider)) value.effort = effort;
    if (provider === "openai_compat") value.base_url = baseUrl.trim();
    save.mutate({ key: row.key, value });
  }

  return (
    <SettingFrame
      label={label}
      row={row}
      busy={save.isPending || reset.isPending}
      onSave={onSave}
      onReset={() => reset.mutate(row.key)}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-provider`}>Provider</Label>
          <select
            id={`${idPrefix}-provider`}
            className={NATIVE_SELECT}
            value={provider}
            onChange={(e) => changeProvider(e.target.value)}
          >
            {PROVIDERS.filter((p) => allowDeepl || !p.translateOnly || p.value === provider).map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        {deepl ? (
          <p className="self-end text-sm text-muted-foreground">
            DeepL translates paragraph by paragraph and keeps formatting. It does not follow the
            house style (quote marks), so proofreading should run after it. Its key goes under
            Secrets.
          </p>
        ) : (
          <div className="grid gap-2">
            <Label htmlFor={`${idPrefix}-model`}>Model</Label>
            <Input
              id={`${idPrefix}-model`}
              list={`${idPrefix}-models`}
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id={`${idPrefix}-models`}>
              {suggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            {(listLine || (listing && !own)) && (
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                {listLine && <span role="status">{listLine}</span>}
                {listing && !own && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={models.isFetching}
                    onClick={() => void models.refetch()}
                  >
                    Refresh
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
        {WITH_EFFORT.has(provider) && (
          <div className="grid gap-2">
            <Label htmlFor={`${idPrefix}-effort`}>Effort</Label>
            <select
              id={`${idPrefix}-effort`}
              className={NATIVE_SELECT}
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
            >
              <option value="">Default</option>
              {EFFORTS.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </select>
          </div>
        )}
        {provider === "openai_compat" && (
          <div className="grid gap-2">
            <Label htmlFor={`${idPrefix}-base-url`}>Base URL</Label>
            <Input
              id={`${idPrefix}-base-url`}
              type="url"
              placeholder="http://localhost:11434/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="justify-self-start"
              disabled={!baseUrl.trim() || models.isFetching}
              onClick={loadOwn}
            >
              Load models
            </Button>
          </div>
        )}
      </div>
    </SettingFrame>
  );
}
