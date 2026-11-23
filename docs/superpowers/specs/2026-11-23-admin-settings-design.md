# Super admin settings: design

Status: draft, written 2026-11-23. The owner chose approach A: secrets entered in the app and stored encrypted.

It builds on these designs:
- team roles (`2026-11-22-roles-and-theme-design.md`);
- the MCP server (`2026-11-22-mcp-server-design.md`).

## Intent

Today, every Jerome setting lives in environment variables on the deployment platform. API keys are made with a command-line command inside the container.

The owner is Jerome's super admin. They want to manage every setting from inside Jerome, on a **Settings** page that only the super admin can see. That includes the AI model, the limits, the API keys for scripts and Janus, and the secrets such as the Anthropic key. No deployment-platform console and no container shell should be needed.

## Goals

- A **Settings** page at `/settings`, in the web app, for the super admin only. The nav link appears only for them.
- **Runtime settings** can be edited, take effect within a few seconds without a restart, and apply in both the API and the worker. These are:

  | Setting | Field | Validation |
  |---|---|---|
  | `models.ai` | provider (`anthropic` or `openai_compat`), model, effort; for `openai_compat` also base URL and API key | effort is empty or one of `low`, `medium`, `high`, `xhigh`, `max`; the model is a non-empty string |
  | `prices` | per-model `input`, `output`, `cache_read`, `cache_write` (USD per million tokens) | numbers ≥ 0 |
  | `text_daily_cost_cap` | USD | ≥ 0 |
  | `text_rpm` | requests per minute | 1–10000 |
  | `max_upload_mb` | MB | 1–500 |
  | `session_days` | days | 1–365 |
  | `languagetool_url` | URL | `http` or `https` |
  | `public_base_url` | URL, or empty | `https`, or `http` on localhost; no path |

- **Secrets** are write-only: the page lets you set, replace or clear them, and never shows the value. These are:
  - `anthropic_api_key`;
  - `webhook_secret`;
  - the `openai_compat` API key.
- **Where each value comes from.** The page shows, for each setting, whether its value is the default, comes from the environment, or was set in the app. It also shows who changed it and when. **Reset** removes the app value, so the environment or default value applies again.
- **Precedence:** an app value overrides the environment, and the environment overrides the code default.
- **Read-only facts**, shown but not editable:
  - the database URL with its password masked;
  - `data_dir` and `web_dir`;
  - `trusted_proxies`;
  - `cookie_secure`;
  - whether `JEROME_SECRET_KEY` is set.
- **API keys:** the same page lists them (name, created date, revoked or active), creates them, and revokes them.
  - A new key is shown **once**, in a dialog with a copy button, the same way temporary passwords are shown today.
  - Revoking a key takes effect on the next request.
- **Audit:** every settings change and every key create or revoke writes a row with who, when, which setting, and the old and new values. For secrets, the row says only "set", "replaced" or "cleared". The page shows the latest 50.

## Non-goals

These stay environment-only:
- the infrastructure settings (database URL, data and web directories, trusted proxies, `cookie_secure`), because a wrong value there would lock everyone out or open a hole, and could not be fixed from the app;
- `JEROME_SECRET_KEY` itself.

Also out of scope:
- Janus or any API key changing settings or keys. The MCP server gets no settings tools, and the endpoints accept only a web session from a super admin, as the Team endpoints do.
- A "test connection" button for the model or LanguageTool.
- A `last used` timestamp for API keys.
- Per-book settings, which already exist.

## Anti-goals

- A secret value must never be returned by any API, written to a log, or shown on the page after it is saved.
- A database backup on its own must not reveal any secret.
- A bad value must never take Jerome down in a way that only a restart or the deployment console can fix. All validation happens on save. A stored value that later turns out to be unreadable falls back to the environment or default value and is flagged on the page.

## Approaches

The owner chose **A**.

- **A. Encrypted in the database.**
  - Secrets are encrypted with Fernet (`cryptography`) using `JEROME_SECRET_KEY`, which lives only in the environment. A backup alone reveals nothing.
  - Without the key, the secret fields are disabled, and the page explains how to set it. If the key changes, the stored secrets can't be read. They are flagged as such and simply entered again.
- **B. Plain text in the database.** This is the simplest option, but anyone with a backup has the Anthropic key. Rejected.
- **C. Jerome writes environment variables to the deployment platform and redeploys.** Jerome would have to hold a platform token, which is a bigger risk than the secrets themselves, and Jerome would be tied to one platform. Rejected.

## Design

### Storage

Migration 0007 adds two tables.

**`app_settings`**, one row per overridden setting:
- `key`, the primary key; one of the names in the table above, or `anthropic_api_key`, `webhook_secret`, `openai_compat_api_key`;
- `value` (jsonb, null for secrets);
- `secret` (bytea Fernet ciphertext, null for non-secrets);
- `updated_by` (text);
- `updated_at`.

