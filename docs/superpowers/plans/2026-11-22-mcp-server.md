# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jerome serves an MCP endpoint at `/mcp`. Janus, signed in with an API key, can do what an editor does through REST: list and import books, run proofreading, review suggestions, change book settings, and round-trip Word files. Every change Janus makes is visible and attributed to it.

**Architecture:**
- **Endpoint.** The official MCP Python SDK (`FastMCP`, stateless Streamable HTTP, JSON responses) is mounted inside the existing FastAPI app.
- **Tools.** Each tool calls the same REST handler function (or core function) the route uses, so validation, limits and result shapes match REST.
- **Attribution.** Decisions record channel `mcp`, and every successful write also leaves a row in a new `agent_actions` log.
- **Files.** Files move through single-use upload and download links under `/mcp/files/…`.

**Tech Stack:** Python 3.12, uv, FastAPI, the `mcp` Python SDK, SQLAlchemy 2, Alembic, Postgres 16, pytest. Web: React 19, TypeScript, react-query 5, vitest.

**Spec:** `docs/superpowers/specs/2026-11-22-mcp-server-design.md`

## Global Constraints

- **Endpoint:** `/mcp` on the app from `create_app`, outside `/v1`. Streamable HTTP, **stateless**, **JSON responses** (no SSE stream).
- **SDK:** the official MCP Python SDK, added with `uv add mcp` and pinned to the exact resolved version (`mcp==X.Y.Z`) in `pyproject.toml`.
- **Auth:**
  - Every `/mcp` request except `/mcp/files/…` needs `Authorization: Bearer <API key>`, checked by `jerome.core.keys.verify_api_key`.
  - A missing, unknown or revoked key gets HTTP **401** before the MCP layer runs.
  - A session cookie is **never** accepted on `/mcp`.
- **Identity:** `Principal(actor=<key name>, channel="mcp", user_id=None, must_change_password=False)`.
- **Decisions** made through tools record `actor=<key name>`, `channel="mcp"`.
- **Not exposed as tools:** deleting a book, team management, sign-in, passwords, API keys.
- **Tool names** (exactly):
  - `list_projects`, `list_books`, `get_book`, `list_chapters`, `set_chapter_proofread`;
  - `request_upload`, `import_book`;
  - `get_style_sheet`, `set_style_sheet`, `get_auto_apply`, `set_auto_apply`;
  - `estimate_job`, `start_job`, `list_jobs`, `get_job`, `cancel_job`, `resume_job`;
  - `get_chapter_summary`, `get_chapter_review`, `list_suggestions`, `get_block`, `decide_suggestion`, `edit_suggestion`, `bulk_decide`;
  - `export_book`, `import_review_copy`, `get_review_import`;
  - `proofread_text`.
- **Tool errors:** `isError: true`, and the text is `<code>: <same message REST puts in detail>`. Codes:
  - `not_found` for `NotFound`;
  - `conflict` for `Conflict`;
  - `invalid` for `InvalidChangeError`, `UnsupportedDocumentError`, `UnknownModelPrice`, pydantic validation errors and other 400/422 cases;
  - `rate_limited` for a 429;
  - `error` for anything unexpected, with the text `error: internal error`, logged server-side.
- **Transactions:** one per tool call, committed on success and rolled back on any error.
- **Links:**
  - single use; expire **15 minutes** after creation;
  - an upload accepts at most `max_upload_mb`;
  - an unknown, used or expired token gets **404**;
  - only the token's sha256 hash is stored;
  - an upload can be consumed only by the key that requested it.
- **Activity summaries** (exact copy, used in tests):
  - `import_book`: `Imported "<title>"`;
  - `import_review_copy`: `Imported a review copy`;
  - `set_style_sheet`: `Changed the style sheet`;
  - `set_auto_apply`: `Turned auto-apply on` or `Turned auto-apply off` when `enabled` changed, otherwise `Changed auto-apply settings`;
  - `set_chapter_proofread`: `Marked a chapter as proofread` or `Marked a chapter as not proofread`;
  - jobs: `Started job <id>`, `Cancelled job <id>`, `Resumed job <id>`;
  - `decide_suggestion`: `Accepted a suggestion`, `Rejected a suggestion`, `Undid a decision`;
  - `edit_suggestion`: `Edited a suggestion`;
  - `bulk_decide`: `Accepted <n> suggestions` or `Rejected <n> suggestions`.
