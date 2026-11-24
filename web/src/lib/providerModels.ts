import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";

export type ProviderModel = { id: string; name: string };
export type ProviderModels = { provider: string; models: ProviderModel[] };

/** Providers whose model list Jerome can fetch, by the name the list line uses. */
export const LIST_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google Gemini",
  mistral: "Mistral",
  openrouter: "OpenRouter",
  xai: "xAI",
  openai_compat: "Own server",
};

export const PROVIDER_MODELS_KEY = "provider-models";

export function providerModelsPath(provider: string, baseUrl?: string | null): string {
  const query = new URLSearchParams({ provider });
  if (baseUrl) query.set("base_url", baseUrl);
  return `/v1/settings/provider-models?${query}`;
}

export function fetchProviderModels(provider: string, baseUrl?: string | null) {
  return apiFetch<ProviderModels>(providerModelsPath(provider, baseUrl));
}

/** The provider's live list, fetched with the key stored on the server (never sent here). */
export function useProviderModels(provider: string, baseUrl: string | null, enabled: boolean) {
  return useQuery({
    queryKey: [PROVIDER_MODELS_KEY, provider, baseUrl],
    queryFn: () => fetchProviderModels(provider, baseUrl),
    enabled,
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
}

export function countModels(n: number): string {
  return `${n} ${n === 1 ? "model" : "models"}`;
}

export function listError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Could not load the models";
}
