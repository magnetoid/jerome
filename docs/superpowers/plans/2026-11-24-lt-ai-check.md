# AI check of LanguageTool marks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In review, ask the AI whether a LanguageTool-only mark is right, save its verdict on
the suggestion, and act on it in one click; also for a chapter's marks in one go.

**Architecture:** A pure module `jerome/proofread/ai_check.py` builds the request for one
paragraph's marks and parses the reply. A service `jerome/core/ai_check.py` picks the marks,
calls the `ai` pass model, bills the call apart (as `retranslate_block` does) and saves the
verdicts. Two REST routes and one MCP tool call the service; the web review card shows the
verdict.

**Tech Stack:** Python 3.12, SQLAlchemy, Alembic, FastAPI, the MCP SDK; React, react-query,
vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-lt-ai-check-design.md`

## Global Constraints

- Eligible mark: `status == "open"`, `source == "languagetool"`, `agreed` false, `ai_verdict` null.
- Verdicts: `correct`, `not_an_error`, `better`. `ai_replacement` only for `better`.
- Model: `registry.for_pass("ai", local_only=book.local_only)`; none → `Conflict` (409).
- Billing: to the mark's job, else the book's latest `proofread` job, in its own transaction,
  `pass_name="ai"`. No job → `Conflict`.
- Rate limit: `TextLimits.take(actor)` once per request (single or chapter). No daily cap.
- Chapter batch: at most `CHAPTER_LIMIT = 40` marks per request, in document order, one model
  call per paragraph.
- An unusable reply saves nothing; the single route answers 502; the spend stays billed.
- Nothing is accepted or rejected by the check itself.
- Public repo: tests use built fixtures only (`make_docx`), no book files.

## Review Focus

1. A mark decided (accepted by a teammate) while the model call runs: its verdict is still
   saved, its status is not touched, and no error is raised. (Task 3 test
   `test_mark_decided_during_the_call_keeps_its_status`.)
2. A `better` reply that equals LanguageTool's own fix is a `correct` verdict, not a second
   identical fix. (Task 2 test `test_better_equal_to_lt_fix_is_correct`.)
3. A `better` reply with the object placeholder U+FFFC or a control character is unusable.
   (Task 2 test `test_unusable_items`.)
4. A local-only book whose `ai` model is hosted gets 409 and no call. (Task 3 test
   `test_local_only_book_with_hosted_model_conflicts`.)
5. A chapter with more than 40 eligible marks reports how many remain, and a second request
   checks the rest. (Task 3 test `test_chapter_checks_40_then_the_rest`.)

---

### Task 1: Columns for the verdict

**Files:**
- Create: `migrations/versions/0010_ai_check.py`
- Modify: `jerome/orm.py` (class `Suggestion`), `jerome/api/schemas.py` (`SuggestionOut`),
  `jerome/api/routes_suggestions.py` (`_PLAIN_FIELDS`), `web/src/lib/review.ts` (`Suggestion`),
  `web/src/components/review/fixtures.tsx`
- Test: `tests/test_schema.py`, `tests/api/test_suggestions_api.py`

**Interfaces:**
- Produces: `Suggestion.ai_verdict: str | None` (check constraint
  `ck_suggestions_ai_verdict`: null or one of the three), `ai_replacement: str | None`,
  `ai_reason: str | None`, `ai_model: str | None`; the same four keys in every suggestion's
  JSON; the TS fields `ai_verdict: "correct" | "not_an_error" | "better" | null`,
  `ai_replacement`, `ai_reason`, `ai_model: string | null`.

- [ ] **Step 1: Failing tests.** In `test_schema.py` add to `test_check_constraints_reject_bad_vocabulary`
  (or a new test `test_ai_verdict_vocabulary`) that `ai_verdict="maybe"` raises
  IntegrityError and `"better"` is stored. In `test_suggestions_api.py` add
  `test_suggestion_json_has_ai_fields`: a listed suggestion has `ai_verdict is None`,
  `ai_replacement is None`, `ai_reason is None`, `ai_model is None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_schema.py tests/api/test_suggestions_api.py -q` → FAIL.
- [ ] **Step 3: Implement.** Migration `revision="0010"`, `down_revision="0009"`, four
  nullable columns (`ai_verdict`, `ai_model` String; `ai_replacement`, `ai_reason` Text) and the
  check constraint; downgrade drops them. ORM columns and `CheckConstraint` beside the others.
  Add the four to `_PLAIN_FIELDS` and `SuggestionOut`. TS type and fixture defaults `null`.
- [ ] **Step 4: Run** the same tests plus `test_migration_matches_models` → PASS; `cd web && npx tsc --noEmit -p .` clean.
- [ ] **Step 5: Commit** `feat: suggestion columns for the AI's verdict`.

