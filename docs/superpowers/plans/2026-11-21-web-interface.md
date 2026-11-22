# Jerome Web Interface (first version) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner's team a browser interface for the whole proofreading workflow, covering projects, jobs, inline review and export, with logins. The AI applies clear-cut corrections itself and explains every change.

**Architecture:** The backend gains users and server-side sessions, an auto-apply step in the worker, and a handful of read endpoints. A React + shadcn app in `web/` is built in a Node stage of the Docker image and served by FastAPI at `/`, with an `index.html` fallback. The browser talks only to `/v1`, authenticated by a session cookie.

**Tech Stack:**

| Part | Stack |
|---|---|
| Backend (unchanged) | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, pytest |
| New backend dependency | `argon2-cffi` |
| Frontend | React 19, TypeScript ~5.9, Vite 7, Tailwind CSS 4 (`@tailwindcss/vite`), shadcn/ui (style `new-york`, base colour `slate`, lucide icons; same `components.json` as the Janus web app), react-router-dom 7, @tanstack/react-query 5, sonner |
| Frontend tests | vitest with @testing-library/react and jsdom |

**Spec:** `docs/superpowers/specs/2026-11-21-web-interface-design.md`. Executors read it before Task 1. Section numbers (§) below refer to it.

## Global Constraints

**Repository and data**
- The repo is PUBLIC. Never commit book files or manuscripts.
- Test text is either synthetic (`tests/fixtures/docx_builders.py`, `epub_builders.py`) or the Gutenberg files in `tests/fixtures/public/`.
- Never read books from anywhere else on this machine.
- No secrets in the repo, compose files or tests. `ANTHROPIC_API_KEY` comes only from the environment or `.env`.

**Model**
- Never change or downgrade the product model `claude-opus-5-5`.

**Running tests**
- Backend:
  ```bash
  export JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test
  uv run pytest -q
  ```
  This uses Homebrew Postgres 16. There is no Docker daemon on this Mac.
- Frontend:
  ```bash
  cd web && npm test && npm run build
  ```
  Node 26 and npm 11 are installed. The build includes `tsc -b`.
- Lint: `uv run ruff check . && uv run ruff format --check .` (ruff 0.17).

**API compatibility (§4)**
- Existing endpoints keep their response shape, except `SuggestionOut`, which only gains fields (Task 7).
- API keys keep working everywhere.
- New `Decision.channel` values are `web` (people in the browser) and `auto` (AI auto-apply). Existing values are `api`, `word` and `system`.

**Auth values (§6.2)**
- Cookie name: `jerome_session`. Flags: `HttpOnly`, `SameSite=Lax`, and `Secure` when `Settings.cookie_secure` (default `True`).
- Sessions last 30 days and slide while in use (`Settings.session_days = 30`).
- Cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` requests must carry the header `X-Jerome-Client: web`. Without it the response is 403.
- The actor recorded for a person is their lower-cased email. For AI auto-apply the actor is `ai`.
- Passwords are at least 10 characters. Temporary passwords are generated with `secrets.token_urlsafe(12)`.

**Auto-apply values (§6.3)**
- Default policy: `enabled=True`, `categories={typo, punctuation}`, `min_severity=high`, `house_rules=True`.
- Books existing before migration 0003 get `enabled=False`.
- LanguageTool-only suggestions never auto-apply. Disputed suggestions never auto-apply.

**Copy and polling**
- Interface copy: "Needs you", "Applied by AI", "All", "N left", "both checkers agree", "the checkers disagree", "Run proofreading again", "+ New project".
- Stage names: "Set up", "Proofreading", "Needs attention", "To review", "Done".
- Polling interval while a job is active: 3000 ms.

**Commits**
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `feat/web-interface`. Never push.

## Review Focus

These are the most likely ways the software could hurt a user without any task's main tests noticing. Each has a test pinned in the task named.

1. **Text containing characters outside the Basic Multilingual Plane**, such as 𝔄 or emoji, before a suggestion. API offsets count Unicode code points, but JavaScript strings count UTF-16 units. Marks must still land on the right words. Pinned in Task 13: `cpSlice` and `Paragraph` tests.
2. **Two people deciding the same suggestion at once.** The second person gets a 409. The interface must show the API's message and refresh that paragraph, not leave stale marks. Pinned in Task 13: `ReviewPage` 409 test.
3. **A deactivated teammate's open browser.** Their next request must get a 401 immediately, not when their cookie expires. Pinned in Task 3: `test_deactivating_a_user_ends_their_sessions`.
4. **A LanguageTool-only job on a book with auto-apply on.** Nothing is applied, even high-severity typos such as "cœur → cur". Pinned in Task 6: `test_languagetool_only_job_applies_nothing`.
5. **A very long chapter.** The review endpoint pages by paragraph and the interface loads more pages as you scroll, instead of one huge request. Pinned in Task 8: `test_chapter_review_pages_by_paragraph`. The interface loads the next page when the last loaded paragraph scrolls into view.

---

## Milestone 1: Logins

### Task 1: Schema for users, sessions, auto-apply and suggestion flags

**Files:**
- Create: `migrations/versions/0003_web_interface.py`
- Modify: `jerome/orm.py` (new `User` and `UserSession` classes; new columns on `Book` and `Suggestion`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `User`: table `users`. Fields:
    - `id`;
    - `email: str`, unique, stored lower-cased;
    - `name: str`;
    - `password_hash: str`;
    - `active: bool`, default true;
    - `must_change_password: bool`, default false;
    - `created_at`;
    - `last_login_at: datetime | None`.
  - `UserSession`: table `user_sessions`. Fields:
    - `id`;
    - `user_id`, a foreign key to `users` with `ondelete=CASCADE`, indexed;
    - `token_hash: str`, unique;
    - `created_at`;
    - `expires_at`;
    - `last_seen_at`.
  - `Book.auto_apply: dict`, JSONB, not null.
  - New `Suggestion` columns:
    - `agreed: bool`, server default false;
    - `disputed: bool`, server default false;
    - `alternative: str | None` (Text);
    - `rule: str | None` (String).

- [ ] **Step 1: Write failing tests** in `tests/test_schema.py`:

```python
def test_migration_0003_adds_web_interface_columns(engine):
    insp = inspect(engine)
    assert {"users", "user_sessions"} <= set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("suggestions")}
    assert {"agreed", "disputed", "alternative", "rule"} <= cols
    assert "auto_apply" in {c["name"] for c in insp.get_columns("books")}

