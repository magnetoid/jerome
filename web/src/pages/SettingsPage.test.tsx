import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import type { QueryClient } from "@tanstack/react-query";
import SettingsPage from "./SettingsPage";
import { ME, MEMBER, json, mockApi, renderApp } from "@/test-utils";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const row = (key: string, value: unknown, source = "default", extra = {}) => ({
  key, value, source, updated_by: null, updated_at: null, unreadable: false, ...extra,
});
const secret = (key: string, is_set: boolean, source = "default") => ({
  key, is_set, source, updated_by: null, updated_at: null, unreadable: false,
});

function body(over: { storage?: string; settings?: unknown[]; secrets?: unknown[] } = {}) {
  return {
    settings: over.settings ?? [
      row("models.ai", { provider: "anthropic", model: "claude-opus-5-5" }),
      row("models.translate", { provider: "anthropic", model: "claude-opus-5-5", effort: "high" }),
      row("prices", {
        "model-a": { input: 1, output: 2, cache_read: 0.5, cache_write: 1.5 },
        "model-b": { input: 3, output: 4, cache_read: 1, cache_write: 2 },
      }),
      row("text_daily_cost_cap", "5.00"),
      row("text_rpm", 30),
      row("max_upload_mb", 50, "app", { updated_by: "ann@example.com", updated_at: "2026-11-20T10:00:00Z" }),
      row("session_days", 14),
      row("languagetool_url", "http://localhost:8010"),
      row("languagetool_username", null),
      row("public_base_url", null),
    ],
    secrets: over.secrets ?? [
      secret("anthropic_api_key", true, "app"),
      secret("webhook_secret", false),
      secret("openai_compat_api_key", false),
    ],
    system: {
      database_url: "postgresql+psycopg://jerome:***@db.example.com/jerome",
      data_dir: "/data", web_dir: null, trusted_proxies: [], cookie_secure: true,
      secret_storage: over.storage ?? "ready",
    },
  };
}

function cachedText(client: QueryClient): string {
  const q = client.getQueryCache().getAll().map((x) => x.state.data);
  const m = client.getMutationCache().getAll().map((x) => [x.state.data, x.state.variables]);
  return JSON.stringify([q, m]);
}

beforeEach(() => vi.mocked(toast.error).mockClear());
afterEach(() => vi.unstubAllGlobals());

function setup(extra: Parameters<typeof mockApi>[0] = {}, settings = body()) {
  const f = mockApi({
    "GET /v1/auth/me": json(200, ME),
    "GET /v1/settings": () => json(200, settings),
    "GET /v1/settings/audit": json(200, [
      { id: 1, at: "2026-11-20T10:00:00Z", actor: "ann@example.com", key: "text_rpm", action: "set", before: 30, after: 12 },
      { id: 2, at: "2026-11-20T11:00:00Z", actor: "ann@example.com", key: "webhook_secret", action: "replaced", before: null, after: null },
    ]),
    "GET /v1/api-keys": json(200, [
      { id: 1, name: "ci", created_at: "2026-11-01T10:00:00Z", revoked_at: null },
      { id: 2, name: "old", created_at: "2026-10-01T10:00:00Z", revoked_at: "2026-11-02T10:00:00Z" },
    ]),
    "GET /v1/learned-words": json(200, [
      {
        id: 7, language: "de", word: "Katharina", rule: "*", reason: "A name in the story.", book_id: 3,
        created_by: "ann@example.com", created_at: "2026-11-25T10:00:00Z",
      },
      {
        id: 8, language: "en", word: "gonna", rule: "INFORMAL", reason: null, book_id: null,
        created_by: "bo@example.com", created_at: "2026-11-24T10:00:00Z",
      },
    ]),
    // the Anthropic key is set in body(), so the Claude model fields ask for its list
    "GET /v1/settings/provider-models?provider=anthropic": json(200, { provider: "anthropic", models: [] }),
    ...extra,
  });
  const view = renderApp(<SettingsPage />);
  return { f, ...view };
}