- **Web label:** a decision on channel `mcp` reads `Janus (<key name>)`. The activity page title is `Janus activity`.
- **Repo rules:** the repo is public; use only `tests/fixtures/public/` or the builders in `tests/fixtures/docx_builders.py`.
- **Test database:** shared; never run two pytest jobs at once.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

These five things are likely to hurt a real user, and no single task's tests cover them on their own:

1. **An upload PUT with a body larger than `max_upload_mb`.** It must get 413 and leave no file on disk. A truncated file must never be accepted. (Task 6 test `test_upload_too_large_leaves_nothing`.)
2. **A tool called with arguments that REST would reject.** An unknown pass name, a chapter of another book, or a negative `max_cost` must get the same refusal as REST, as an `invalid:`/`not_found:` tool error, not a 500. (Task 4 test `test_start_job_rejects_what_rest_rejects`.)
3. **Two decisions on the same suggestion through MCP and the web at once.** The existing 409 conflict rules apply, and the loser gets `conflict:`. (Task 5 test `test_decide_conflict_maps_to_conflict`.)
4. **A revoked key mid-session.** Stateless mode checks the key on every request, so the next call after revocation gets 401. (Task 2 test `test_revoked_key_is_refused_on_next_call`.)
5. **The proofread_text rate limit and daily cap.** They are shared with REST for the same key, so MCP must not become a way around them. (Task 4 test `test_proofread_text_shares_rest_rate_limit`.)

---

### Task 1: Data — `mcp` channel, `agent_actions`, `transfer_links`

**Files:**
- Create: `migrations/versions/0006_mcp.py`
- Modify: `jerome/orm.py` (`CHANNELS`, new models `AgentAction`, `TransferLink`), `jerome/core/suggestions.py:17` (`Channel`), `jerome/api/deps.py` (`Principal.channel` type if typed)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `CHANNELS = ("api", "word", "system", "web", "auto", "mcp")`;
  - `Channel = Literal["api", "word", "web", "auto", "mcp"]`;
  - `class AgentAction(Base)`, table `agent_actions`, with columns:
    - `id`, and `at` (timestamptz, default now, indexed);
    - `actor` (str), and `channel` (str, check `channel IN ('mcp')`);
    - `tool` (str);
    - `book_id`, `chapter_id`, `job_id`: nullable ints. The `book_id` FK to `books.id` has `ondelete="SET NULL"` and is indexed. The other two have no FK.
    - `summary` (str);
    - `before`, `after` (JSONB, nullable);
  - `class TransferLink(Base)`, table `transfer_links`, with columns:
    - `id`, and `token_hash` (str, unique);
    - `kind` (check `kind IN ('upload', 'download')`);
    - `actor` (str), and `filename` (str, nullable);
    - `media_type` (str, nullable), and `size` (int, nullable);
    - `path` (str, nullable), relative to `data_dir`;
    - `created_at`, `expires_at`;
    - `received_at`, `used_at` (nullable).

- [ ] **Step 1: Write the failing tests** in `tests/test_schema.py`, following the 0005 test's pattern (`alembic_config` helper, downgrade inside `try`):
  - `test_migration_0006_adds_mcp_channel_and_tables`: after upgrading to head, these all succeed:
    - inserting a decision with `channel='mcp'`;
    - inserting an `agent_actions` row;
    - inserting a `transfer_links` row.

    Inserting `channel='bogus'` raises `IntegrityError`.
  - `test_migration_0006_downgrade_refuses_with_mcp_data`: with one `channel='mcp'` decision present, `downgrade("0005")` raises, and the error message contains `mcp`. After deleting it, inserting one `agent_actions` row makes the downgrade raise again. With neither present, the downgrade succeeds and both tables are gone.
- [ ] **Step 2: Run** `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q tests/test_schema.py -k 0006`. Expected: FAIL (no revision 0006).
- [ ] **Step 3: Implement** `0006_mcp.py`, the ORM models and `CHANNELS`/`Channel`.
  - The upgrade replaces `ck_decisions_channel` with the list including `'mcp'`, as 0004 does, and creates both tables.
  - The downgrade first checks `SELECT 1 FROM decisions WHERE channel='mcp'` and `SELECT 1 FROM agent_actions`. If either returns a row, it raises `RuntimeError("cannot downgrade 0006: Janus (mcp) data exists")`. Otherwise it drops both tables and restores the 0004 constraint.