def test_books_existing_before_0003_get_auto_apply_off(...):
    # upgrade to 0002, insert a book row with raw SQL, upgrade to head
    assert row.auto_apply == {"enabled": False, "categories": ["punctuation", "typo"],
                              "min_severity": "high", "house_rules": True}
```

Follow how the existing schema tests drive Alembic.

- [ ] **Step 2: Run the tests and confirm they fail.**
  ```bash
  uv run pytest tests/test_schema.py -q
  ```
- [ ] **Step 3: Write migration `0003_web_interface`** (`down_revision` is the 0002 revision) **and the ORM classes.**
  - `books.auto_apply` gets the server default `'{"enabled": false, "categories": ["punctuation", "typo"], "min_severity": "high", "house_rules": true}'::jsonb`, so existing rows get auto-apply off.
  - Categories are serialised sorted.
  - `downgrade()` drops everything the upgrade adds.
- [ ] **Step 4: Run the full suite and confirm it passes.**
  ```bash
  uv run pytest -q
  ```
- [ ] **Step 5: Commit** with the message `feat: schema for users, sessions, auto-apply and suggestion flags`.

### Task 2: Users core and `jerome users create`

**Files:**
- Create: `jerome/core/users.py`, `tests/core/test_users.py`
- Modify: `jerome/cli.py` (new `users` command group), `pyproject.toml` and `uv.lock` (`uv add argon2-cffi`), `tests/test_cli.py`

**Interfaces:**
- Consumes: `User` (Task 1). `Conflict`, `NotFound` and `InvalidChangeError` from `jerome.errors`.
- Produces, all in `jerome/core/users.py`:

| Function | Behaviour |
|---|---|
| `normalize_email(email: str) -> str` | strips and lower-cases |
| `create_user(session, email: str, name: str, password: str \| None = None) -> tuple[User, str \| None]` | When `password` is None, generates a temporary password, sets `must_change_password=True` and returns it. A duplicate email raises `Conflict`. |
| `authenticate(session, email: str, password: str) -> User \| None` | None if the user is unknown, inactive or the password is wrong. On success sets `last_login_at`. |
| `change_password(session, user: User, current: str, new: str) -> None` | A wrong `current` raises `InvalidChangeError`. Clears `must_change_password`. |
| `reset_password(session, user_id: int) -> str` | Returns a new temporary password and sets `must_change_password=True`. |
| `list_users(session) -> list[User]` | ordered by name |
| `update_user(session, user_id: int, *, name: str \| None = None, active: bool \| None = None) -> User` | Deactivating the last active user raises `Conflict("cannot deactivate the last active member")`. |

- Passwords are hashed with `argon2.PasswordHasher`. A password shorter than 10 characters raises `InvalidChangeError`.

- [ ] **Step 1: Write failing tests** in `tests/core/test_users.py`:
  - `test_create_user_with_generated_password_must_change_it`: `pw` is not None, `user.must_change_password`, and `authenticate(session, "ANA@x.org ", pw)` returns `user`.
  - `test_duplicate_email_conflicts`: matching is case-insensitive.
  - `test_authenticate_rejects_wrong_password_and_inactive_user`
  - `test_change_password_needs_current_and_clears_flag`
  - `test_short_password_is_refused`: 9 characters raises `InvalidChangeError`.
  - `test_reset_password_returns_new_temporary_password`
  - `test_cannot_deactivate_last_active_member`

  In `tests/test_cli.py`, add `test_users_create_prints_a_password_once`: run `jerome users create ana@x.org --name Ana`. The exit code is 0, the output is a single line, and `authenticate` works with it.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `jerome/core/users.py` and the CLI command** `users create EMAIL --name NAME`, which prints only the generated password. Mirror `keys create` in `jerome/cli.py`.
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat: team users with hashed passwords and jerome users create`.

### Task 3: Sessions, login API, cookie auth, cross-site guard and throttling

