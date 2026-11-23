# Super admin settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A super-admin-only `/settings` page that edits Jerome's runtime settings and secrets (secrets encrypted in the database), manages API keys, and shows an audit of changes, with changes reaching the API and the worker within seconds and without a restart.

**Architecture:** Two new tables hold app overrides and the audit. A pure `overlay()` lays the decrypted app values over the environment `Settings`. A per-process `SettingsStore` caches the result and re-reads the (tiny) table at most every 5 seconds, or at once after a write in the same process. The API reads every runtime setting through one helper that also rebuilds the registry, the LanguageTool client and the text limiter when their inputs change. The worker gets the same effect through a refresh callable that runs before each task and each chunk.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, `cryptography` (Fernet), pytest. Web: React 19, react-query 5, shadcn/ui, vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-admin-settings-design.md`

## Global Constraints

- The repo is public: no secrets, owner hosts, IPs, domains or deployment IDs in code, docs or tests.
- The product default model stays `claude-opus-5-5`; the code default in `jerome/config.py` does not change.
- Tests use only `tests/fixtures/public/` and the test builders. No book files.
- A secret value is never returned by any API, written to a log or the audit, or shown on the page after it is saved.
- A database backup alone reveals no secret: secrets are stored only as Fernet ciphertext made with `JEROME_SECRET_KEY`, which lives only in the environment.
- A bad value never takes Jerome down: all validation happens on save; a stored value that cannot be read or no longer validates falls back to the environment or default value and is flagged `unreadable`.
- Precedence: app value > environment > code default.
- Changes apply within 10 seconds or less, without a restart, in the API and in the worker.
- Settings and API-key endpoints depend on `require_super_admin` (web session, role `super_admin`). No MCP tools for them.
- Editable runtime keys, exactly: `models.ai`, `prices`, `text_daily_cost_cap`, `text_rpm`, `max_upload_mb`, `session_days`, `languagetool_url`, `public_base_url`.
- Secret keys, exactly: `anthropic_api_key`, `webhook_secret`, `openai_compat_api_key`.
- Validation: effort empty or one of `low`, `medium`, `high`, `xhigh`, `max`; model non-empty; prices numbers ≥ 0; `text_daily_cost_cap` ≥ 0; `text_rpm` 1–10000; `max_upload_mb` 1–500; `session_days` 1–365; `languagetool_url` `http`/`https`; `public_base_url` empty, or scheme + host + optional port with trailing `/` removed, no path or query, `https` any host, `http` only `localhost`, `127.0.0.1`, `[::1]`.
- Audit actions, exactly: `set`, `replaced`, `cleared`, `reset`, `key_created`, `key_revoked`. `before`/`after` are always null for secrets. The page shows the latest 50.
- Reusing a revoked key's name returns 409 with `a key with this name existed; choose another name`.
- Backend checks: `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .`. Web: `cd web && npm test && npm run build`. Never run two pytest processes at once (shared test DB).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **`JEROME_SECRET_KEY` changed, missing or malformed while secrets are stored** → Jerome still starts, `GET /v1/settings` still works, those secrets show `unreadable: true`, and the environment value (if any) is used. Pinned in Task 1 (`secret_box` returns None for a malformed key) and Task 2 (`test_overlay_wrong_key_falls_back_and_flags`).
2. **The settings table can't be read for a moment (DB blip) when the store checks** → the last good settings stay in use; no request fails because of the check. Pinned in Task 4 (`test_store_keeps_last_settings_when_read_fails`).
3. **A secret value leaking** through `GET /v1/settings`, the audit, the PUT response, or an error message → never. Pinned in Task 6 (`test_secret_never_returned`).
4. **A wrong-but-plausible value** (`True` for `text_rpm`, `2.5` for `session_days`, `openai_compat` with no base URL, an `api_key` inside `models.ai`, `https://x.org/path` as the public URL) → 400 with a clear message and nothing stored. Pinned in Task 2 (`test_validate_rejects`).
5. **A new API key's token** being shown again or kept in the browser's query cache → the token appears only in the create response and the one-time dialog. Pinned in Task 6 (`test_api_keys_lifecycle`) and Task 7 (`creates a key and shows it once`).

---

### Task 1: Secret box, `JEROME_SECRET_KEY`, tables

**Files:**
- Modify: `pyproject.toml` (add `"cryptography"` to `dependencies`), `uv.lock`
- Modify: `jerome/config.py` (field `secret_key`)
- Create: `jerome/core/secretbox.py`
- Modify: `jerome/orm.py` (models `AppSetting`, `SettingsAudit`)
- Create: `migrations/versions/0007_app_settings.py`
- Test: `tests/core/test_secretbox.py`, `tests/test_schema.py` (existing `test_migration_matches_models` covers the tables)