### Task 2: Request and reply for one paragraph's marks

**Files:**
- Create: `jerome/proofread/ai_check.py`, `jerome/proofread/prompts/ai_check_en.md`,
  `jerome/proofread/prompts/ai_check_de.md`
- Test: `tests/proofread/test_ai_check.py`

**Interfaces:**
- Consumes: `StyleSheet`, `SystemPart`, `_style_text` from `jerome.proofread.ai_pass`,
  `PLACEHOLDER`, `check_replacement_text` from `jerome.documents.changes` (raises
  `InvalidChangeError` on text a document cannot carry).
- Produces:
  - `@dataclass(frozen=True) class Mark: id: int; start: int; end: int; original: str; replacement: str; message: str`
  - `@dataclass(frozen=True) class Verdict: id: int; verdict: str; replacement: str | None; reason: str`
  - `VERDICT_SCHEMA: dict` — `{"verdicts": [{"id": int, "verdict": enum, "replacement": str, "reason": str}]}`, all required, no extra keys.
  - `build_request(style, text: str, marks: list[Mark], *, previous: str | None, source: str | None) -> tuple[list[SystemPart], str]`
  - `parse_verdicts(data: dict, marks: list[Mark]) -> list[Verdict]` — usable items only;
    raises `ModelOutputError` when `data` has no `verdicts` list.

- [ ] **Step 1: Failing tests** in `tests/proofread/test_ai_check.py`:
  - `test_request_shows_each_mark_in_its_context`: the user text holds the paragraph, each
    mark's id, original, LanguageTool's fix and message, and the span in context as
    `…before⟦original⟧after…` (up to 40 characters each side); `previous` appears in a
    read-only `<previous_paragraph>` element and `source` in `<source_paragraph>` only when given;
    the system parts are the `ai_check_<language>.md` prompt and the style sheet.
  - `test_parses_the_three_verdicts`: `correct` → `replacement None`; `not_an_error` →
    `replacement None`; `better` with `"teh cat"`→ `Verdict(..., "better", "the cat", reason)`.
  - `test_better_equal_to_lt_fix_is_correct`: `better` whose replacement equals the mark's
    `replacement` → `Verdict(verdict="correct", replacement=None)`.
  - `test_unusable_items` (parametrized): unknown id, unknown verdict, empty reason, `better`
    with empty replacement, `better` equal to `original`, `better` containing U+FFFC, `better`
    containing `"\x07"`, a non-dict item, a second verdict for the same id → that item
    dropped (the first of a repeated id stays).
  - `test_reply_without_verdicts_list_is_model_output_error`.
- [ ] **Step 2: Run** `uv run pytest tests/proofread/test_ai_check.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the module and both prompts. The prompt says: you are checking
  marks a rule-based checker (LanguageTool) made in a book; judge each against the book's
  language variant and style sheet; names, deliberate dialect and author style are
  `not_an_error`; `better` only for the same span, never wider; reason is one short sentence
  in English; reply with JSON matching the schema. German prompt says the same in German
  (reason still in English, since the reviewer's UI is English). Load with
  `importlib.resources` like `ai_pass._instructions`.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat: ask the AI about LanguageTool marks: request and reply`.

### Task 3: The service: pick, call, bill, save

**Files:**
- Create: `jerome/core/ai_check.py`
- Modify: `jerome/core/translation.py` (`_bill_apart` gets `pass_name: str = "translate"`),
  `jerome/errors.py` (new `AiCheckError(JeromeError)`)
- Test: `tests/core/test_ai_check.py`

**Interfaces:**
- Consumes: Task 2's `Mark`, `build_request`, `parse_verdicts`, `VERDICT_SCHEMA`;
  `_bill_apart(session, registry, job_id, billed, configured, pass_name=...)`;
  `Billed`, `billed_from` (late import from `jerome.jobs.worker`, as `retranslate_block` does);
  `get_style_sheet`, `source_texts`, `lock_blocks`.
- Produces:
  - `CHAPTER_LIMIT = 40`
  - `NO_AI_MODEL = "No AI model is set for proofreading (models.ai); set one under Settings"`
  - `check_suggestion(session, registry, suggestion_id: int, *, actor: str) -> Suggestion`
    — 404 for an unknown id; `Conflict` when not eligible (message names why: not open /
    not from LanguageTool alone / already checked); `AiCheckError` (→ 502) when the reply
    gives no usable verdict for it, or the model refused / sent unusable output.
  - `@dataclass class ChapterCheck: checked: list[Suggestion]; remaining: int; failed: int`
  - `check_chapter(session, registry, book_id: int, chapter_id: int, *, actor: str) -> ChapterCheck`
    — 404 when the chapter is not the book's; the first `CHAPTER_LIMIT` eligible marks in
    (block ordinal, start, id) order; `remaining` = eligible marks left after this request;
    `failed` = marks asked about that got no usable verdict. No eligible marks → empty result,
    no call.
  - `AiCheckError` in `jerome/errors.py`.