**Files:**
- Create: `jerome/core/sessions.py`, `jerome/api/auth.py`, `jerome/api/routes_auth.py`, `tests/core/test_sessions.py`, `tests/api/test_auth_api.py`
- Modify:
  - `jerome/config.py`: new settings `trusted_proxies: list[str] = []`, `session_days: int = 30`, `cookie_secure: bool = True`, `web_dir: Path | None = None`;
  - `jerome/api/deps.py`: `Principal`, `current_principal`, and `current_actor` delegating to `current_principal`;
  - `jerome/api/app.py`: include the auth router; declare the Bearer scheme;
  - `jerome/api/routes_suggestions.py`: pass `channel` through;
  - `jerome/core/suggestions.py`: widen `channel` to `Literal["api", "word", "web", "auto"]`.

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - In `jerome/core/sessions.py`:

    | Function | Behaviour |
    |---|---|
    | `create_session(session, user: User, *, days: int, now: datetime) -> str` | Returns a raw token (`secrets.token_urlsafe(32)`). Stores only its sha256 hex. |
    | `resolve_session(session, token: str, *, days: int, now: datetime) -> User \| None` | None if the session is unknown, expired or the user is inactive. Otherwise sets `last_seen_at` and slides `expires_at` to `now + days`. |
    | `revoke_session(session, token: str) -> None` | |
    | `revoke_user_sessions(session, user_id: int) -> None` | |

  - In `jerome/api/deps.py`:
    - `@dataclass(frozen=True) class Principal: actor: str; channel: Literal["api", "web"]; user_id: int | None; must_change_password: bool`;
    - `current_principal(request, session) -> Principal`. It checks a Bearer key first, then the `jerome_session` cookie.
      - For a cookie, it requires `X-Jerome-Client: web` on unsafe methods (403 otherwise).
      - While `must_change_password` is set, it answers 403 with detail `"password change required"` on everything except `/v1/auth/me`, `/v1/auth/password` and `/v1/auth/logout`.
    - `current_actor` keeps its signature and returns `principal.actor`.
  - In `jerome/api/auth.py`:
    - `client_ip(request, trusted: list[str]) -> str | None`. Returns the left-most `X-Forwarded-For` address only when the direct peer is in `trusted`; otherwise None.
    - `class LoginThrottle`: in memory. `check(keys: list[str]) -> float | None` returns the seconds to wait. `fail(keys)` and `succeed(keys)` record outcomes. A key is blocked after 5 failures within 15 minutes, for 15 minutes.
  - Routes in `jerome/api/routes_auth.py`, prefix `/v1`:

    | Route | Auth | Behaviour |
    |---|---|---|
    | `POST /auth/login` `{email, password}` | none | Sets the cookie and returns `{id, email, name, must_change_password}`. Wrong details: 401 `"Wrong email or password"`. Throttled: 429 with `Retry-After`. |
    | `POST /auth/logout` | | 204; revokes the session and clears the cookie |
    | `GET /auth/me` | | Returns the same shape as login. Key callers get `{name: <key name>, email: null, ...}`. |
    | `POST /auth/password` `{current, new}` | | 204 |

  - The OpenAPI schema declares an `HTTPBearer` security scheme, so `/docs` shows Authorize. Use `fastapi.security.HTTPBearer(auto_error=False)` inside `current_principal`.

- [ ] **Step 1: Write failing tests.**

  For these tests, a `web_client` fixture (in `tests/conftest.py`) is the existing `client` with `base_url="https://testserver"`, so `Secure` cookies are sent. A helper logs in a created user.

  In `tests/core/test_sessions.py`:
  - `test_session_round_trip_and_sliding_expiry`
  - `test_expired_session_is_refused`
  - `test_inactive_user_session_is_refused`

  In `tests/api/test_auth_api.py`:
  - `test_login_sets_secure_httponly_cookie`: `Set-Cookie` contains `jerome_session=`, `HttpOnly`, `Secure` and `SameSite=lax`.
  - `test_cookie_auth_reads_books_and_records_web_channel`: accept a suggestion through the cookie. Then `Decision.channel == "web"` and `actor == "ana@x.org"`.
  - `test_cookie_post_without_client_header_is_forbidden`: 403.
  - `test_bearer_key_still_works_and_records_api_channel`
  - `test_must_change_password_blocks_other_routes_until_changed`
  - `test_wrong_password_is_401_and_sixth_failure_is_429`
  - `test_deactivating_a_user_ends_their_sessions`: call `update_user(active=False)`, then the next `GET /v1/books` with the old cookie returns 401. *(Review Focus 3)*
  - `test_forwarded_ip_used_only_from_trusted_proxy`: unit-tests `client_ip`.
  - `test_logout_clears_cookie`
  - `test_openapi_declares_bearer_scheme`
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
  - Throttle keys are `email:<normalized>`, plus `ip:<addr>` when `client_ip` returns one.
  - `update_user(active=False)` must call `revoke_user_sessions`. Do this inside `jerome/core/users.py` so every path does it.
  - Suggestion routes pass `principal.channel` to `decide` and `bulk_decide`.
- [ ] **Step 4: Run the full suite** (`uv run pytest -q`) and confirm it passes.
- [ ] **Step 5: Commit** with the message `feat: session logins with cookie auth, cross-site guard and throttling`.

### Task 4: Team API