**Interfaces:**
- Produces:
  - `Settings.secret_key: SecretStr | None = None` (env `JEROME_SECRET_KEY` through the existing prefix).
  - `class SecretUnreadable(Exception)`.
  - `class SecretBox: __init__(self, key: str)` (raises `ValueError` on a malformed key); `encrypt(self, plain: str) -> bytes`; `decrypt(self, token: bytes) -> str` (raises `SecretUnreadable`).
  - `secret_box(settings: Settings) -> SecretBox | None`: None when the key is unset, empty or malformed.
  - `secret_storage_state(settings: Settings) -> Literal["ready", "missing", "invalid"]`.
  - ORM `AppSetting` (`app_settings`): `key: str` PK, `value` JSONB nullable, `secret: bytes | None` (`LargeBinary`), `updated_by: str` not null, `updated_at` timestamptz not null, server default now.
  - ORM `SettingsAudit` (`settings_audit`): `id` PK, `at` timestamptz server default now, `actor: str`, `key: str`, `action: str` with check constraint `ck_settings_audit_action` over the six actions, `before` JSONB nullable, `after` JSONB nullable; index `ix_settings_audit_at` on `at`.

- [ ] **Step 1: Write the failing tests** in `tests/core/test_secretbox.py`:

```python
from cryptography.fernet import Fernet
from jerome.config import Settings
from jerome.core.secretbox import SecretBox, SecretUnreadable, secret_box, secret_storage_state

def test_round_trip_and_ciphertext_hides_plain():
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("sk-ant-test-123")
    assert b"sk-ant-test-123" not in token
    assert box.decrypt(token) == "sk-ant-test-123"

def test_other_key_cannot_read():
    token = SecretBox(Fernet.generate_key().decode()).encrypt("x")
    with pytest.raises(SecretUnreadable):
        SecretBox(Fernet.generate_key().decode()).decrypt(token)

def test_secret_box_states():
    assert secret_box(Settings(_env_file=None)) is None
    assert secret_storage_state(Settings(_env_file=None)) == "missing"
    bad = Settings(_env_file=None, secret_key="not-a-fernet-key")
    assert secret_box(bad) is None and secret_storage_state(bad) == "invalid"
    good = Settings(_env_file=None, secret_key=Fernet.generate_key().decode())
    assert secret_box(good) is not None and secret_storage_state(good) == "ready"
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_secretbox.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the dependency (`uv add cryptography`), the config field, `jerome/core/secretbox.py` over `cryptography.fernet.Fernet` (`InvalidToken` → `SecretUnreadable`), the two ORM models, and migration `0007` (revision `"0007"`, down_revision `"0006"`, with a `downgrade()` that drops both tables).
- [ ] **Step 4: Run** `uv run pytest tests/core/test_secretbox.py tests/test_schema.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: secret box and app settings tables`.

---

### Task 2: Setting definitions, validation and the overlay

**Files:**
- Create: `jerome/core/app_settings.py`
- Test: `tests/core/test_app_settings.py`

**Interfaces:**
- Consumes: Task 1 `SecretBox`, `SecretUnreadable`, `AppSetting`.
- Produces:
  - `RUNTIME_KEYS: tuple[str, ...]` and `SECRET_KEYS: tuple[str, ...]` (the exact lists in Global Constraints, in that order).
  - `class InvalidSetting(ValueError)`; `class UnknownSetting(NotFound)` (subclass of `jerome.errors.NotFound`, message `unknown setting: <key>`).
  - `validate(key: str, value: Any) -> Any`: returns the normalized JSON value to store, raises `InvalidSetting` with a message a person can act on. Unknown key → `UnknownSetting`. Secret keys are not validated here.
  - `@dataclass(frozen=True) class Meta`: `source: Literal["default", "environment", "app"]`, `updated_by: str | None = None`, `updated_at: datetime | None = None`, `unreadable: bool = False`, `is_set: bool | None = None` (secrets only).
  - `@dataclass(frozen=True) class Effective`: `settings: Settings`, `meta: dict[str, Meta]` (one entry per runtime and secret key).
  - `overlay(base: Settings, rows: Sequence[AppSetting], box: SecretBox | None) -> Effective`.
  - `value_of(settings: Settings, key: str) -> Any`: the JSON-able effective value of a runtime key (`models.ai` without `api_key`; `text_daily_cost_cap` as a string; `public_base_url` may be None).

Normalization (stored form): `models.ai` → `{"provider", "model", "effort"?, "base_url"?}` with `effort` omitted when empty and `base_url` only for `openai_compat` (required there, `http`/`https`); any other field, including `api_key`, is rejected (message names `openai_compat_api_key`). `prices` → `{model: {"input", "output", "cache_read", "cache_write"}}`, all four required, JSON numbers, model names non-empty. `text_daily_cost_cap` → decimal string (accepts a number or numeric string). Integer keys reject `bool` and non-integral numbers. URLs → trailing `/` stripped. `public_base_url` `None` or `""` → `None`.

Overlay rules (spec "Calls made after the builder check"):
- Source of a runtime key without an app row: `environment` when its field is in `base.model_fields_set` (`models` for `models.ai`), else `default`.
- An app row for `models.ai` replaces only the `ai` entry of `base.models`; other passes stay.
- A stored non-secret value that fails `validate` now → env/default value, `unreadable=True`, source as if no row.
- Secrets: an app row with `secret` null means cleared (`is_set=False`, source `app`, wins over env). An app row whose ciphertext can't be decrypted, or any secret row when `box` is None → env value, `unreadable=True`. Without a row: `environment` with `is_set=True` when the env value is non-empty, else `default` with `is_set=False`.
- `openai_compat_api_key`: its env value is `base.models["ai"].get("api_key")`. The effective `models["ai"]` gets `api_key` = the app secret when an app row exists (removed when cleared), else the env one; `api_key` is dropped when the provider is `anthropic`.
- The returned `settings` is `base.model_copy(update=...)`; `anthropic_api_key` stays a `SecretStr`.

- [ ] **Step 1: Write the failing tests** (`row(key, value=None, secret=None)` is a local helper that builds an unsaved `AppSetting` with `updated_by="ana@x.org"` and a fixed `updated_at`):

```python
def test_validate_rejects():
    bad = [
        ("text_rpm", True), ("text_rpm", 0), ("text_rpm", 10001), ("session_days", 2.5),
        ("max_upload_mb", 501), ("text_daily_cost_cap", -1),
        ("models.ai", {"provider": "openai_compat", "model": "m"}),
        ("models.ai", {"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "k"}),
        ("models.ai", {"provider": "anthropic", "model": "", "effort": "high"}),
        ("models.ai", {"provider": "anthropic", "model": "m", "effort": "huge"}),
        ("prices", {"m": {"input": 1, "output": 1, "cache_read": -1, "cache_write": 1}}),
        ("prices", {"m": {"input": 1}}),
        ("languagetool_url", "ftp://lt"), ("public_base_url", "https://x.org/path"),
        ("public_base_url", "https://x.org?q=1"), ("public_base_url", "http://x.org"),
    ]
    for key, value in bad:
        with pytest.raises(InvalidSetting):
            validate(key, value)
    with pytest.raises(UnknownSetting):
        validate("database_url", "x")

def test_validate_normalizes():
    assert validate("public_base_url", "https://jerome.example.com/") == "https://jerome.example.com"
    assert validate("public_base_url", "http://localhost:8000") == "http://localhost:8000"
    assert validate("public_base_url", "") is None
    assert validate("text_daily_cost_cap", 7.5) == "7.5"
    assert validate("models.ai", {"provider": "anthropic", "model": "m", "effort": ""}) == {
        "provider": "anthropic", "model": "m"}

def test_overlay_sources_and_precedence():
    base = Settings(_env_file=None, text_rpm=40)
    eff = overlay(base, [row("session_days", 7)], None)
    assert eff.settings.session_days == 7 and eff.meta["session_days"].source == "app"
    assert eff.meta["session_days"].updated_by == "ana@x.org"
    assert eff.meta["text_rpm"].source == "environment" and eff.settings.text_rpm == 40
    assert eff.meta["max_upload_mb"].source == "default"
    assert eff.settings.models["ai"]["model"] == "claude-opus-5-5"

def test_overlay_models_ai_keeps_other_passes_and_injects_key():
    box = SecretBox(Fernet.generate_key().decode())
    base = Settings(_env_file=None, models={"ai": {"provider": "anthropic", "model": "a"},
                                             "other": {"provider": "anthropic", "model": "b"}})
    ai = {"provider": "openai_compat", "model": "m", "base_url": "http://llm:1"}
    eff = overlay(base, [row("models.ai", ai),
                         row("openai_compat_api_key", secret=box.encrypt("k1"))], box)
    assert eff.settings.models["other"]["model"] == "b"
    assert eff.settings.models["ai"]["api_key"] == "k1"
    assert "api_key" not in value_of(eff.settings, "models.ai")

def test_overlay_cleared_secret_wins_over_env():
    base = Settings(_env_file=None, webhook_secret="env-secret")
    eff = overlay(base, [row("webhook_secret")], None)
    assert eff.settings.webhook_secret is None
    assert eff.meta["webhook_secret"] == Meta(source="app", updated_by="ana@x.org",
        updated_at=FIXED, is_set=False)

def test_overlay_wrong_key_falls_back_and_flags():
    token = SecretBox(Fernet.generate_key().decode()).encrypt("app-secret")
    other = SecretBox(Fernet.generate_key().decode())
    base = Settings(_env_file=None, webhook_secret="env-secret")
    for box in (other, None):
        eff = overlay(base, [row("webhook_secret", secret=token)], box)
        assert eff.settings.webhook_secret == "env-secret"
        assert eff.meta["webhook_secret"].unreadable is True

def test_overlay_invalid_stored_value_falls_back():
    eff = overlay(Settings(_env_file=None), [row("text_rpm", "lots")], None)
    assert eff.settings.text_rpm == 30 and eff.meta["text_rpm"].unreadable is True
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_app_settings.py -q` → FAIL.
- [ ] **Step 3: Implement** `jerome/core/app_settings.py`; one small validator function per key in a dict is enough.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat: setting validation and overlay`.

---

### Task 3: Changes, audit and API-key management (core)

**Files:**
- Create: `jerome/core/settings_changes.py`
- Modify: `jerome/core/keys.py`
- Modify: `jerome/api/app.py` (`_ERROR_STATUS`: `InvalidSetting` → 400; `UnknownSetting` (a `NotFound`) and `SecretStorageUnavailable` / `KeyNameTaken` (both `Conflict`) already map to 404 and 409 through their base classes)
- Test: `tests/core/test_settings_changes.py`, `tests/core/test_keys.py` (new)

**Interfaces:**
- Consumes: Task 2 `validate`, `overlay`, `value_of`, `RUNTIME_KEYS`, `SECRET_KEYS`; Task 1 tables and `secret_box`.
- Produces (all take the request's `Session`, flush, never commit):
  - `class SecretStorageUnavailable(Conflict)` with message `secret storage is not set up: set JEROME_SECRET_KEY`.
  - `set_value(session, key: str, value: Any, *, actor: str, base: Settings) -> None`: validates, upserts the row, audits `set` with `before`/`after` = `value_of` the effective value before and after.
  - `set_secret(session, key: str, value: str | None, *, actor: str, base: Settings) -> None`: `None` clears (always allowed, stores a row with `secret` null, audit `cleared`); a string is stripped, empty → `InvalidSetting("enter a value, or clear it")`, no usable box → `SecretStorageUnavailable`; audit `set` when the effective secret was not set before, else `replaced`. `before`/`after` null.
  - `reset(session, key: str, *, actor: str, base: Settings) -> None`: deletes the app row and audits `reset` (non-secret: before/after values; secret: null). No row → nothing, no audit.
  - `list_audit(session, limit: int = 50) -> list[SettingsAudit]`, newest first.
  - `load_rows(session) -> list[AppSetting]`.
  - In `keys.py`: `class KeyNameTaken(Conflict)`; `create_named_key(session, name: str, *, actor: str) -> tuple[ApiKey, str]` (strips the name; empty or over 100 chars → `InvalidSetting`; an active key with that name → `KeyNameTaken("a key with this name already exists")`; a revoked one → `KeyNameTaken("a key with this name existed; choose another name")`; audits `key_created` with `key = "api_key:<name>"`); `list_api_keys(session) -> list[ApiKey]` ordered by `created_at`; `revoke_api_key(session, key_id: int, *, actor: str) -> ApiKey` (unknown → `NotFound`; already revoked → returned unchanged, no audit; else sets `revoked_at`, audits `key_revoked`). `create_api_key` and the CLI stay as they are.

- [ ] **Step 1: Write the failing tests** (use the `session` fixture; `BOX_SETTINGS = Settings(_env_file=None, secret_key=<generated>)`):

```python
def test_set_value_audits_before_and_after(session):
    set_value(session, "text_rpm", 12, actor="ana@x.org", base=BOX_SETTINGS)
    set_value(session, "text_rpm", 20, actor="ana@x.org", base=BOX_SETTINGS)
    rows = list_audit(session)
    assert [(r.action, r.before, r.after) for r in rows] == [("set", 12, 20), ("set", 30, 12)]

def test_secret_actions_and_ciphertext(session):
    set_secret(session, "anthropic_api_key", " sk-1 ", actor="a", base=BOX_SETTINGS)
    set_secret(session, "anthropic_api_key", "sk-2", actor="a", base=BOX_SETTINGS)
    set_secret(session, "anthropic_api_key", None, actor="a", base=BOX_SETTINGS)
    assert [r.action for r in list_audit(session)] == ["cleared", "replaced", "set"]
    assert all(r.before is None and r.after is None for r in list_audit(session))
    stored = session.get(AppSetting, "anthropic_api_key")
    assert stored.secret is None  # cleared

def test_secret_needs_storage_but_clear_does_not(session):
    with pytest.raises(SecretStorageUnavailable):
        set_secret(session, "webhook_secret", "s", actor="a", base=Settings(_env_file=None))
    set_secret(session, "webhook_secret", None, actor="a", base=Settings(_env_file=None))

def test_reset_removes_override(session):
    set_value(session, "session_days", 3, actor="a", base=BOX_SETTINGS)
    reset(session, "session_days", actor="a", base=BOX_SETTINGS)
    assert session.get(AppSetting, "session_days") is None
    assert list_audit(session)[0].action == "reset"
    reset(session, "session_days", actor="a", base=BOX_SETTINGS)  # no row: no new audit
    assert len(list_audit(session)) == 2

def test_key_names_stay_unique(session):
    key, token = create_named_key(session, " janus ", actor="a")
    assert key.name == "janus" and verify_api_key(session, token) == "janus"
    with pytest.raises(KeyNameTaken, match="already exists"):
        create_named_key(session, "janus", actor="a")
    revoke_api_key(session, key.id, actor="a")
    assert verify_api_key(session, token) is None
    with pytest.raises(KeyNameTaken, match="existed; choose another name"):
        create_named_key(session, "janus", actor="a")
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_settings_changes.py tests/core/test_keys.py -q` → FAIL.
- [ ] **Step 3: Implement** both modules and the error-status entry.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat: settings changes, audit and key management`.

---

### Task 4: `SettingsStore` and live objects

**Files:**
- Create: `jerome/core/settings_store.py`
- Modify: `jerome/proofread/text.py` (`TextLimits.set_rpm`)
- Test: `tests/core/test_settings_store.py` (also holds the `set_rpm` test)

**Interfaces:**
- Consumes: Task 2 `overlay`, `Effective`; Task 3 `load_rows`; Task 1 `secret_box`.
- Produces:
  - `class SettingsStore: __init__(self, base: Settings, *, interval: float = 5.0, clock: Callable[[], float] = time.monotonic)`.
    - `base` property; assigning a different object recomputes the overlay from the last rows read (no DB) and bumps `version`; assigning the same object does nothing.
    - `current(self, session: Session) -> Effective`: reads the rows when invalidated or when `interval` has passed since the last read; recomputes and bumps `version` only when the rows' fingerprint (key, value, secret, updated_at) changed. A read that raises is logged and the last `Effective` is returned.
    - `cached(self) -> Effective` (no DB). `invalidate(self) -> None`. `version: int` (starts at 0; the initial overlay over no rows is version 0).
  - `registry_inputs(settings: Settings) -> tuple`: what `build_registry` depends on (`models`, `prices`, the Anthropic key value), comparable with `==`.
  - `TextLimits.set_rpm(self, rpm: int) -> None`: changes capacity and rate, keeps the buckets (the existing `min(capacity, …)` in `take` clamps them).

- [ ] **Step 1: Write the failing tests** (`session_factory` fixture: rows really committed; a fake clock list):

```python
def test_store_sees_change_after_interval(session_factory):
    now = [0.0]
    store = SettingsStore(Settings(_env_file=None), clock=lambda: now[0])
    with session_factory() as s:
        assert store.current(s).settings.text_rpm == 30 and store.version == 0
        set_value(s, "text_rpm", 9, actor="a", base=store.base); s.commit()
        assert store.current(s).settings.text_rpm == 30  # not re-read yet
        now[0] = 5.1
        assert store.current(s).settings.text_rpm == 9 and store.version == 1
        now[0] = 11.0
        store.current(s)
        assert store.version == 1  # same rows: no new version

def test_invalidate_reads_at_once(session_factory): ...  # same shape: set, invalidate(), current() sees it at now=0

def test_store_keeps_last_settings_when_read_fails():
    class Broken:
        def scalars(self, *a, **k): raise OperationalError("x", {}, Exception())
    store = SettingsStore(Settings(_env_file=None, text_rpm=40))
    store.invalidate()
    assert store.current(Broken()).settings.text_rpm == 40

def test_base_swap_recomputes_without_db():
    store = SettingsStore(Settings(_env_file=None))
    store.base = Settings(_env_file=None, cookie_secure=False)
    assert store.cached().settings.cookie_secure is False and store.version == 1
```

and, in the same file:

```python
def test_set_rpm_keeps_counters():
    limits = TextLimits(2, clock=lambda: 0.0)
    assert limits.take("k") is None and limits.take("k") is None
    limits.set_rpm(10)
    assert limits.take("k") is not None  # the spent bucket was not refilled by the change
```

(Adjust `Broken` to whatever call `load_rows` makes; the point is any exception from the read.)

- [ ] **Step 2: Run** `uv run pytest tests/core/test_settings_store.py -q` → FAIL.
- [ ] **Step 3: Implement**; a `threading.Lock` around the read-and-swap keeps it safe under the threaded sync routes.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat: settings store with change check`.

---

### Task 5: Every reader uses the effective settings (API, MCP, worker)

**Files:**
- Create: `jerome/api/runtime.py`
- Modify: `jerome/api/app.py`, `jerome/api/deps.py`, `jerome/api/limits.py`, `jerome/api/routes_text.py` (only if it bypasses `get_settings_dep`), `jerome/mcp/context.py`, `jerome/jobs/worker.py`, `jerome/cli.py`
- Test: `tests/api/test_runtime.py`, `tests/jobs/test_worker_refresh.py`

**Interfaces:**
- Consumes: Task 4 `SettingsStore`, `registry_inputs`, `TextLimits.set_rpm`; existing `build_registry`, `LanguageToolClient`.
- Produces:
  - `create_app` additionally sets `app.state.settings_store = SettingsStore(settings)`. `app.state.settings` stays the environment base; `app.state.registry`, `lt_client`, `text_limits` stay where they are (tests assign them).
  - `effective_for_app(app: FastAPI, session: Session) -> Settings`: sets `store.base = app.state.settings`, calls `store.current(session)`, and when `store.version` differs from the version last synced on `app.state`: rebuilds `app.state.registry` only if `registry_inputs` changed, `app.state.lt_client` only if `languagetool_url` changed, and calls `app.state.text_limits.set_rpm` only if `text_rpm` changed. Returns the effective `Settings`.
  - `cached_for_app(app: FastAPI) -> Settings`: the same without DB access (`store.cached()`), for `BodyLimitMiddleware`.
  - `get_settings_dep(request, session: SessionDep) -> Settings` returns `effective_for_app`. `get_registry` and `get_worker_deps` call `effective_for_app` first; `get_worker_deps` uses the effective `webhook_secret`. `current_principal` and `set_session_cookie` callers use the effective `session_days`. MCP `call()` builds `ToolContext.settings` with `effective_for_app(app, session)` before `_registry`/`_worker_deps`.
  - `WorkerDeps.refresh: Callable[[WorkerDeps], WorkerDeps] | None = None`. `work_forever` applies it before each `run_task`, and `_run_chapter` applies it before each chunk; a refresh that raises is logged and the old deps are kept.
  - `class LiveRefresh` in `jerome/core/settings_store.py`: `__init__(self, store: SettingsStore, session_factory)`; `__call__(self, deps: WorkerDeps) -> WorkerDeps` returns `dataclasses.replace(deps, …)` with a rebuilt registry / LT client only when their inputs changed, and the effective `webhook_secret`. The `jerome worker` command wires it.

- [ ] **Step 1: Write the failing tests:**

```python
# tests/api/test_runtime.py — rows written through the request session, then invalidate()
def test_app_value_reaches_readers(web_client, session):
    store = web_client.app.state.settings_store
    set_value(session, "max_upload_mb", 1, actor="a", base=store.base)
    set_value(session, "public_base_url", "https://files.example.org", actor="a", base=store.base)
    store.invalidate()
    eff = effective_for_app(web_client.app, session)
    assert eff.max_upload_mb == 1 and eff.public_base_url == "https://files.example.org"
    r = web_client.post("/v1/books", headers=AUTH, files={"file": ("a.docx", b"x" * (2 * MIB))})
    assert r.status_code == 413

def test_registry_rebuilt_only_when_its_inputs_change(client, session):
    app = client.app
    before = app.state.registry
    set_value(session, "text_rpm", 7, actor="a", base=app.state.settings)
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert app.state.registry is before            # text_rpm is not a registry input
    set_value(session, "models.ai", {"provider": "anthropic", "model": "claude-sonnet-5-5"},
              actor="a", base=app.state.settings)
    app.state.settings_store.invalidate()
    effective_for_app(app, session)
    assert app.state.registry is not before
    assert app.state.registry.for_pass("ai", local_only=False)[1].model == "claude-sonnet-5-5"
```

```python
# tests/jobs/test_worker_refresh.py
def test_refresh_runs_before_each_chunk(...):
    # A two-chunk chapter job with the existing fake provider/LT helpers from tests/jobs;
    # deps.refresh counts its calls and swaps in a registry whose model name changes after
    # the first call. Assert refresh ran once per chunk (plus once per task) and that the
    # second chunk's usage row names the new model.

def test_live_refresh_picks_up_webhook_secret(session_factory):
    store = SettingsStore(Settings(_env_file=None, secret_key=KEY))
    deps = WorkerDeps(registry=build_registry(store.base), lt=..., webhook_secret=None)
    with session_factory() as s:
        set_secret(s, "webhook_secret", "w1", actor="a", base=store.base); s.commit()
    store.invalidate()
    fresh = LiveRefresh(store, session_factory)(deps)
    assert fresh.webhook_secret == "w1" and fresh.registry is deps.registry
```

Follow the existing worker tests' helpers for the two-chunk job; write the test to the assertions in the comment.

- [ ] **Step 2: Run** `uv run pytest tests/api/test_runtime.py tests/jobs/test_worker_refresh.py -q` → FAIL.
- [ ] **Step 3: Implement.** Tests that assign `app.state.settings` (e.g. `cookie_secure=False`) must keep passing unchanged: that is what the `base` setter is for.
- [ ] **Step 4: Run** the full backend suite and lint (commands in Global Constraints) → all pass.
- [ ] **Step 5: Commit** `feat: runtime settings reach the API and the worker`.

---

### Task 6: Settings and API-key endpoints, docs

**Files:**
- Create: `jerome/api/routes_settings.py`
- Modify: `jerome/api/schemas.py`, `jerome/api/app.py` (include the router under `/v1` with the auth dependency)
- Modify: `README.md` (env table row for `JEROME_SECRET_KEY`, a short "Settings page" section), `.env.example` (`# JEROME_SECRET_KEY=`)
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: Tasks 2–5. After every write the route calls `request.app.state.settings_store.invalidate()`.
- Produces (router `dependencies=[Depends(require_super_admin)]`; actor = `principal.actor`):
  - `GET /v1/settings` →
    ```json
    {"settings": [{"key": "text_rpm", "value": 30, "source": "default",
                   "updated_by": null, "updated_at": null, "unreadable": false}],
     "secrets": [{"key": "anthropic_api_key", "is_set": false, "source": "default",
                  "updated_by": null, "updated_at": null, "unreadable": false}],
     "system": {"database_url": "postgresql+psycopg://jerome:***@db:5432/jerome",
                "data_dir": "/data", "web_dir": null, "trusted_proxies": [],
                "cookie_secure": true, "secret_storage": "ready"}}
    ```
    `settings` in `RUNTIME_KEYS` order, `secrets` in `SECRET_KEYS` order. The DB password is replaced with `***` (`make_url(...).render_as_string(hide_password=True)`). Built with `overlay(app.state.settings, load_rows(session), secret_box(...))` from the request session, not the cache.
  - `PUT /v1/settings/{key}` body `{"value": <any>}` → 200 with the same body as `GET`. Runtime key → `set_value`; secret key → `set_secret` (value must be a string or null, else 400).
  - `DELETE /v1/settings/{key}` → 204.
  - `GET /v1/settings/audit` → list of `{id, at, actor, key, action, before, after}`, latest 50.
  - `GET /v1/api-keys` → list of `{id, name, created_at, revoked_at}`.
  - `POST /v1/api-keys` body `{"name": str}` → 201 `{"key": {id, name, created_at, revoked_at}, "token": "jk_…"}`.
  - `DELETE /v1/api-keys/{key_id}` → 200 with the key row.

- [ ] **Step 1: Write the failing tests** (reuse `login`, `WEB`, `create_user` from `tests/api/test_auth_api.py`; a fixture sets `web_client.app.state.settings` to a copy with a generated `secret_key`):

```python
def test_only_super_admin_on_web(web_client, session, auth):
    assert web_client.get("/v1/settings", headers=auth).status_code == 403   # API key
    create_user(session, "bo@x.org", "Bo", PASSWORD, role="member")
    web_client.post("/v1/auth/login", json={"email": "bo@x.org", "password": PASSWORD})
    assert web_client.get("/v1/settings").status_code == 403

def test_put_validates_and_resets(web_client, session):
    login(web_client, session)
    assert web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 0}).status_code == 400
    r = web_client.put("/v1/settings/text_rpm", headers=WEB, json={"value": 12})
    row = next(s for s in r.json()["settings"] if s["key"] == "text_rpm")
    assert row["value"] == 12 and row["source"] == "app" and row["updated_by"] == "ana@x.org"
    assert web_client.delete("/v1/settings/text_rpm", headers=WEB).status_code == 204
    assert web_client.put("/v1/settings/database_url", headers=WEB, json={"value": "x"}).status_code == 404

def test_secret_never_returned(web_client, session):
    login(web_client, session)
    r = web_client.put("/v1/settings/anthropic_api_key", headers=WEB, json={"value": "sk-ant-SECRET-1"})
    assert r.status_code == 200 and "sk-ant-SECRET-1" not in r.text
    secret = next(s for s in r.json()["secrets"] if s["key"] == "anthropic_api_key")
    assert secret["is_set"] is True and secret["source"] == "app" and "value" not in secret
    for path in ("/v1/settings", "/v1/settings/audit"):
        assert "sk-ant-SECRET-1" not in web_client.get(path).text
    stored = session.get(AppSetting, "anthropic_api_key").secret
    assert b"sk-ant-SECRET-1" not in stored

def test_secret_without_storage_is_409_but_clear_works(web_client, session): ...
    # base without secret_key: PUT a string → 409 with the exact message; PUT null → 200

def test_api_keys_lifecycle(web_client, session):
    login(web_client, session)
    r = web_client.post("/v1/api-keys", headers=WEB, json={"name": "janus"})
    assert r.status_code == 201 and r.json()["token"].startswith("jk_")
    token = r.json()["token"]
    listing = web_client.get("/v1/api-keys")
    assert token not in listing.text and "key_hash" not in listing.text
    kid = r.json()["key"]["id"]
    assert web_client.delete(f"/v1/api-keys/{kid}", headers=WEB).json()["revoked_at"]
    assert web_client.get("/v1/books", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    again = web_client.post("/v1/api-keys", headers=WEB, json={"name": "janus"})
    assert again.status_code == 409
    assert again.json()["detail"] == "a key with this name existed; choose another name"
    actions = [a["action"] for a in web_client.get("/v1/settings/audit").json()]
    assert actions[:2] == ["key_revoked", "key_created"]

def test_masked_database_url(web_client, session):
    login(web_client, session)
    system = web_client.get("/v1/settings").json()["system"]
    assert "jerome:jerome@" not in system["database_url"] and "***" in system["database_url"]
```

- [ ] **Step 2: Run** `uv run pytest tests/api/test_settings_api.py -q` → FAIL.
- [ ] **Step 3: Implement** the router, schemas and docs. README text names `JEROME_SECRET_KEY` and how to make one: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, and says losing or changing it means re-entering the secrets on the page. No owner hosts.
- [ ] **Step 4: Run** the full backend suite and lint → all pass.
- [ ] **Step 5: Commit** `feat: settings and API key endpoints`.

---

### Task 7: The Settings page

**Files:**
- Create: `web/src/pages/SettingsPage.tsx`, `web/src/pages/SettingsPage.test.tsx`
- Create: `web/src/components/OneTimeSecretDialog.tsx` (generalize `TempPasswordDialog`'s look: title, description, value, copy button), and make `TempPasswordDialog` use it only if that stays a small change; otherwise leave it alone
- Modify: `web/src/App.tsx` (route `/settings`), `web/src/components/AppShell.tsx` (nav link `Settings` after `Team`, super admin only), `web/src/components/AppShell.test.tsx`

**Interfaces:**
- Consumes: Task 6 endpoints and shapes. Query keys `["settings"]`, `["settings-audit"]`, `["api-keys"]`; every settings or key mutation invalidates all three.
- Produces: the page, with sections in this order and these headings: `AI model`, `Limits`, `Services`, `Secrets`, `API keys`, `System`, `Recent changes`.

Behaviour to build (follow `TeamPage.tsx`: `useMe` gate, `enabled: isSuperAdmin`, the `super admin only` 403 → invalidate `ME_KEY`, `toast.error(err.detail)` on errors):
- Non super admin: the text `Only the super admin can change settings.` and no requests.
- Each runtime setting: current value in its control, a source badge `Default` / `Environment` / `App`, `Changed by <actor> on <date>` when from the app, `Can't be read; using the fallback` when `unreadable`, a `Save` button, and `Reset` only when the source is `App`.
- `AI model`: provider select (`anthropic`, `openai_compat`), model input, effort select (empty → "Default", then the five levels), base URL input only for `openai_compat`. Saved as one `models.ai` value.
- `Limits`: `text_daily_cost_cap`, `text_rpm`, `max_upload_mb`, `session_days` number inputs, each saved on its own, sent as numbers. The `prices` table lives here too: one row per model with the four numbers, `Add model`, a remove button per row, one `Save prices` for the whole table.
- `Services`: `languagetool_url`, `public_base_url` (empty saves `null`).
- `Secrets`: per secret a label (`Anthropic API key`, `Webhook secret`, `OpenAI-compatible API key`), a status `Set (app)` / `Set (environment)` / `Not set` / `Can't be read, enter again`, a password input, `Save` (sends the input, then clears it), and `Clear` (sends `null`, behind a confirm). When `system.secret_storage` is not `ready`, the inputs and `Save` are disabled and the section says `Secret storage is off. Set JEROME_SECRET_KEY on the server, then reload.` (`Clear` stays enabled).
- `API keys`: table of name, created, status (`Active` / `Revoked <date>`), a `Revoke` button behind a confirm; a name input and `Create key`. The token goes straight into component state and is stripped before the mutation resolves (`gcTime: 0`, same pattern as `keepOnce` in `TeamPage`), then shows in `OneTimeSecretDialog` titled `New API key` with `Copy this key now. It won't be shown again.`
- `System`: read-only list of the `system` fields; `secret_storage` shown as `Ready` / `Not set` / `Invalid key`.
- `Recent changes`: table of when, who, setting, action, and `before → after` for non-secrets.

- [ ] **Step 1: Write the failing tests** in `SettingsPage.test.tsx` with the existing `test-utils` render helper and fetch mocks used by `TeamPage.test.tsx`:
  - `shows the member message without fetching` (role `member`: message present, no `/v1/settings` call).
  - `shows sources and saves a limit` (GET fixture; `text_rpm` shows `Default`; typing 12 and `Save` sends `PUT /v1/settings/text_rpm` with `{"value": 12}` as a number).
  - `resets only app values` (`Reset` exists for an `app` row and not for a `default` row; clicking it sends `DELETE`).
  - `secret input is cleared after save and never prefilled` (the password input starts empty even when `is_set`; after `Save` the PUT body has the typed value and the input is empty again).
  - `disables secrets when storage is off` (`secret_storage: "missing"` → message shown, `Save` disabled, `Clear` enabled).
  - `creates a key and shows it once` (POST returns a token; the dialog shows it; after closing, the token text is not in the document).
  - `saves the whole prices table` (edit one number, `Save prices` sends every row).
  - In `AppShell.test.tsx`: `Settings` link only for `super_admin`.
- [ ] **Step 2: Run** `cd web && npx vitest run src/pages/SettingsPage.test.tsx src/components/AppShell.test.tsx` → FAIL.
- [ ] **Step 3: Implement** the page, dialog, route and nav link. Light and dark themes come from the existing tokens; no new colors.
- [ ] **Step 4: Run** `cd web && npm test && npm run build` → all pass.
- [ ] **Step 5: Commit** `feat(web): super admin settings page`.
