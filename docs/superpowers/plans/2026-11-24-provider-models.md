# Provider Model List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jerome fetches a provider's model list with the stored key, for the Secrets and Models parts of Settings.

**Architecture:** A small lister module calls each provider's list endpoint with httpx. A super-admin route reads the effective key from the settings overlay and returns the list. The web asks that route after a key is saved and while the Model field is shown.

**Tech Stack:** Python 3.12, httpx, respx, FastAPI; React, react-query, vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-provider-models-design.md`

## Global Constraints

- Route: `GET /v1/settings/provider-models?provider=<name>[&base_url=<url>]`, super admin only. Reply `{"provider": str, "models": [{"id": str, "name": str}]}`, sorted by id.
- Providers listed: `anthropic`, `openai`, `gemini`, `mistral`, `openrouter`, `xai`, `openai_compat`. `deepl` and any other name are a 400.
- HTTP 409 when the key is not set (`openai_compat` needs no key). HTTP 400 for a bad provider or base URL. HTTP 502 when the provider fails.
- Limits: 10 s timeout, no redirects, a reply over 5 MB is refused, Anthropic at most 20 pages.
- A key never appears in a reply, an error message or a log line.
- Copy: "Set the {label} key under Secrets first", "{label} refused the key", "Key works: {n} models", "{n} models from {label}".

## Review Focus

1. A provider answering 200 with HTML or an unexpected shape: 502 with a plain message, no crash.
2. An own-server URL that redirects (to a login page, say): not followed, 502.
3. An environment-only key (JEROME_MODELS / ANTHROPIC_API_KEY, nothing stored in the app): the list still works.
4. An unreadable stored secret (key rotated): treated as not set, 409, not a 500.
5. The Model field after switching provider: the live list of the old provider must not stay as suggestions.

---

### Task 1: The lister

**Files:**
- Create: `jerome/models/listing.py`
- Modify: `jerome/api/app.py` (`_ERROR_STATUS`: `ProviderListError: 502`)
- Test: `tests/models/test_listing.py`

**Interfaces:**
- Produces: `LISTABLE: tuple[str, ...]`, `LABELS: dict[str, str]` (provider → name people know, e.g. "OpenAI", "Own server"), `class ProviderListError(JeromeError)`, `list_models(provider: str, *, api_key: str | None, base_url: str | None = None, timeout: float = 10.0) -> list[dict[str, str]]` (dicts `{"id","name"}`, sorted by id, unique ids).

- [ ] **Step 1: Write the failing tests** (respx), each asserting the request made and the list returned:
  - `test_anthropic_follows_pages`: two pages (`has_more`, `last_id` → `after_id`), headers `x-api-key` and `anthropic-version: 2023-06-01`; names from `display_name`.
  - `test_openai_drops_non_text_models`: ids `gpt-5`, `text-embedding-3-large`, `tts-1`, `whisper-1`, `dall-e-3`, `omni-moderation-latest`, `gpt-4o-realtime-preview`, `gpt-image-1`, `sora-2` → only `gpt-5`; header `Authorization: Bearer k`.
  - `test_gemini_strips_prefix`: `models/gemini-2.5-pro`, `models/text-embedding-004` → `gemini-2.5-pro`.
  - `test_mistral_uses_capabilities`: `completion_chat: false` dropped.
  - `test_openrouter_needs_text_output`: `architecture.output_modalities == ["image"]` dropped; `name` kept.
  - `test_own_server_without_key`: no Authorization header; base URL `http://localhost:11434/v1`.
  - `test_errors_never_show_the_key`: 401 → `ProviderListError("OpenAI refused the key")`; 500, timeout, non-JSON, a 302 (not followed) and a 6 MB body → `ProviderListError`; `"k-secret" not in str(err)` for each.
  - `test_unlisted_provider`: `list_models("deepl", ...)` raises `ValueError`.