**Files:**
- Create: `jerome/api/routes_users.py`, `tests/api/test_users_api.py`
- Modify: `jerome/api/app.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces routes under `/v1`, available to any authenticated principal:

| Route | Response |
|---|---|
| `GET /users` | `[{id, email, name, active, must_change_password, last_login_at, created_at}]` |
| `POST /users` `{email, name}` | 201, `{user: {...}, temporary_password}` |
| `PATCH /users/{id}` `{name?, active?, reset_password?: bool}` | `{user: {...}, temporary_password: str \| null}` |

- Deactivating the last active member returns 409.

- [ ] **Step 1: Write failing tests:**
  - `test_add_member_returns_temporary_password_once`: a later `GET /users` never contains a password field.
  - `test_reset_password_returns_new_temporary_password`
  - `test_deactivate_last_active_member_is_409`
  - `test_team_routes_need_auth`
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement the routes.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat: team members API`.

## Milestone 2: AI applies the clear-cut changes

### Task 5: Merge records agreement and disputes; consistency marks its rule

**Files:**
- Modify:
  - `jerome/types.py`: `Candidate` gains `agreed: bool = False`, `disputed: bool = False`, `alternative: str | None = None`, `rule: str | None = None`;
  - `jerome/proofread/merge.py`;
  - `jerome/proofread/consistency.py`: each candidate builder sets `rule`;
  - `jerome/core/suggestions.py`: `add_candidates` stores the four fields, and `AddResult` gains `rows: list[Suggestion]`.
- Test: `tests/proofread/test_merge.py`, `tests/proofread/test_consistency.py`, `tests/core/test_suggestions.py`

**Interfaces:**
- Produces:
  - `merge_candidates(candidates) -> list[Candidate]` keeps its signature.
  - New behaviour for twins, where `(block_id, start, end, replacement)` is equal: the kept candidate is the AI's (`source=MODEL`) with the AI's category, severity and explanation, and `agreed=True`. This holds whichever twin ranked higher.
  - New behaviour for a dropped overlapping candidate from a *different source* with a *different replacement*: the kept candidate gets `disputed=True` and `alternative=<dropped replacement>`. The first dropped alternative wins.
  - Consistency `rule` values:
    - `"house_spelling"` from `_house_candidates`;
    - `"quote_style"` from `_quote_candidates`;
    - `"variant"` from `_variant_candidates`;
    - `"name"` from `_name_candidates`.
  - `AddResult.rows` holds the `Suggestion` rows created by this call, in creation order.

- [ ] **Step 1: Write failing tests:**
  - `test_twins_keep_the_ai_rating_and_mark_agreed`: LanguageTool `high` and the AI `medium` make the same fix. The kept candidate has `source == MODEL`, `severity == MEDIUM` and `agreed`.
  - `test_different_fix_for_overlapping_span_marks_disputed_with_alternative`
  - `test_same_source_overlap_is_not_a_dispute`
  - `test_consistency_candidates_carry_their_rule`: one test per rule.
  - `test_add_candidates_stores_flags_and_returns_rows`
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.** Use `dataclasses.replace` on the frozen `Candidate`.
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** with the message `feat: keep checker agreement, disputes and consistency rule on suggestions`.

### Task 6: Auto-apply policy, worker step, reversing an AI change

**Files:**
- Create: `jerome/core/auto_apply.py`, `tests/core/test_auto_apply.py`
- Modify:
  - `jerome/jobs/worker.py`: `_Plan.policy`; call after `add_candidates` in `_run_chunk` and `_run_consistency`; `job.stats` key `auto_applied`;
  - `jerome/core/suggestions.py`: `decide` lets `reject` and `edit` act on an auto-applied suggestion;
  - `jerome/core/books.py`: `import_book(..., auto_apply: AutoApplyPolicy = DEFAULT_POLICY)`;
  - `jerome/api/routes_books.py`: the upload form gains `auto_apply: bool = True`;
  - `jerome/core/exports.py`: tracked changes from auto decisions get the author `"Jerome AI"`.
- Test: `tests/core/test_auto_apply.py`, `tests/jobs/test_worker.py`, `tests/core/test_suggestions.py`, `tests/core/test_exports.py`

**Interfaces:**
- Consumes: Task 5 (`AddResult.rows`, `Suggestion.rule`, `disputed`). `decide(..., channel=...)` from Task 3.
- Produces, in `jerome/core/auto_apply.py`:
  - `@dataclass(frozen=True) class AutoApplyPolicy: enabled: bool; categories: frozenset[Category]; min_severity: Severity; house_rules: bool`;
  - `DEFAULT_POLICY`, the values in Global Constraints;
  - `OFF_POLICY = replace(DEFAULT_POLICY, enabled=False)`;
  - `policy_to_json(p) -> dict` with categories sorted, and `policy_from_json(d) -> AutoApplyPolicy`;
  - `get_policy(session, book_id) -> AutoApplyPolicy` and `set_policy(session, book_id, p) -> AutoApplyPolicy`;
  - `qualifies(s: Suggestion, p: AutoApplyPolicy) -> bool`, with this rule:

    ```
    not p.enabled or s.disputed             -> False
    s.source == "consistency":               -> p.house_rules and s.rule in ("house_spelling", "quote_style")
    otherwise                                -> s.source == "model" and Category(s.category) in p.categories
                                                and Severity(s.severity).rank >= p.min_severity.rank
    ```

  - `apply_clear_cut(session, rows: Sequence[Suggestion], p: AutoApplyPolicy) -> int` accepts each qualifying open row with `decide(session, s.id, "accept", actor="ai", channel="auto")`. It skips rows that `decide` refuses with `Conflict`, and returns how many it applied.
  - `is_auto_applied(session, suggestion_ids) -> set[int]`: rows whose status is `accepted` and whose latest non-`system` decision has `channel == "auto"`.