Flow for one paragraph (shared by both): build the request from `block.text`, the previous
block's text in the chapter (by ordinal) and, on a translated book, `source_texts`; call
`provider.complete(model=cfg.model, system=..., user=..., schema=VERDICT_SCHEMA, effort=cfg.effort, max_tokens=4000)`;
bill (`pass_name="ai"`) whatever was billed, also on `ModelRefusal` / `ModelOutputError`;
then `lock_blocks` the block, re-read its marks with `populate_existing`, and for each usable
verdict whose mark still has `ai_verdict is None` set the four columns (`ai_model` = the
reply's model). Status is never changed. The job billed is the first mark's `job_id`, else
the book's latest `proofread` job.

- [ ] **Step 1: Failing tests** in `tests/core/test_ai_check.py` (built with `make_docx`, a
  `Job(type="proofread")` row, `insert_raw_open`, and `Registry` on `FakeProvider` with
  `models["ai"]`):
  - `test_single_check_saves_the_verdict_and_bills_the_job`: reply `better` → row has
    `ai_verdict="better"`, `ai_replacement`, `ai_reason`, `ai_model="claude-opus-5-5"`;
    status still `open`; one `ModelUsage` row with `pass_name="ai"` and that `job_id`; the
    job's `cost_actual` went up.
  - `test_not_eligible_conflicts` (parametrized): accepted mark; `source="model"`; `agreed=True`;
    already has a verdict → `Conflict`, no call.
  - `test_unusable_reply_saves_nothing_but_is_billed`: reply `{"verdicts": []}` →
    `AiCheckError`; no verdict saved; one usage row.
  - `test_refusal_is_ai_check_error` : `ModelRefusal(usage=Usage(10, 0), model="m", provider="fake")` → `AiCheckError`, billed.
  - `test_no_ai_model_conflicts` and `test_local_only_book_with_hosted_model_conflicts`.
  - `test_mark_decided_during_the_call_keeps_its_status`: the fake reply is a callable that
    accepts the mark in another session first; afterwards the mark is `accepted` and has the verdict.
  - `test_chapter_groups_by_paragraph`: two paragraphs, three marks → two calls, three verdicts,
    `remaining == 0`, `failed == 0`; a `model` mark in the chapter is left alone.
  - `test_chapter_checks_40_then_the_rest`: 45 marks in one paragraph each 1 char apart (use
    `insert_raw_open` on a long built paragraph) → first request `len(checked) == 40`,
    `remaining == 5`; second → 5 and 0.
  - `test_chapter_counts_failed_marks`: one paragraph's reply unusable → its marks in
    `failed`, the other paragraph's saved.
  - `test_chapter_of_another_book_is_not_found`.
- [ ] **Step 2: Run** `uv run pytest tests/core/test_ai_check.py -q` → FAIL.
- [ ] **Step 3: Implement** `jerome/core/ai_check.py` and the two small changes.
- [ ] **Step 4: Run** the same plus `tests/jobs/test_translate_job.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: check LanguageTool marks with the AI and keep the verdict`.

### Task 4: REST routes and the MCP tool

**Files:**
- Modify: `jerome/api/routes_suggestions.py`, `jerome/api/schemas.py`, `jerome/api/app.py`
  (`_ERROR_STATUS[AiCheckError] = 502`), `jerome/mcp/errors.py`
  (`AiCheckError: "unavailable"`), `jerome/mcp/tools_review.py`
- Test: `tests/api/test_suggestions_api.py`, `tests/mcp/test_tools_decisions.py`

**Interfaces:**
- Consumes: Task 3's `check_suggestion`, `check_chapter`, `ChapterCheck`.
- Produces:
  - `POST /v1/suggestions/{id}/ai-check` → `SuggestionOut`. Declared **before** the
    `/suggestions/{suggestion_id}/{action}` route so `ai-check` is not read as an action.
  - `POST /v1/books/{book_id}/chapters/{chapter_id}/ai-check` → `ChapterAiCheckOut{checked: int, remaining: int, failed: int, suggestions: list[SuggestionOut]}`.
  - Both take `limits.take(actor)` first; a wait → `too_many("Rate limit exceeded", wait)`.
  - MCP `ai_check_suggestion(suggestion_id: int) -> dict` with the `CALLS_MODEL`
    annotations, calling the route function with `c.app.state.text_limits`, logged with
    `record_action(tool="ai_check_suggestion", summary="Asked the AI about a LanguageTool mark", ...)`.

- [ ] **Step 1: Failing tests:**
  - `test_ai_check_route_returns_the_suggestion` (200, `ai_verdict` set),
    `test_ai_check_route_409_when_not_eligible`, `test_ai_check_route_502_on_unusable_reply`,
    `test_ai_check_route_rate_limited` (429 with `Retry-After`, using an app whose
    `text_limits` has `rpm=0` or a spent bucket, as `test_translation_api` does),
    `test_chapter_ai_check_route` (counts and suggestions).
  - MCP: `test_ai_check_suggestion_tool` returns the suggestion JSON and writes an agent action;
    `test_errors.py`'s table test keeps passing with the new class.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_suggestions_api.py tests/mcp -q` → FAIL.
- [ ] **Step 3: Implement** routes, schema, error mapping and the tool.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat: AI check routes and MCP tool`.

### Task 5: The review card and the chapter button

**Files:**
- Create: `web/src/hooks/useAiCheck.ts`, `web/src/components/review/CheckLtMarks.tsx`
- Modify: `web/src/lib/review.ts` (`canAskAi`, `AI_VERDICT_LABELS`),
  `web/src/components/review/FocusCard.tsx`, `web/src/pages/ReviewPage.tsx`,
  `web/src/hooks/useDecide.ts` (export the cache patch)
- Test: `web/src/components/review/FocusCard.test.tsx`, `web/src/pages/ReviewPage.test.tsx`

**Interfaces:**
- Consumes: Task 4's routes and JSON.
- Produces:
  - `canAskAi(s): boolean` — open, `source === "languagetool"`, not agreed, no verdict.
  - `AI_VERDICT_LABELS = { correct: "AI: LanguageTool is right", not_an_error: "AI: not an error", better: "AI suggests a better fix" }`.
  - `useAiCheck(bookId)` — mutation POSTing `/v1/suggestions/{id}/ai-check`; on success the
    row is patched into the review cache; on error a toast with the API's message.
  - FocusCard props `onAskAi: () => void`, `asking: boolean`, `onUseAiFix: () => void`.
  - `CheckLtMarks({ bookId, chapterId })` — button "Check LT marks in this chapter"; on
    success a toast: `Checked ${checked} of ${checked + remaining}; click again for the rest`
    when `remaining > 0`, else `Checked ${n} LanguageTool mark(s)`, or
    `No LanguageTool marks left to check` when 0; `failed > 0` adds
    `; ${failed} could not be checked`. Then refresh the review.

- [ ] **Step 1: Failing tests** (FocusCard):
  - `shows Ask AI only for an unchecked LanguageTool mark` (shown for `source: "languagetool"`,
    hidden for `model`, for `agreed`, for a decided mark, and once `ai_verdict` is set).
  - `shows the verdict and reason`: `better` shows the label, `“the cat”` and the reason, and
    a **Use AI fix** button calling `onUseAiFix`.
  - `not an error makes Reject the main button`: Reject without the outline variant class,
    Accept with it.
  - ReviewPage: `Ask AI posts and shows the verdict` (mock POST returns the row with a verdict;
    the card shows the label) and `Use AI fix edits with the AI's text` (POST
    `/v1/suggestions/{id}/edit` with `{ replacement: ai_replacement }`), and
    `Check LT marks reports what is left` (mock returns `checked: 40, remaining: 23` → toast
    text "Checked 40 of 63; click again for the rest").
- [ ] **Step 2: Run** `cd web && npx vitest run src/components/review/FocusCard.test.tsx src/pages/ReviewPage.test.tsx` → FAIL.
- [ ] **Step 3: Implement.** In ReviewPage: `onUseAiFix={() => current.ai_replacement !== null && run(current.id, "edit", current.ai_replacement)}`;
  the chapter button goes in the toolbar's `bulk` slot next to BulkAccept (wrap both in a fragment).
- [ ] **Step 4: Run** the same → PASS; `npm run build` OK.
- [ ] **Step 5: Commit** `feat(web): ask the AI about a LanguageTool mark in review`.

### Task 6: README

**Files:** Modify `README.md` (review section): one paragraph on **Ask AI** and
**Check LT marks in this chapter**: what it does, that each check is a paid call on the `ai`
model billed to the proofreading job, and the 40-per-click limit.

- [ ] **Step 1:** Write it. **Step 2:** `uv run ruff check . && uv run ruff format --check .`
  and the full backend suite → green. **Step 3: Commit** `docs: AI check of LanguageTool marks`.
