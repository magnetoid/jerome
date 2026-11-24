# Pulling a provider's model list — design

Approved by the owner on 2026-11-24 ("Odobravam oba"). A small change.

## Intent

The owner adds a provider's API key on Settings and wants Jerome to fetch that provider's
models instead of typing a name from memory. The fixed suggestion lists in the web code
(`SUGGESTED_MODELS`) go stale. Fetching the list also proves the key works.

## Goals

- When a provider key is saved under Secrets, Jerome fetches that provider's models at once
  and says "Key works: N models" or why it failed ("OpenAI refused the key").
- Under Models, a provider with a key offers its live list in the Model field, with a
  Refresh button. Without a key, the fixed suggestions stay. Any name can still be typed.
- For "Own server / other", the list comes from the Base URL typed in the form.

## Non-goals

- Prices stay manual. OpenRouter's list has prices; taking them is a possible later step.
- The list is not stored in the database. It is fetched when needed.
- DeepL has no model choice, so it gets no list.
- No MCP tool: Settings is for a super admin web session only.

## Approaches

- **Server fetches with the stored key (chosen).** The key never reaches the browser, and
  environment keys work too.
- Browser fetches the provider directly: impossible without sending the key to the browser,
  and most providers block cross-origin calls.
- Store the list when a key is saved: stale lists, more state, no gain for one admin.

## Design

- `GET /v1/settings/provider-models?provider=<name>[&base_url=<url>]`, super admin only, like
  the rest of `/v1/settings`. It returns `{"provider", "models": [{"id", "name"}]}`, sorted by id.
- The key is the effective secret, from the app or the environment, `<provider>_api_key`.
  `overlay` already computes it; `Effective` keeps the secrets so the route can read one.
- Endpoints:
  - Anthropic: `GET https://api.anthropic.com/v1/models`, with `x-api-key` and
    `anthropic-version`, following `has_more` / `last_id`.
  - OpenAI, Gemini, Mistral, OpenRouter, xAI: `GET <preset base URL>/models` with a Bearer key.
  - Own server: `GET <base_url>/models`, with the key when one is set.
  - Gemini ids lose their `models/` prefix.
- Only models that write text are listed. Ids naming embeddings, speech, images, video,
  moderation or realtime audio are dropped. Mistral's `capabilities.completion_chat: false`
  and OpenRouter's output modalities without text drop a model too.
- Errors are a clear message with HTTP 409 (no key set), 400 (bad provider or URL) or 502
  (the provider failed: refused key, timeout, bad reply). The key never appears in a message.
- Limits: a 10 s timeout, no redirects, replies up to 5 MB, at most 20 pages from Anthropic.
- Web:
  - **Secrets:** after Save of a model provider's key, the item shows the result of a fetch.
    The own-server key is skipped, since its URL is set under Models.
  - **Models:** the live list fills the Model field's suggestions while the provider has a
    key (for own server: once Load is pressed). A line shows "N models from OpenAI", or the
    error. Refresh fetches it again.

## Left to the builder

The exact filter words, the query cache time on the web (10 minutes is fine) and the copy of
the lines.