- `decide` changes: `reject` and `edit` are also allowed when the suggestion is auto-applied, with `previous_status` `accepted`. They log one decision. A person's own accept still cannot be rejected without an undo (409, as today).

- [ ] **Step 1: Write failing tests.** In `tests/core/test_auto_apply.py`:
  - `test_default_policy_applies_ai_high_typos_and_punctuation_only`: the AI with grammar `high` is not applied; the AI with typo `medium` is not applied.
  - `test_languagetool_alone_never_applies`: a LanguageTool typo `high` "cœur" → "cur" stays open.
  - `test_disputed_never_applies`
  - `test_house_rules_apply_but_variants_and_names_do_not`
  - `test_house_rules_toggle_off`
  - `test_disabled_policy_applies_nothing`
  - `test_policy_json_round_trip`
  - `test_auto_decision_is_logged_as_ai_on_auto_channel`

  In `tests/core/test_suggestions.py`:
  - `test_reject_reverts_an_auto_applied_change_in_one_step`: status becomes `rejected` with one new decision, `previous_status="accepted"`, `channel="web"`.
  - `test_edit_an_auto_applied_change`
  - `test_reject_after_a_persons_accept_is_still_409`

  In `tests/jobs/test_worker.py`:
  - `test_chunk_auto_applies_and_counts_in_stats`
  - `test_languagetool_only_job_applies_nothing` *(Review Focus 4)*
  - `test_consistency_pass_applies_house_spellings`

  In `tests/core/test_books.py`:
  - `test_new_books_default_to_auto_apply_on`
  - `test_upload_with_auto_apply_false_stores_off_policy`

  In `tests/core/test_exports.py`:
  - `test_tracked_export_marks_auto_changes_as_jerome_ai`: the `w:author` of the auto change is `Jerome AI`, and a person's change keeps `Jerome`.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
  - The policy is read once per task in `_start` (`get_policy`) into `_Plan.policy`.
  - `apply_clear_cut` runs on `added.rows` inside the same transaction as `add_candidates`.
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** with the message `feat: AI applies clear-cut suggestions; people can reverse them in one step`.

## Milestone 3: Read endpoints for the interface

### Task 7: Richer suggestions, totals, jobs list, auto-apply settings

**Files:**
- Modify:
  - `jerome/api/schemas.py`: `SuggestionOut` gains `chapter_id: int`, `agreed: bool`, `disputed: bool`, `alternative: str | None`, `rule: str | None`, `auto_applied: bool`, `decided_by: str | None`;
  - `jerome/core/suggestions.py`: `list_suggestions` returns rows plus a total;
  - `jerome/api/routes_suggestions.py`;
  - `jerome/api/routes_jobs.py`;
  - `jerome/api/routes_books.py`.
- Test: `tests/api/test_suggestions_api.py` (update the `KEYS` set), `tests/api/test_jobs_api.py`, `tests/api/test_books_api.py`

**Interfaces:**
- Consumes: `is_auto_applied`, `get_policy`, `set_policy` and `policy_*_json` (Task 6).
- Produces:
  - `suggestion_out(session, rows: Sequence[Suggestion]) -> list[dict]` in `jerome/api/routes_suggestions.py`. It is reused by Task 8 and fills the new fields in a fixed number of queries, not one per row.
    - `decided_by` is the actor of the latest decision whose channel is not `system`, or None.
  - `GET /v1/books/{id}/suggestions` keeps its list body and adds the header `X-Total-Count`.
  - `GET /v1/books/{id}/jobs` returns `[_job_json(job)]`, newest first.
  - `GET /v1/books/{id}/auto-apply` and `PUT /v1/books/{id}/auto-apply` use the body `{enabled: bool, categories: list[Category], min_severity: Severity, house_rules: bool}`. An empty `categories` list is allowed.

- [ ] **Step 1: Write failing tests:**
  - `test_suggestion_out_has_new_fields_and_auto_flag`
  - `test_decided_by_is_the_latest_person_or_ai`
  - `test_list_sets_total_count_header`
  - `test_book_jobs_listed_newest_first`
  - `test_auto_apply_settings_get_put_round_trip`
  - `test_auto_apply_put_rejects_unknown_category_422`
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** with the message `feat: suggestion flags, totals, book jobs and auto-apply settings endpoints`.

### Task 8: Projects list with stages, chapter summary, chapter review

**Files:**
- Create: `jerome/core/projects.py`, `jerome/api/routes_projects.py`, `tests/core/test_projects.py`, `tests/api/test_projects_api.py`
- Modify: `jerome/api/app.py`