- [ ] **Step 4: Run** the step 2 command, then the full `tests/test_schema.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: mcp decision channel, agent action log and transfer link tables`.

### Task 2: The `/mcp` endpoint — auth, tool context, error mapping, first tool

**Files:**
- Create:
  - `jerome/mcp/__init__.py`;
  - `jerome/mcp/server.py`: builds `FastMCP`, mounts it, sets up the lifespan;
  - `jerome/mcp/auth.py`: ASGI auth middleware;
  - `jerome/mcp/context.py`: per-call context;
  - `jerome/mcp/errors.py`;
  - `jerome/mcp/tools_books.py`: only `list_books` in this task.
- Modify: `jerome/api/app.py` (FastAPI `lifespan`, `mount_mcp(app)` before `mount_web`), `pyproject.toml`
- Test: `tests/mcp/__init__.py`, `tests/mcp/conftest.py`, `tests/mcp/test_endpoint.py`

**Interfaces:**
- Consumes: `Principal` and `verify_api_key` (existing), `CHANNELS` with `mcp` (Task 1).
- Produces:
  - `mount_mcp(app: FastAPI) -> None`. It is called in `create_app` before `mount_web`, so the web catch-all never shadows `/mcp`.
  - `create_app` gets a FastAPI `lifespan` that runs the MCP session manager (`mcp.session_manager.run()`).
  - `jerome.mcp.context.call(fn: Callable[[ToolContext], T]) -> T` runs one tool body. It:
    - opens the session, resolving `get_session` through `app.dependency_overrides` when set, as REST does in tests;
    - runs `fn` inside `session.begin_nested()`;
    - maps exceptions with `to_tool_error`.
  - `ToolContext` is a dataclass with these fields: `app: FastAPI`, `session: Session`, `principal: Principal`, `settings: Settings`, `storage: Storage` (it resolves `get_storage` through `app.dependency_overrides` too), `registry: Registry` and `worker_deps: WorkerDeps`.
  - `jerome.mcp.errors.to_tool_error(exc: Exception) -> ToolError` (the SDK's tool error type) implements the code table from Global Constraints. `HTTPException(429)` becomes `rate_limited`. Other `HTTPException`s become `invalid` with their detail, and `RequestValidationError` and pydantic `ValidationError` become `invalid` too.
  - `jerome.mcp.server.mcp: FastMCP` is the instance that tool modules register on, with `@mcp.tool(...)`.
  - The principal reaches the tool as follows. The auth middleware stores it in `scope["state"]["jerome_principal"]`. `context.call` reads it from the SDK's request context, `ctx.request_context.request`.
  - `list_books(ctx) -> list[dict]` returns the REST `GET /v1/books` JSON. It must have `readOnlyHint` set.
- Test helper in `tests/mcp/conftest.py`:
  - `mcp_call(client, headers, tool: str, arguments: dict) -> dict` POSTs a JSON-RPC `tools/call` to `/mcp` with `Accept: application/json, text/event-stream` and returns the JSON-RPC `result`;
  - `mcp_list_tools(client, headers) -> list[str]`;
  - a fixture `mcp_auth(session)`, which is `{"Authorization": "Bearer <key named 'janus-test'>"}`.

- [ ] **Step 1: Write the failing tests** in `tests/mcp/test_endpoint.py`, using the existing `client` fixture:
  - `test_no_key_is_401`: POST to `/mcp` without `Authorization` returns 401.
  - `test_bad_key_is_401`: a garbage Bearer returns 401.
  - `test_cookie_only_is_401`: sign in through `/v1/auth/login`, so the cookie is set, then POST to `/mcp` with no Bearer. Expect 401.
  - `test_revoked_key_is_refused_on_next_call`: one successful `list_books` call, then revoke that key (set `revoked_at`), then the next call returns 401.
  - `test_list_books_matches_rest`: with `imported_docx_book`, `mcp_call(..., "list_books", {})` returns `structuredContent` (or the JSON text content) equal to `client.get("/v1/books", headers=auth).json()`.
  - `test_not_found_maps_to_tool_error`: register a throwaway test-only tool on `mcp` that raises `NotFound("book 999 not found")`. The result has `isError` true, and its text is `not_found: book 999 not found`.
  - `test_unexpected_error_is_generic`: a test tool raising `RuntimeError("secret")` gives the text `error: internal error`, and `secret` doesn't appear in the response.
  - `test_sdk_client_can_initialize_and_list` (`@pytest.mark.anyio`): the real SDK client, the `streamablehttp_client` with an httpx `ASGITransport` and the app's lifespan running, initializes and lists tools, and `list_books` is among them.
- [ ] **Step 2: Run** `uv run pytest -q tests/mcp` with the test DB env. Expected: FAIL (`/mcp` 404 or import error).
- [ ] **Step 3: Implement** the module files above, then `uv add mcp`, and pin the exact version.
  - Build `FastMCP` with `stateless_http=True` and `json_response=True`, and mount its streamable HTTP app at `/mcp`.
  - The auth middleware wraps only the MCP app and lets `/mcp/files/` paths through untouched, for Task 6.
- [ ] **Step 4: Run** `uv run pytest -q tests/mcp tests/api`. Expected: PASS, and existing API tests are still green.
- [ ] **Step 5: Commit** `feat: /mcp endpoint with API-key auth and tool error mapping`.

### Task 3: Activity log core and `GET /v1/agent-actions`

**Files:**
- Create: `jerome/core/agent_actions.py`, `jerome/api/routes_agent_actions.py`
- Modify: `jerome/api/app.py` (include the router under `/v1` with `auth`), `jerome/api/schemas.py` (`AgentActionOut`)
- Test: `tests/core/test_agent_actions.py`, `tests/api/test_agent_actions_api.py`

**Interfaces:**
- Consumes: `AgentAction` (Task 1).
- Produces:
  - `record_action(session, *, actor: str, tool: str, summary: str, book_id: int | None = None, chapter_id: int | None = None, job_id: int | None = None, before: Any = None, after: Any = None) -> AgentAction` flushes, with `channel="mcp"`.
  - `list_actions(session, *, book_id: int | None, limit: int, offset: int) -> tuple[list[AgentAction], int]` returns rows newest first (`at desc, id desc`) and the total.
  - `AgentActionOut` has `id, at, actor, channel, tool, book_id, chapter_id, job_id, summary, before, after`.
  - `GET /v1/agent-actions?book_id=&limit=&offset=` takes `limit` between 1 and 200, default 50, and `offset` of at least 0. It returns `list[AgentActionOut]` with an `X-Total-Count` header, and works for a web session or an API key.

- [ ] **Step 1: Write the failing tests:**
  - `test_record_and_list_newest_first`;
  - `test_list_filters_by_book`;
  - `test_deleting_book_keeps_action_with_null_book`;
  - API: `test_agent_actions_requires_auth` (401 without auth), `test_agent_actions_lists_with_total_header`, `test_agent_actions_limit_bounds` (`limit=0` and `limit=201` give 422).
- [ ] **Step 2: Run** them. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both modules and the schema.
- [ ] **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Janus activity log and its API`.

### Task 4: Read, settings, job and text tools

**Files:**
- Modify:
  - `jerome/mcp/tools_books.py`;
  - `jerome/api/routes_text.py`: take `TextLimits` through a new dependency `get_text_limits(request) -> TextLimits`, so the tool can pass `app.state.text_limits`. REST behaviour is unchanged.
- Create: `jerome/mcp/tools_jobs.py`, `jerome/mcp/tools_review.py` (read tools only here), `jerome/mcp/tools_text.py`
- Test: `tests/mcp/test_tools_books.py`, `tests/mcp/test_tools_jobs.py`, `tests/mcp/test_tools_review_read.py`, `tests/mcp/test_tools_text.py`

**Interfaces:**
- Consumes: `context.call`, `ToolContext` (Task 2), `record_action` (Task 3).
- Produces these tools. Each calls the REST handler function from `jerome/api/routes_*.py`, or the same core function with the same arguments, and returns the REST JSON (`fastapi.encoders.jsonable_encoder`, validated through the route's `response_model` where it has one).
  - **Read-only tools** (`readOnlyHint`):
    - `list_projects()`, `get_book(book_id)`, `list_chapters(book_id)`;
    - `get_style_sheet(book_id)`, `get_auto_apply(book_id)`;
    - `estimate_job(book_id, passes=None, chapters=None)`, `list_jobs(book_id)`, `get_job(job_id)`;
    - `get_chapter_summary(book_id)`, `get_chapter_review(chapter_id, offset=0, limit=50)`;
    - `list_suggestions(book_id, chapter_id=None, category=None, severity=None, min_severity=None, source=None, status=None, limit=100, offset=0)`, which returns `{"suggestions": [...], "total": <X-Total-Count>}`;
    - `get_block(book_id, block_id)`.
  - **Writing tools.** Each calls `record_action` with the Global Constraints summary.
    - `set_chapter_proofread(chapter_id, proofread: bool)`, with `before`/`after` `{"proofread": bool}`, and `book_id` and `chapter_id` set;
    - `set_style_sheet(book_id, <StyleSheetUpdate fields>)`, with `before`/`after` the full style sheet as REST returns it;
    - `set_auto_apply(book_id, enabled, categories, min_severity, house_rules)`, with `before`/`after` the `AutoApplyBody` JSON;
    - `start_job(book_id, max_cost, passes=None, chapters=None, callback_url=None)`. It builds `JobIn(type="proofread", ...)`. Its description says it runs paid model work and to call `estimate_job` first. The log row gets `job_id`.
    - `cancel_job(job_id)` and `resume_job(job_id, max_cost=None)`, with `job_id` and `book_id` set.
  - `proofread_text(text, variant, passes=None, quote_style=None, ignore_words=None, local_only=False)` is not logged, because it changes nothing.

- [ ] **Step 1: Write the failing tests:**
  - **Parity, one per read tool:** the tool result equals the REST JSON for the same object. Use `imported_docx_book`, and a job created through `/v1/books/{id}/jobs` with the existing model test doubles.
  - `test_set_style_sheet_logs_before_and_after`: the `agent_actions` row has `tool="set_style_sheet"`, `actor="janus-test"`, `summary="Changed the style sheet"`, and the old and new values.
  - `test_set_auto_apply_summary_on_off_and_change`: three calls give `Turned auto-apply off`, `Turned auto-apply on`, and `Changed auto-apply settings`.
  - `test_set_chapter_proofread_logs`.
  - `test_start_cancel_resume_job_log_job_id`.
  - `test_start_job_rejects_what_rest_rejects`:
    - an unknown pass gives `invalid:`;
    - a chapter id from another book gives the same code as REST;
    - `max_cost=-1` gives `invalid:`;
    - no `agent_actions` row is written in any of these cases.
  - `test_read_tools_write_no_activity`: every read tool call leaves `agent_actions` empty.
  - `test_proofread_text_shares_rest_rate_limit`: with `text_rpm=1` in `test_settings`, one REST call with key X, then the `proofread_text` tool with the same key X, gives `rate_limited:`.
  - `test_delete_book_is_not_a_tool`: `delete_book` and anything with `user` in its name are absent from `mcp_list_tools`.
- [ ] **Step 2: Run** `uv run pytest -q tests/mcp`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the tools. Write every description for an agent: what the tool does, where each ID comes from (for example "`chapter_id` from `list_chapters`"), and what it returns.
- [ ] **Step 4: Run** `uv run pytest -q tests/mcp tests/api`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: MCP tools for books, settings, jobs, review reads and quick text`.

### Task 5: Decision tools and the `decided_channel` field

**Files:**
- Modify:
  - `jerome/mcp/tools_review.py`;
  - `jerome/api/routes_suggestions.py`: the `suggestion_out` decider query also selects `Decision.channel`;
  - `jerome/api/schemas.py`: `SuggestionOut.decided_channel: str | None`.
- Test: `tests/mcp/test_tools_decisions.py`, `tests/api/test_suggestions_api.py` (one assertion added)

**Interfaces:**
- Consumes: `context.call` (Task 2), `record_action` (Task 3).
- Produces:
  - `decide_suggestion(suggestion_id, action: Literal["accept","reject","undo"])`;
  - `edit_suggestion(suggestion_id, replacement: str)`;
  - `bulk_decide(book_id, action: Literal["accept","reject"], ids=None, filter=None)`, where `filter` has the `SuggestionFilterIn` fields.

  All three call the REST handlers with a `Principal` on channel `mcp`, so the decisions are recorded as `actor=<key name>`, `channel="mcp"`. Each logs one row, with `book_id` set, and `after` is `{"suggestion_ids": [...]}`. For a bulk decision, `after` is `{"decided": n}` instead.
  - `SuggestionOut.decided_channel` is the latest non-system decision's channel, or null.

- [ ] **Step 1: Write the failing tests:**
  - `test_accept_records_mcp_channel_and_actor`: the latest `Decision` row has `channel == "mcp"` and `actor == "janus-test"`. A REST `GET /v1/books/{id}/suggestions` then shows `decided_by == "janus-test"` and `decided_channel == "mcp"`.
  - `test_mcp_decision_can_be_undone_over_rest`: accept through MCP, then `POST /v1/suggestions/{id}/undo` through REST. The suggestion is open again.
  - `test_bulk_decide_logs_count`: the summary is `Accepted <n> suggestions` with the real n.
  - `test_edit_suggestion_logs`.
  - `test_decide_conflict_maps_to_conflict`: deciding a suggestion that REST would refuse with 409 gives `conflict:` and no log row. Use the existing conflict case from the API tests, for example accepting an overlapping sibling.
  - The existing API test `decided_channel` is `"web"` for a web-session decision and `"api"` for a key decision.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q tests/mcp tests/api`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: MCP decision tools recorded as Janus`.

### Task 6: File links, file tools and README

**Files:**
- Create:
  - `jerome/core/transfer_links.py`;
  - `jerome/mcp/files.py`: a small Starlette or FastAPI router for `PUT /mcp/files/{token}` and `GET /mcp/files/{token}`;
  - `jerome/mcp/tools_files.py`.
- Modify: `jerome/storage.py` (`transfers` directory helpers), `jerome/mcp/server.py` (mount files routes ahead of the MCP app), `README.md`
- Test: `tests/core/test_transfer_links.py`, `tests/mcp/test_files.py`

**Interfaces:**
- Consumes: `TransferLink` (Task 1), `context.call` (Task 2), `record_action` (Task 3).
- Produces:
  - `LINK_TTL = timedelta(minutes=15)`.
  - `create_upload(session, actor: str, now: datetime) -> tuple[TransferLink, str]` returns the row and the raw token, `secrets.token_urlsafe(32)`.
  - `receive_upload(session, storage, token: str, data: bytes, limit: int, now) -> None`. It raises `NotFound` when the token is unknown, expired or already received, and an `InvalidChangeError("file too large")` when over the limit. It writes `transfers/<id>` atomically and sets `size` and `received_at`.
  - `consume_upload(session, storage, upload_id: int, actor: str, now) -> bytes`. It raises `NotFound` when the upload is unknown, belongs to another actor, is expired, was never received or is already used. It marks `used_at`, deletes the file and returns the bytes.
  - `create_download(session, storage, actor, filename, media_type, data, now) -> tuple[TransferLink, str]`.
  - `serve_download(session, storage, token, now) -> tuple[TransferLink, bytes]` is single use: it marks `used_at` and deletes the file.
  - `purge_expired(session, storage, now) -> int` deletes expired rows and their files. It runs at the start of `create_upload` and `create_download`.
  - `PUT /mcp/files/{token}` answers 204. It gives 404 for a bad token and 413 when the body is over the limit.
  - `GET /mcp/files/{token}` answers 200 with the bytes, the `media_type`, and the `Content-Disposition` from `routes_exports.content_disposition`. It gives 404 for a bad token.
  - Tools:
    - `request_upload()` returns `{"upload_id", "upload_url", "expires_at", "max_bytes"}`. `upload_url` is absolute, built from the MCP request's base URL plus `/mcp/files/<token>`.
    - `import_book(upload_id, filename, variant, title=None, local_only=False, auto_apply=True)` calls `books.import_book` with the consumed bytes, as `upload_book` does, and logs `Imported "<title>"`.
    - `import_review_copy(book_id, upload_id)` calls `import_review(session, book_id, data, actor)`, as REST does, and logs `Imported a review copy`.
    - `get_review_import(book_id, import_id)` is read-only and matches REST.
    - `export_book(book_id, format, min_severity=None, categories=None, chapters=None)` calls `export_book` and returns `{"download_url", "filename", "media_type", "size", "expires_at"}`. It is not logged, because it changes nothing in the book.
  - README: a "Connecting Janus (MCP)" section, from the spec's Docs paragraph. Use a placeholder domain only.

- [ ] **Step 1: Write the failing tests:**
  - **Core:** expiry at 15 minutes, using an injected `now`; single use for both kinds; another actor can't consume; `purge_expired` removes files; only the hash is stored (the raw token is not in the row).
  - `test_upload_then_import_book` (MCP): `request_upload`, a `PUT` of the `make_docx(...)` bytes to the returned URL path, then `import_book`. The book appears in `list_books`, and one log row reads `Imported "<title>"`.
  - `test_upload_too_large_leaves_nothing`: `max_upload_mb=1` in `test_settings` and a 1 MiB + 1 byte PUT give 413 and no file under `data_dir/transfers`.
  - `test_upload_link_single_use_and_404`: a second PUT gives 404, and an unknown token gives 404.
  - `test_export_book_download_single_use`: the GET returns the same bytes as REST `GET /v1/books/{id}/export?format=docx` for the same filters, with the same `Content-Disposition`. A second GET gives 404.
  - `test_files_routes_need_no_bearer_and_ignore_cookie`: the file links work without `Authorization`, and a request to `/mcp` itself (not `/mcp/files/`) without a key is still 401.
  - `test_import_review_copy_round_trip`: export a review copy, then upload it as is and call `import_review_copy`. The result equals the REST review-import response shape. Any decisions the import records are on channel `word` with `actor == "janus-test"`, not `mcp`. One log row is written.
- [ ] **Step 2: Run** `uv run pytest -q tests/core/test_transfer_links.py tests/mcp/test_files.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the core module, the routes, the tools and the README section.
- [ ] **Step 4: Run** the full backend suite: `uv run pytest -q`, then `uv run ruff check . && uv run ruff format --check .`. Expected: all pass, apart from the known timing flake `tests/core/test_review_pipeline.py::test_large_paragraph_with_eight_suggestions_classifies_quickly`.
- [ ] **Step 5: Commit** `feat: MCP file links for import, review copies and export; README`.

### Task 7: Web — "Janus (<key>)" label and the Janus activity page

**Files:**
- Modify:
  - `web/src/lib/review.ts`: add `decided_channel` to the type, and change the label at line ~163;
  - `web/src/components/review/fixtures.tsx`;
  - `web/src/App.tsx`: route `/activity`;
  - `web/src/components/AppShell.tsx`: a nav link `Janus activity` for every member;
  - `web/src/pages/ProjectPage.tsx`: a link "Janus activity for this book" to `/activity?book=<id>`.
- Create: `web/src/pages/ActivityPage.tsx`, `web/src/lib/activity.ts` (type and a `useAgentActions(bookId?, page)` react-query hook on `/v1/agent-actions`)
- Test: `web/src/lib/review.test.ts` (or the existing review label test file), `web/src/pages/ActivityPage.test.tsx`, `web/src/components/AppShell.test.tsx`

**Interfaces:**
- Consumes:
  - `SuggestionOut.decided_channel` (Task 5);
  - `GET /v1/agent-actions` (Task 3), which returns `AgentActionOut` with the fields `id, at, actor, channel, tool, book_id, chapter_id, job_id, summary, before, after` and an `X-Total-Count` header.
- Produces: the decision label `"<Verb> by Janus (<decided_by>)"` when `decided_channel === "mcp"`. It is unchanged otherwise.

- [ ] **Step 1: Write the failing tests:**
  - **Label:** a decided suggestion with `decided_channel: "mcp", decided_by: "janus-desktop"` renders `Accepted by Janus (janus-desktop)`. The existing labels for `web`, `api` and `auto` are unchanged.
  - **ActivityPage:**
    - it shows the heading `Janus activity`;
    - rows show the time, `Janus (<actor>)` and the summary;
    - for a row with `before`/`after`, it shows both values;
    - `?book=5` requests `/v1/agent-actions?book_id=5`, and the page shows a link back to that book;
    - with no rows, it shows the text `Janus hasn't changed anything yet.`;
    - paging uses `X-Total-Count`.
  - **AppShell:** the `Janus activity` link is visible for both `ME` (super admin) and `MEMBER`.
- [ ] **Step 2: Run** `cd web && npm test`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** with the existing shadcn components in `web/src/components/ui`. Lay out the page like the Team page: a table with Time, Who, What and Changes columns. In Changes, show `before → after` as compact JSON.
- [ ] **Step 4: Run** `cd web && npm test && npm run build`. Expected: PASS, apart from the known load flake in `ProjectPage.test.tsx` ("set_up shows the start panel…"), which passes alone.
- [ ] **Step 5: Commit** `feat(web): Janus label on decisions and the Janus activity page`.