- [ ] **Step 2: Run** `uv run pytest -q tests/models/test_listing.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `list_models` in `jerome/models/listing.py`: preset URLs from `jerome.models.registry.PRESET_BASE_URLS`; `httpx.Client(follow_redirects=False, timeout=timeout)`; stream the body and stop past 5 MB; drop ids containing any of `embed`, `tts`, `whisper`, `dall-e`, `moderation`, `image`, `realtime`, `transcribe`, `audio`, `sora`, `imagen`, `veo`, `aqa`. Add `ProviderListError` → 502 in `jerome/api/app.py`.
- [ ] **Step 4: Run** the same tests — Expected: PASS. Then `uv run ruff check . && uv run ruff format --check .`.
- [ ] **Step 5: Commit** `feat: list a provider's models`.

### Task 2: The route

**Files:**
- Modify: `jerome/core/app_settings.py` (`Effective` gains `secrets: dict[str, str | None]`, `field(default_factory=dict, repr=False)`; `overlay` fills it)
- Modify: `jerome/api/routes_settings.py` (new route)
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: Task 1's `list_models`, `LISTABLE`, `LABELS`, `ProviderListError`.
- Produces: `GET /v1/settings/provider-models` as in Global Constraints.

- [ ] **Step 1: Write the failing tests:**
  - `test_provider_models_super_admin_only`: API key → 403; member session → 403.
  - `test_provider_models_with_app_key`: store `openai_api_key` (with_secret_storage), respx `GET https://api.openai.com/v1/models` → 200 `{"provider":"openai","models":[{"id":"gpt-5","name":"gpt-5"}]}`; the key not in the reply.
  - `test_provider_models_with_environment_key`: `app.state.settings` with `anthropic_api_key` set only there → 200.
  - `test_provider_models_without_key_is_409`: `xai` → 409, "Set the xAI key under Secrets first".
  - `test_provider_models_bad_input_is_400`: `provider=deepl`, `provider=nope`, `openai_compat` without base_url, `base_url=ftp://x` → 400.
  - `test_provider_models_provider_failure_is_502`: respx 401 → 502, "OpenAI refused the key".
- [ ] **Step 2: Run** `uv run pytest -q tests/api/test_settings_api.py -k provider_models` — Expected: FAIL (404).
- [ ] **Step 3: Implement** the route: `overlay(base, load_rows(session), secret_box(base)).secrets[f"{provider}_api_key"]`; base URL checked with `app_settings._url`; `list_models` runs in the threadpool (a plain `def` route).
- [ ] **Step 4: Run** the tests — Expected: PASS; then the full backend suite.
- [ ] **Step 5: Commit** `feat: provider model list route`.

### Task 3: The web

**Files:**
- Create: `web/src/lib/providerModels.ts` (`useProviderModels(provider: string, baseUrl: string | null, enabled: boolean)`, query key `["provider-models", provider, baseUrl]`, `staleTime` 10 min, `retry: false`; `fetchProviderModels(provider, baseUrl?)`)
- Modify: `web/src/components/settings/ModelSettings.tsx`, `SecretsSection.tsx`, `web/src/pages/SettingsPage.tsx` (pass which provider keys are set)
- Test: `web/src/pages/SettingsPage.test.tsx`
- Modify: `README.md` (one line under Settings)

**Interfaces:**
- Consumes: the Task 2 route.

- [ ] **Step 1: Write the failing tests:**
  - "a provider with a key offers its live models": `openai_api_key` set, provider OpenAI picked → the datalist holds the route's ids, and the line "2 models from OpenAI" shows; Refresh calls the route again.
  - "without a key the fixed suggestions stay": no request is made.
  - "switching provider drops the old list": after OpenAI → Mistral (no key), no OpenAI ids remain.
  - "own server lists after Load": Load sends `base_url`.
  - "saving a provider key checks it": after Save of `openai_api_key` → "Key works: 2 models"; a 502 → its message; Save of `webhook_secret` → no request.
- [ ] **Step 2: Run** `cd web && npm test -- SettingsPage` — Expected: FAIL.
- [ ] **Step 3: Implement** as in the spec's Web section; the route's error message is shown as is.
- [ ] **Step 4: Run** `cd web && npm test && npm run build` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): live model lists on Settings`.