**Interfaces:**
- Consumes: `suggestion_out` (Task 7), `is_auto_applied` (Task 6), `current_text` and `ACTIVE_JOB_STATUSES`.
- Produces:
  - In `jerome/core/projects.py`:
    - `Stage = Literal["set_up", "proofreading", "needs_attention", "to_review", "done"]`;
    - `derive_stage(*, active_job: bool, latest_job_status: str | None, open_count: int, completed_jobs: int) -> Stage`. It applies §6.4's table top to bottom:
      - an active job → `proofreading`;
      - otherwise `latest_job_status in ("failed", "paused_cost_cap")` → `needs_attention`;
      - otherwise `open_count > 0` → `to_review`;
      - otherwise `completed_jobs > 0` → `done`;
      - otherwise `set_up`.

      Note that `paused_cost_cap` is in `ACTIVE_JOB_STATUSES`. Treat it as *not* active for staging.
    - `list_projects(session) -> list[dict]`. It uses a fixed number of queries for all books together.
  - `GET /v1/projects` returns, ordered by `last_activity.at` descending (books with no activity by `created_at`):

    ```
    [{id, title, format, variant, stage, open_count, auto_applied_count, decided_count,
      active_job: {id, status, chapters_done, chapters_total, cost_actual, max_cost} | null,
      last_activity: {actor, at} | null, created_at}]
    ```
  - `GET /v1/books/{id}/chapter-summary` returns `[{chapter_id, ordinal, title, proofread, open, auto_applied, decided}]`, ordered by ordinal.
  - `GET /v1/chapters/{id}/review?offset=0&limit=50` (limit between 1 and 200) returns:

    ```
    {chapter_id, book_id, total_blocks, offset,
     blocks: [{id, ordinal, kind, original, current, suggestions: [SuggestionOut...]}]}
    ```

    Blocks are ordered by ordinal. Suggestions within a block are ordered by `start`.

- [ ] **Step 1: Write failing tests:**
  - `test_derive_stage_table`: parametrised over the five rows plus "only cancelled jobs → set_up" and "paused_cost_cap → needs_attention".
  - `test_projects_list_one_request_shape_and_order`
  - `test_projects_list_query_count_is_constant`: count statements with a SQLAlchemy event listener for 1 book and for 5 books; the counts are equal.
  - `test_chapter_summary_counts`
  - `test_chapter_review_returns_current_text_and_suggestions`
  - `test_chapter_review_pages_by_paragraph`: 120 blocks with `limit=50&offset=100` returns 20 blocks and `total_blocks == 120`. *(Review Focus 5)*
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite and confirm it passes.**
- [ ] **Step 5: Commit** with the message `feat: projects list with stages, chapter summary and chapter review endpoints`.

## Milestone 4: The interface

### Task 9: Web app scaffold, API client, serving from FastAPI, Docker build

**Files:**
- Create:
  - `web/`: Vite React TS app. `package.json` scripts: `dev`, `build` (`tsc -b && vite build`), `test` (`vitest run`). Also `components.json` copied from the Janus web app, `vite.config.ts` (alias `@` → `src`; dev proxy `/v1` → `http://127.0.0.1:8000`), `src/index.css` (Tailwind 4 plus shadcn tokens, light and dark by `prefers-color-scheme`), `src/main.tsx`, `src/App.tsx` (router), `src/lib/api.ts`, `src/lib/api.test.ts`;
  - `jerome/api/web.py`;
  - `tests/api/test_web_serving.py`.
- Modify: `jerome/api/app.py` (calls `mount_web` when `settings.web_dir` is set), `Dockerfile`, `.dockerignore` (add `web/node_modules` and `web/dist`), `.gitignore` (add `web/node_modules` and `web/dist`).

**Interfaces:**
- Produces:
  - In `web/src/lib/api.ts`:
    - `class ApiError extends Error { status: number; detail: string }`;
    - `apiFetch<T>(path: string, init?: RequestInit & { json?: unknown }) -> Promise<T>`. It always sends `credentials: "same-origin"` and `X-Jerome-Client: web`, and serialises `json`. On a non-2xx response it throws `ApiError` with the API's `detail`. On 401 it navigates to `/login`, and on 403 `"password change required"` to `/change-password`. It returns `undefined` for 204.
  - In `jerome/api/web.py`, `mount_web(app: FastAPI, web_dir: Path) -> None`:
    - It serves `web_dir/assets/*` with `Cache-Control: public, max-age=31536000, immutable`. A missing asset is a 404.
    - Every other GET path not starting with `/v1`, `/docs`, `/redoc` or `/openapi.json` returns `web_dir/index.html` with `Cache-Control: no-cache`.
  - Docker:
    - The first stage is `FROM node:22-slim AS web`: `WORKDIR /web`, copy `web/package.json` and `web/package-lock.json`, `npm ci`, copy `web/`, `npm run build`.
    - The Python stage adds `COPY --from=web /web/dist /app/web` and `ENV JEROME_WEB_DIR=/app/web`.

- [ ] **Step 1: Write failing tests.**
  - In `tests/api/test_web_serving.py`, using a temporary `web_dir` with `index.html` and `assets/app.js`:
    - `test_deep_link_returns_index_html_no_cache`
    - `test_assets_are_long_cached`
    - `test_missing_asset_is_404_not_index`
    - `test_api_and_docs_paths_are_not_swallowed`
    - `test_no_web_dir_means_api_only`
  - In `web/src/lib/api.test.ts`: the client sends the header and `credentials`, throws `ApiError` with `detail`, and returns `undefined` on 204.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Scaffold `web/` and add shadcn components:** button, input, label, card, table, badge, dialog, alert-dialog, dropdown-menu, select, checkbox, tabs, progress, textarea, separator, tooltip, sonner. Implement `api.ts`, `mount_web` and the Dockerfile stage. Commit `package-lock.json`.