const call = (f: ReturnType<typeof mockApi>, method: string, path: string) =>
  f.mock.calls.find(([p, i]) => p === path && (i?.method ?? "GET") === method);

describe("SettingsPage", () => {
  it("shows the member message without fetching", async () => {
    const f = mockApi({ "GET /v1/auth/me": json(200, MEMBER) });
    renderApp(<SettingsPage />);
    expect(await screen.findByText("Only the super admin can change settings.")).toBeInTheDocument();
    expect(f.mock.calls.some(([p]) => String(p).startsWith("/v1/settings"))).toBe(false);
    expect(f.mock.calls.some(([p]) => p === "/v1/api-keys")).toBe(false);
  });

  it("shows the sections in order", async () => {
    setup();
    await screen.findByRole("heading", { name: "Models" });
    const names = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(names).toEqual(["Models", "Limits", "Services", "Secrets", "Learned words", "API keys", "System", "Recent changes"]);
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("shows sources and saves a limit as a number", async () => {
    const f = setup({ "PUT /v1/settings/text_rpm": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const rpm = (await screen.findByRole("group", { name: "Text requests per minute" }));
    expect(within(rpm).getByText("Default")).toBeInTheDocument();
    const upload = screen.getByRole("group", { name: "Max upload (MB)" });
    expect(within(upload).getByText("App")).toBeInTheDocument();
    expect(within(upload).getByText(/Changed by ann@example.com on/)).toBeInTheDocument();

    const input = within(rpm).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "12");
    await user.click(within(rpm).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/text_rpm")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/text_rpm")![1]!.body as string)).toEqual({ value: 12 });
  });

  it("resets only app values", async () => {
    const f = setup({ "DELETE /v1/settings/max_upload_mb": json(204) }).f;
    const user = userEvent.setup();
    const rpm = await screen.findByRole("group", { name: "Text requests per minute" });
    expect(within(rpm).queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
    const upload = screen.getByRole("group", { name: "Max upload (MB)" });
    await user.click(within(upload).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(call(f, "DELETE", "/v1/settings/max_upload_mb")).toBeTruthy());
  });

  it("flags a value that can't be read", async () => {
    const s = body();
    const at = s.settings.findIndex((r) => (r as { key: string }).key === "text_rpm");
    s.settings[at] = row("text_rpm", 30, "default", { unreadable: true });
    setup({}, s);
    const rpm = await screen.findByRole("group", { name: "Text requests per minute" });
    expect(within(rpm).getByText("Can't be read; using the fallback")).toBeInTheDocument();
  });

  it("secret input is cleared after save and never prefilled", async () => {
    const { f, client } = setup({ "PUT /v1/settings/anthropic_api_key": () => json(200, body()) });
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Anthropic API key" });
    expect(within(group).getByText("Set (app)")).toBeInTheDocument();
    const input = within(group).getByLabelText("New Anthropic API key") as HTMLInputElement;
    expect(input.value).toBe("");
    await user.type(input, "sk-test-value");
    await user.click(within(group).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(input.value).toBe(""));
    expect(JSON.parse(call(f, "PUT", "/v1/settings/anthropic_api_key")![1]!.body as string)).toEqual({
      value: "sk-test-value",
    });
    expect(cachedText(client)).not.toContain("sk-test-value");
  });

  it("clears a secret with null after a confirm", async () => {
    const f = setup({ "PUT /v1/settings/anthropic_api_key": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Anthropic API key" });
    await user.click(within(group).getByRole("button", { name: "Clear" }));
    expect(call(f, "PUT", "/v1/settings/anthropic_api_key")).toBeUndefined();
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/anthropic_api_key")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/anthropic_api_key")![1]!.body as string)).toEqual({ value: null });
  });

  it("resets a secret that has an app value with DELETE", async () => {
    const f = setup({ "DELETE /v1/settings/anthropic_api_key": () => new Response(null, { status: 204 }) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Anthropic API key" });
    expect(within(await screen.findByRole("group", { name: "Webhook secret" })).queryByRole("button", { name: "Reset" })).toBeNull();
    await user.click(within(group).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(call(f, "DELETE", "/v1/settings/anthropic_api_key")).toBeTruthy());
    await waitFor(() => expect(call(f, "GET", "/v1/settings/audit")).toBeTruthy());
  });

  it("shows an empty change cell when before and after are both null", async () => {
    setup({
      "GET /v1/settings/audit": json(200, [
        { id: 9, at: "2026-11-20T10:00:00Z", actor: "ann@example.com", key: "api_key:ci", action: "created", before: null, after: null },
      ]),
    });
    await screen.findByText("api_key:ci");
    expect(screen.queryByText("none → none")).toBeNull();
  });

  it("disables secrets when storage is off", async () => {
    setup({}, body({ storage: "missing" }));
    expect(
      await screen.findByText("Secret storage is off. Set JEROME_SECRET_KEY on the server, then reload."),
    ).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Anthropic API key" });
    expect(within(group).getByLabelText("New Anthropic API key")).toBeDisabled();
    expect(within(group).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(within(group).getByRole("button", { name: "Clear" })).toBeEnabled();
    expect(screen.getByText("Not set", { selector: "dd" })).toBeInTheDocument();
  });

  it("creates a key and shows it once", async () => {
    const created = { id: 3, name: "bot", created_at: "2026-11-23T10:00:00Z", revoked_at: null };
    const { client } = setup({
      "POST /v1/api-keys": () => json(201, { key: created, token: "jrm_tok_abc123" }),
    });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Key name"), "bot");
    await user.click(screen.getByRole("button", { name: "Create key" }));
    const dialog = await screen.findByRole("dialog", { name: "New API key" });
    expect(within(dialog).getByText("jrm_tok_abc123")).toBeInTheDocument();
    expect(within(dialog).getByText("Copy this key now. It won't be shown again.")).toBeInTheDocument();
    expect(cachedText(client)).not.toContain("jrm_tok_abc123");
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("jrm_tok_abc123")).not.toBeInTheDocument());
  });

  it("lists keys and revokes behind a confirm", async () => {
    const f = setup({
      "DELETE /v1/api-keys/1": () => json(200, { id: 1, name: "ci", created_at: "2026-11-01T10:00:00Z", revoked_at: "2026-11-23T10:00:00Z" }),
    }).f;
    const user = userEvent.setup();
    const ci = (await screen.findByText("ci")).closest("tr")!;
    expect(within(ci).getByText("Active")).toBeInTheDocument();
    const old = screen.getByText("old").closest("tr")!;
    expect(within(old).getByText(/^Revoked/)).toBeInTheDocument();
    expect(within(old).queryByRole("button", { name: /Revoke/ })).not.toBeInTheDocument();
    await user.click(within(ci).getByRole("button", { name: "Revoke ci" }));
    expect(call(f, "DELETE", "/v1/api-keys/1")).toBeUndefined();
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(call(f, "DELETE", "/v1/api-keys/1")).toBeTruthy());
  });

  it("lists learned words and deletes one behind a confirm", async () => {
    const f = setup({ "DELETE /v1/learned-words/7": () => new Response(null, { status: 204 }) }).f;
    const user = userEvent.setup();
    const section = await screen.findByRole("region", { name: "Learned words" });
    const kat = (await within(section).findByText("Katharina")).closest("tr")!;
    expect(kat).toHaveTextContent("German");
    expect(kat).toHaveTextContent("any spelling rule");
    expect(kat).toHaveTextContent("A name in the story.");
    expect(kat).toHaveTextContent("ann@example.com");
    const gonna = within(section).getByText("gonna").closest("tr")!;
    expect(gonna).toHaveTextContent("English");
    expect(gonna).toHaveTextContent("INFORMAL");
    await user.click(within(kat).getByRole("button", { name: "Delete Katharina" }));
    expect(call(f, "DELETE", "/v1/learned-words/7")).toBeUndefined();
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(call(f, "DELETE", "/v1/learned-words/7")).toBeTruthy());
  });

  it("saves the whole prices table", async () => {
    const f = setup({ "PUT /v1/settings/prices": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const input = await screen.findByLabelText("model-a output");
    await user.clear(input);
    await user.type(input, "9");
    await user.click(screen.getByRole("button", { name: "Save prices" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/prices")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/prices")![1]!.body as string)).toEqual({
      value: {
        "model-a": { input: 1, output: 9, cache_read: 0.5, cache_write: 1.5 },
        "model-b": { input: 3, output: 4, cache_read: 1, cache_write: 2 },
      },
    });
  });

  it("saves the AI model as one value and shows base URL only for openai_compat", async () => {
    const f = setup({ "PUT /v1/settings/models.ai": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Proofreading model" });
    expect(within(group).queryByLabelText("Base URL")).not.toBeInTheDocument();
    await user.selectOptions(within(group).getByLabelText("Provider"), "openai_compat");
    await user.type(await within(group).findByLabelText("Base URL"), "https://llm.example.com/v1");
    expect(within(group).queryByLabelText("Effort")).not.toBeInTheDocument(); // only Claude and OpenAI
    expect(within(group).getByLabelText("Model")).toHaveValue(""); // a Claude name would not work there
    await user.type(within(group).getByLabelText("Model"), "llama3.3");
    await user.click(within(group).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/models.ai")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/models.ai")![1]!.body as string)).toEqual({
      value: { provider: "openai_compat", model: "llama3.3", base_url: "https://llm.example.com/v1" },
    });
  });

  it("offers the presets with model suggestions, and DeepL only for translation", async () => {
    const f = setup({
      "PUT /v1/settings/models.ai": () => json(200, body()),
      "PUT /v1/settings/models.translate": () => json(200, body()),
    }).f;
    const user = userEvent.setup();
    const ai = await screen.findByRole("group", { name: "Proofreading model" });
    const aiProviders = [...within(ai).getByLabelText("Provider").querySelectorAll("option")].map((o) => o.value);
    expect(aiProviders).toEqual(["anthropic", "openai", "gemini", "mistral", "openrouter", "xai", "openai_compat"]);
    await user.selectOptions(within(ai).getByLabelText("Provider"), "openrouter");
    const list = document.getElementById(within(ai).getByLabelText("Model").getAttribute("list")!)!;
    expect([...list.querySelectorAll("option")].map((o) => o.value)).toContain("qwen/qwen3-235b-a22b");
    await user.type(within(ai).getByLabelText("Model"), "deepseek/deepseek-chat");
    await user.click(within(ai).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/models.ai")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/models.ai")![1]!.body as string)).toEqual({
      value: { provider: "openrouter", model: "deepseek/deepseek-chat" },
    });

    const tr = screen.getByRole("group", { name: "Translation model" });
    await user.selectOptions(within(tr).getByLabelText("Provider"), "deepl");
    expect(within(tr).queryByLabelText("Model")).not.toBeInTheDocument();
    expect(within(tr).queryByLabelText("Effort")).not.toBeInTheDocument();
    await user.click(within(tr).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/models.translate")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/models.translate")![1]!.body as string)).toEqual({
      value: { provider: "deepl", model: "deepl" },
    });
    // the hidden model name "deepl" does not carry over to another provider
    await user.selectOptions(within(tr).getByLabelText("Provider"), "openai");
    expect(within(tr).getByLabelText("Model")).toHaveValue("");
  });

  it("saves the LanguageTool Premium username", async () => {
    const f = setup({ "PUT /v1/settings/languagetool_username": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "LanguageTool Premium username" });
    await user.type(within(group).getByLabelText("LanguageTool Premium username"), " ana@x.org ");
    await user.click(within(group).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/languagetool_username")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/languagetool_username")![1]!.body as string)).toEqual({
      value: "ana@x.org",
    });
  });

  it("renders the translation model editor and saves models.translate", async () => {
    const f = setup({ "PUT /v1/settings/models.translate": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "Translation model" });
    expect(within(group).getByLabelText("Model")).toHaveValue("claude-opus-5-5");
    expect(within(group).getByLabelText("Effort")).toHaveValue("high");
    await user.clear(within(group).getByLabelText("Model"));
    await user.type(within(group).getByLabelText("Model"), "claude-sonnet-5-5");
    await user.click(within(group).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/models.translate")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/models.translate")![1]!.body as string)).toEqual({
      value: { provider: "anthropic", model: "claude-sonnet-5-5", effort: "high" },
    });
    expect(call(f, "PUT", "/v1/settings/models.ai")).toBeFalsy();
  });

  it("saves an empty service URL as null", async () => {
    const f = setup({ "PUT /v1/settings/languagetool_url": () => json(200, body()) }).f;
    const user = userEvent.setup();
    const group = await screen.findByRole("group", { name: "LanguageTool URL" });
    await user.clear(within(group).getByRole("textbox"));
    await user.click(within(group).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(call(f, "PUT", "/v1/settings/languagetool_url")).toBeTruthy());
    expect(JSON.parse(call(f, "PUT", "/v1/settings/languagetool_url")![1]!.body as string)).toEqual({ value: null });
  });

  it("shows the audit without secret values and toasts a save error", async () => {
    setup({
      "PUT /v1/settings/text_rpm": () => json(400, { detail: "must be a whole number from 1 to 10000" }),
    });
    const user = userEvent.setup();
    expect(await screen.findByText("30 → 12")).toBeInTheDocument();
    const rpm = screen.getByRole("group", { name: "Text requests per minute" });
    await user.click(within(rpm).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("must be a whole number from 1 to 10000"));
  });

  describe("provider model lists", () => {
    const withOpenAiKey = () =>
      body({
        secrets: [
          secret("anthropic_api_key", true, "app"),
          secret("openai_api_key", true, "app"),
          secret("mistral_api_key", false),
          secret("webhook_secret", false),
        ],
      });
    const OPENAI_LIST = "GET /v1/settings/provider-models?provider=openai";
    const openAiModels = () =>
      json(200, { provider: "openai", models: [{ id: "gpt-live-a", name: "gpt-live-a" }, { id: "gpt-live-b", name: "gpt-live-b" }] });
    const suggestions = (group: HTMLElement) => {
      const list = document.getElementById(within(group).getByLabelText("Model").getAttribute("list")!)!;
      return [...list.querySelectorAll("option")].map((o) => o.value);
    };
    const listCalls = (f: ReturnType<typeof mockApi>, provider: string) =>
      f.mock.calls.filter(([p]) => String(p).startsWith(`/v1/settings/provider-models?provider=${provider}`)).length;

    it("a provider with a key offers its live models, with Refresh", async () => {
      const { f } = setup({ [OPENAI_LIST]: openAiModels }, withOpenAiKey());
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "openai");
      expect(await within(ai).findByText("2 models from OpenAI")).toBeInTheDocument();
      expect(suggestions(ai)).toEqual(["gpt-live-a", "gpt-live-b"]);
      await user.click(within(ai).getByRole("button", { name: "Refresh" }));
      await waitFor(() => expect(listCalls(f, "openai")).toBe(2));
    });

    it("without a key keeps the fixed suggestions and asks nothing", async () => {
      const { f } = setup({}, withOpenAiKey());
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "mistral");
      expect(suggestions(ai)).toContain("mistral-large-latest");
      expect(listCalls(f, "mistral")).toBe(0);
      expect(within(ai).queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument();
    });

    it("switching provider drops the old list", async () => {
      setup({ [OPENAI_LIST]: openAiModels }, withOpenAiKey());
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "openai");
      await within(ai).findByText("2 models from OpenAI");
      await user.selectOptions(within(ai).getByLabelText("Provider"), "mistral");
      expect(suggestions(ai)).not.toContain("gpt-live-a");
      expect(within(ai).queryByText("2 models from OpenAI")).not.toBeInTheDocument();
    });

    it("shows why a list could not be loaded", async () => {
      setup({ [OPENAI_LIST]: () => json(502, { detail: "OpenAI refused the key" }) }, withOpenAiKey());
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "openai");
      expect(await within(ai).findByText("OpenAI refused the key")).toBeInTheDocument();
      expect(suggestions(ai)).toContain("gpt-5"); // the fixed ones stay
    });

    it("an own server lists after Load, from the typed URL", async () => {
      const path =
        "/v1/settings/provider-models?provider=openai_compat&base_url=https%3A%2F%2Fllm.example.com%2Fv1";
      const { f } = setup({ [`GET ${path}`]: json(200, { provider: "openai_compat", models: [{ id: "qwen3", name: "qwen3" }] }) });
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "openai_compat");
      expect(listCalls(f, "openai_compat")).toBe(0);
      await user.type(within(ai).getByLabelText("Base URL"), "https://llm.example.com/v1");
      await user.click(within(ai).getByRole("button", { name: "Load models" }));
      expect(await within(ai).findByText("1 model from Own server")).toBeInTheDocument();
      expect(suggestions(ai)).toEqual(["qwen3"]);
    });

    it("saving a provider key checks it at once", async () => {
      const { f } = setup(
        {
          "PUT /v1/settings/openai_api_key": () => json(200, withOpenAiKey()),
          "PUT /v1/settings/webhook_secret": () => json(200, withOpenAiKey()),
          [OPENAI_LIST]: openAiModels,
        },
        withOpenAiKey(),
      );
      const user = userEvent.setup();
      const group = await screen.findByRole("group", { name: "OpenAI API key" });
      await user.type(within(group).getByLabelText("New OpenAI API key"), "sk-new");
      await user.click(within(group).getByRole("button", { name: "Save" }));
      expect(await within(group).findByText("Key works: 2 models")).toBeInTheDocument();

      const hook = screen.getByRole("group", { name: "Webhook secret" });
      await user.type(within(hook).getByLabelText("New Webhook secret"), "whsec");
      await user.click(within(hook).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(call(f, "PUT", "/v1/settings/webhook_secret")).toBeTruthy());
      expect(within(hook).queryByText(/Key works/)).not.toBeInTheDocument();
    });

    it("resetting a provider key reloads its models and drops the key check", async () => {
      const { f } = setup(
        {
          "PUT /v1/settings/openai_api_key": () => json(200, withOpenAiKey()),
          "DELETE /v1/settings/openai_api_key": () => new Response(null, { status: 204 }),
          [OPENAI_LIST]: openAiModels,
        },
        withOpenAiKey(),
      );
      const user = userEvent.setup();
      const ai = await screen.findByRole("group", { name: "Proofreading model" });
      await user.selectOptions(within(ai).getByLabelText("Provider"), "openai");
      await within(ai).findByText("2 models from OpenAI");
      const group = screen.getByRole("group", { name: "OpenAI API key" });
      await user.type(within(group).getByLabelText("New OpenAI API key"), "sk-new");
      await user.click(within(group).getByRole("button", { name: "Save" }));
      expect(await within(group).findByText("Key works: 2 models")).toBeInTheDocument();
      const before = listCalls(f, "openai");
      await user.click(within(group).getByRole("button", { name: "Reset" }));
      await waitFor(() => expect(call(f, "DELETE", "/v1/settings/openai_api_key")).toBeTruthy());
      await waitFor(() => expect(listCalls(f, "openai")).toBeGreaterThan(before));
      expect(within(group).queryByText(/Key works/)).not.toBeInTheDocument();
    });

    it("a refused key says so after saving", async () => {
      setup(
        {
          "PUT /v1/settings/openai_api_key": () => json(200, withOpenAiKey()),
          [OPENAI_LIST]: () => json(502, { detail: "OpenAI refused the key" }),
        },
        withOpenAiKey(),
      );
      const user = userEvent.setup();
      const group = await screen.findByRole("group", { name: "OpenAI API key" });
      await user.type(within(group).getByLabelText("New OpenAI API key"), "sk-bad");
      await user.click(within(group).getByRole("button", { name: "Save" }));
      expect(await within(group).findByText("OpenAI refused the key")).toBeInTheDocument();
    });
  });
});