**`settings_audit`:**
- `id`, `at`, `actor`, `key`;
- `action`, one of `set`, `replaced`, `cleared`, `reset`, `key_created`, `key_revoked`;
- `before` and `after` (jsonb, always null for secrets).

`api_keys` stays as it is.

### Effective settings

A single function builds the effective settings: the environment `Settings`, with the decrypted app values from `app_settings` laid over it. Every place that reads a runtime setting today reads the effective settings instead. That covers:
- the registry (model, prices and key);
- the LanguageTool client;
- the text limits;
- the upload limit;
- the session length;
- the public base URL;
- the webhook secret.

It applies in both the API and the worker. Each process keeps the effective settings and the objects built from them, the registry and the LanguageTool client. A cheap check of a change marker, such as `max(updated_at)` or a version counter, rebuilds them when a value has changed. Changes therefore apply within a few seconds and with no restart. The interval is left to the builder; it must be 10 seconds or less.

A job already running keeps the model it started with until its current chunk finishes. The next chunk uses the new settings.

### API

The new endpoints all depend on `require_super_admin`, so they need a web session.

- `GET /v1/settings` returns each setting with its effective value, source, and who last changed it and when. For secrets it returns `is_set`, the source, and `unreadable`, never the value. It also returns the read-only facts and whether secret storage is available.
- `PUT /v1/settings/{key}` validates the value and saves it. For a secret, the body carries the new value, or null to clear it. It returns 400 with a clear message when the value is invalid, and 409 when a secret is set while `JEROME_SECRET_KEY` is missing.
- `DELETE /v1/settings/{key}` resets the setting to the environment or default value.
- `GET /v1/settings/audit` returns the latest 50 audit rows.
- `GET /v1/api-keys`, `POST /v1/api-keys {name}` (returns the key once) and `DELETE /v1/api-keys/{id}` (revokes it). Names are unique; reusing a revoked key's name is allowed only if the builder can do it cleanly, otherwise it returns a 409 that says so.

### Web

The `/settings` page has these sections:
1. AI model;
2. Limits;
3. Services (LanguageTool, public URL);
4. Secrets;
5. API keys;
6. System (read-only);
7. Recent changes.

Each setting shows its current value and a source badge (default, environment or app). It has an edit control, Save, and Reset when it has an app value.

Secret fields show "Set (app)", "Set (environment)", "Not set" or "Can't be read, enter again", next to a password-style input and Clear. The UI follows the existing Team page patterns: shadcn/ui, react-query, toast on errors, light and dark themes.

## Constraints

- The repo is public: no secrets, owner hosts or deployment IDs in code, docs or tests.
- The product default model stays `claude-opus-5-5`. The owner may change it on the page, and the code default does not change.
- Tests use only the public fixtures and the test builders.
- Deploying needs `JEROME_SECRET_KEY` set in production, as a random Fernet key. The owner has agreed that the assistant generates it and stores it on the deployment platform without printing it.

## Calls made after the builder check

The owner can change any of these.

1. **`models.ai` is the unit you override.** The app value replaces the whole `ai` entry. Any other passes in the environment's `models` stay as they are.
2. **The `openai_compat` key.**
   - The stored `models.ai` never holds an `api_key`.
   - The effective settings put the `openai_compat_api_key` secret into it. When that secret is set in the app, it overrides any `api_key` in the environment's `models.ai`; otherwise the page shows that key as coming from the environment.
   - The secret is kept when the provider is switched to `anthropic`, but it is used only while the provider is `openai_compat`.
3. **An override is all-or-nothing per key.** `prices` is edited as one table, filled in with the effective prices, and saving it replaces the whole `prices` value. The source badge, Reset and "who changed it" therefore all work per key.
4. **When changes apply.**
   - The worker picks up changes between chunks.
   - The webhook secret, the upload limit, the session length and the public URL are read from the effective settings each time they are used.
   - The LanguageTool client and the registry are rebuilt when their inputs change.
   - When `text_rpm` changes, the limiter keeps its counters if that is simple to do. Resetting them is acceptable.
5. **What `app.state.settings` holds.** It stays the environment base. A settings store on `app.state`, and the same store in the worker, gives the effective settings. `create_app(settings)` in tests keeps working, and tests that need an app value write it to the table.
6. **`public_base_url`.**
   - It is a scheme, a host and an optional port, with any trailing `/` removed. A path or a query is rejected.
   - `https` is accepted with any host; `http` only with `localhost`, `127.0.0.1` or `[::1]`.
7. **Clear and Reset are different.**
   - **Clear** stores an explicit "not set" in the app, which wins over the environment. It is useful to switch the webhook off.
   - **Reset** deletes the app value, so the environment value applies again.
   - Clearing is always allowed.
8. **Key names stay unique, as the table already requires.** Reusing a revoked key's name returns 409 with "a key with this name existed; choose another name".

## Left to the builder

- How the change check and its interval work (10 seconds or less), and how the registry is rebuilt.
- How validation is structured, for example one pydantic model per setting.
- The exact page layout within the sections above.
- Whether the CLI `keys create` stays as it is; it should.