- [ ] **Step 4: Verify.** Run `uv run pytest -q`, then `cd web && npm test && npm run build`. Also run `docker compose -f docker-compose.coolify.yml config` (the docker CLI works without a daemon); it must still parse.
- [ ] **Step 5: Commit** with the message `feat: web app scaffold served by FastAPI, built in the Docker image`.

### Task 10: Login, change password, app shell, Team page

**Files:**
- Create: `web/src/pages/LoginPage.tsx`, `ChangePasswordPage.tsx`, `TeamPage.tsx`, `web/src/components/AppShell.tsx`, `web/src/hooks/useMe.ts`, and tests next to each page (`*.test.tsx`).

**Interfaces:**
- Consumes: `apiFetch` (Task 9). The auth and users routes (Tasks 3 and 4).
- Produces:
  - Routes: `/login`, `/change-password`, `/team`.
  - `AppShell` has a top bar with "Jerome", the links Projects (`/`) and Team (`/team`), and an account menu with "Change password" and "Log out". It wraps every route except `/login` and `/change-password`.
  - `useMe()` is a react-query hook over `GET /v1/auth/me`.

- [ ] **Step 1: Write failing component tests** with a mocked `fetch`:
  - Login shows "Wrong email or password" on 401 and shows the wait on 429.
  - Login goes to `/change-password` when `must_change_password` is set.
  - The Team page's add form shows the temporary password once, in a dialog with a copy button.
  - Deactivating the last member shows the 409 message as a toast.
- [ ] **Step 2: Run** `npm test` **and confirm the tests fail.**
- [ ] **Step 3: Implement the pages** with shadcn components.
- [ ] **Step 4: Run** `npm test && npm run build` **and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat(web): login, password change, app shell and team page`.

### Task 11: Projects home and New project

**Files:**
- Create: `web/src/pages/ProjectsPage.tsx`, `NewProjectPage.tsx`, `web/src/components/StageBadge.tsx`, `RunningJobsStrip.tsx`, and tests.

**Interfaces:**
- Consumes: `GET /v1/projects` (Task 8). `POST /v1/books` (multipart: `file`, `variant`, `title`, `auto_apply`). The auto-apply GET and PUT (Task 7).
- Produces:
  - Routes: `/` and `/projects/new`.
  - `StageBadge({ stage })` maps the API stage to its label (Global Constraints).
  - The next-step button, per stage:

    | Stage | Button |
    |---|---|
    | `set_up` | Start proofreading → `/projects/:id` |
    | `proofreading` | View progress → `/projects/:id` |
    | `needs_attention` | Resume → `/projects/:id` |
    | `to_review` | Review (N left) → `/projects/:id/review` |
    | `done` | Export → `/projects/:id` |

- [ ] **Step 1: Write failing tests:**
  - The table renders rows in API order, each with one next-step button and the right label.
  - The search filters by title.
  - The running strip polls every 3000 ms (fake timers) while a job is active, and stops when none is.
  - New project pre-fills the title from the file name. On a 413 or 422 it shows the API's detail and stays on the page.
  - On success it navigates to `/projects/:id`.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.** Language options: English (US) `en-US`, English (GB) `en-GB`, German (DE) `de-DE`, German (AT) `de-AT`, German (CH) `de-CH`. The collapsed "House style" section saves through `PUT /v1/books/{id}/style-sheet` after the upload.
- [ ] **Step 4: Run** `npm test && npm run build` **and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat(web): projects home and new project`.

### Task 12: Project page

**Files:**
- Create: `web/src/pages/ProjectPage.tsx`, `web/src/components/project/StartPanel.tsx`, `ProgressPanel.tsx`, `SummaryPanel.tsx`, `JobsList.tsx`, `ExportMenu.tsx`, `ReviewUpload.tsx`, `SettingsTab.tsx`, and tests.

**Interfaces:**
- Consumes these endpoints:
  - `GET /v1/books/{id}`;
  - `/chapters` and `PATCH /v1/chapters/{id}`;
  - `POST /v1/books/{id}/estimate`;
  - `POST /v1/books/{id}/jobs`;
  - `GET /v1/jobs/{id}`, `/cancel` and `/resume`;
  - `GET /v1/books/{id}/jobs` and `/chapter-summary`;
  - `GET /v1/books/{id}/export?format=…`;
  - `POST /v1/books/{id}/review-imports`;
  - the style sheet and auto-apply endpoints;
  - `DELETE /v1/books/{id}`.
- Produces: the route `/projects/:id`.
- `StartPanel` (Set up and "Run proofreading again"):
  - chapter checkboxes, initially ticked when `proofread` is true;
  - pass choice: "AI + LanguageTool" or "Free check (LanguageTool only)";
  - the estimate is re-fetched when the choice changes;
  - the cost limit defaults to `Math.ceil(Number(estimate.total.high))` dollars, with a minimum of 1;
  - the Start button.

- [ ] **Step 1: Write failing tests:**
  - The page shows the panel for each stage.
  - The cost limit defaults to the rounded-up high estimate. For example, `"3.2"` gives 4, and `"0"` gives 1.
  - "Run proofreading again" is shown whenever no job is active.
  - Progress polls every 3000 ms and stops when the job completes.
  - Resume sends an optional higher `max_cost`.
  - The export menu lists only formats valid for the book. A DOCX book gets all four; an EPUB book gets "EPUB clean" only.
  - Delete asks for confirmation, then navigates to `/`.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test && npm run build` **and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat(web): project page with start, progress, summary, exports and settings`.

### Task 13: Inline review screen

**Files:**
- Create: `web/src/pages/ReviewPage.tsx`, `web/src/components/review/Paragraph.tsx`, `FocusCard.tsx`, `ReviewToolbar.tsx`, `web/src/hooks/useReviewKeys.ts`, `web/src/lib/offsets.ts`, and tests (`offsets.test.ts`, `Paragraph.test.tsx`, `ReviewPage.test.tsx`).

**Interfaces:**
- Consumes:
  - `GET /v1/books/{id}/chapter-summary`;
  - `GET /v1/chapters/{id}/review`, paged;
  - `POST /v1/suggestions/{id}/accept`, `/reject`, `/edit` and `/undo`;
  - `POST /v1/suggestions/bulk`;
  - the `SuggestionOut` fields from Task 7.
- Produces:
  - Route: `/projects/:id/review/:chapterId?`. Without a chapter it opens the first chapter with open items.
  - `cpSlice(text: string, start: number, end: number) -> string` and `cpToUtf16(text: string, cp: number) -> number` convert code-point offsets.
  - `Paragraph` renders `block.original` split at suggestion offsets:
    - **Needs you** (status `open`): underlined. The `disputed` badge says "the checkers disagree" and shows `alternative`.
    - **Applied by AI** (`auto_applied`): struck original plus new text in the AI colour, with the note in a margin aside.
    - **Decided by the team**: subtle, with `decided_by` on hover.
    - **Rejected**: original text only.
  - `useReviewKeys` maps the keys:

    | Key | Action |
    |---|---|
    | `a` | accept |
    | `r` | reject; also reverts AI |
    | `e` | edit |
    | `j` / `k` | next / previous suggestion in the current view |
    | `u` | undo the focused decided suggestion, including a teammate's decision in the All view; with nothing decided in focus, undo the last decision made in this page session |
    | `Escape` | close the edit field |

    Keys are ignored while focus is in an input or textarea.
  - The counter "N left" counts only `status === "open"` in the chapter, using the chapter summary's `open`.
  - Views: Needs you (default), Applied by AI, All. Filters: category and severity.
  - Bulk: "Accept all N remaining <category> in this chapter", with confirmation, using `filter: {chapter_id, category, status: "open"}`.
  - When the last open item in a chapter is decided, a prompt links to the next chapter with `open > 0`.

- [ ] **Step 1: Write failing tests:**
  - `offsets.test.ts`: `cpSlice("𝔄 teh cat", 2, 5) === "teh"`. *(Review Focus 1)*
  - `Paragraph.test.tsx`:
    - with "𝔄" before the suggestion, the needs-you mark wraps exactly "teh";
    - an auto-applied change shows the struck original, the new text and its explanation;
    - an agreed suggestion shows "both checkers agree".
  - `ReviewPage.test.tsx`:
    - `j` and `a` accept the focused suggestion, and the counter drops by one;
    - `r` on an AI-applied change calls `/reject`;
    - typing `a` inside the edit textarea does not accept;
    - a 409 on accept shows the API detail as a toast and refetches that chapter page *(Review Focus 2)*;
    - the Applied by AI view lists only auto-applied changes;
    - the bulk action confirms, then posts the chapter and category filter;
    - deciding the last open item shows the next-chapter prompt.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.** Pages of 50 paragraphs; the next page loads when the last paragraph scrolls into view (`IntersectionObserver`).
- [ ] **Step 4: Run** `npm test && npm run build` **and confirm they pass.**
- [ ] **Step 5: Commit** with the message `feat(web): inline review screen with AI-applied changes and notes`.

### Task 14: End-to-end check and docs

**Files:**
- Create: `tests/test_web_end_to_end.py`
- Modify: `README.md` (a "Web interface" section and deployment notes), `.env.example` (commented `JEROME_TRUSTED_PROXIES=`)

**Interfaces:**
- Consumes everything above.

- [ ] **Step 1: Write a failing end-to-end test** in `tests/test_web_end_to_end.py`. Use the API with a cookie session (not the browser), with the fake model provider and LanguageTool fakes the existing end-to-end test uses, on `tests/fixtures/public/pg932.epub`:
  - create a user; log in; change the password;
  - upload with auto-apply on;
  - run an AI + LanguageTool job through the worker to completion;
  - assert that the project's stage is `to_review` and that some suggestions are auto-applied with `decided_by == "ai"`;
  - revert one with `/reject`; accept one open;
  - the clean EPUB export downloads.
- [ ] **Step 2: Run the test and confirm it fails** if something is missing; fix anything it finds.
- [ ] **Step 3: Write the README section:**
  - what the interface does;
  - first login: `jerome users create EMAIL --name NAME`, run on Coolify through a one-off scheduled task;
  - `JEROME_TRUSTED_PROXIES`: behind a reverse proxy, set it to the Docker gateway address of the Jerome network so per-IP login throttling sees real client addresses;
  - local development: `uv run jerome serve` plus `cd web && npm run dev`.
- [ ] **Step 4: Verify.** Run `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`, and `cd web && npm test && npm run build`. All must pass.
- [ ] **Step 5: Commit** with the message `docs: web interface usage and deployment; end-to-end check`.
