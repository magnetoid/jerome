# Translation (EN↔DE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project can be created as "Translate + proofread". Jerome translates the book EN↔DE into a new, linked book, which then goes through today's proofreading, review and export with the original's formatting.

**Architecture:**
- Import creates two books: a hidden, read-only **source** book, and a **translated** book whose blocks are empty and point at their source blocks.
- A `translate` job fills the translated blocks chapter by chapter, using the existing worker, cost cap and resume. A pure tagging module carries marks and placeholders through the model.
- Export first rebuilds a **translated base file** from the source original and the translated blocks. Then it runs the existing writers on that base. Review copies, change records, clean export and the Word round trip therefore work unchanged on the translated book.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, lxml, pytest, ruff; React 19, Vite, Tailwind 4, shadcn/ui, react-query 5, vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-translation-design.md`. Read it before any task.

## Global Constraints

- The repo is public. No book text, owner hosts, IPs, domains or deployment IDs in code, docs or tests.
- Tests use only the public fixtures (`tests/fixtures/public/pg932.epub`, English; `tests/fixtures/public/pg22367.epub`, German) and the test builders (`tests/fixtures/docx_builders.py`, `tests/fixtures/epub_builders.py`). Never read or use any other book file on this machine.
- Model calls in tests use `jerome.models.FakeProvider`. No live model calls in CI.
- The product default model stays `claude-opus-5-5`, for both `ai` and the new `translate` pass.
- The supported variants are `en-US`, `en-GB`, `de-DE`, `de-AT`, `de-CH`. The source and target must differ in language (`StyleSheet.language`), not only in variant.
- "Proofread only" projects behave exactly as today. Every existing test keeps passing unchanged, except where a task says it changes a test.
- The source book is never modified.
- A translation never loses text. Every source block gets a translated block or a visible flag.
- Jerome never trusts model-written XML. The model returns tagged plain text, and Jerome rebuilds the file.
- **Backend tests:** `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q`. Never run two pytest processes at once.
- **Lint:** `uv run ruff check . && uv run ruff format --check .`.
- **Web:** `cd web && npm test && npm run build`.
- **Commits** end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

These are the inputs and failures most likely to hurt a real user that no single feature test covers. Each one has a pinned test in the task named.

1. **A paragraph with a footnote reference, crossing bold/italic and a link.** After translation, export must keep the footnote linked and the link's target. The test is `test_translated_docx_keeps_footnote_reference_and_link` in Task 6.
2. **The model returns a block twice, skips one, or invents an id.** Those blocks are retried alone once and then flagged. They are never dropped or stored half. The test is `test_reply_missing_and_duplicate_blocks_are_retried_then_flagged` in Task 4.
3. **The worker stops mid-chapter and resumes.** Translated blocks are not translated or billed again. The test is `test_resume_after_stop_does_not_retranslate_done_blocks` in Task 4.
4. **One paragraph longer than a chunk.** It goes in a chunk of its own, is never split, and gets enough output tokens. The test is `test_long_block_gets_own_chunk_and_scaled_max_tokens` in Task 3.
5. **An agent calls job, export or style tools on the hidden source book id.** It gets a 409 that names the translated book. It must not start a job on the wrong book. The test is `test_source_book_is_read_only_everywhere` in Task 8.

---

### Task 1: Schema for translated books

**Files:**
- Create: `migrations/versions/0008_translation.py`
- Modify: `jerome/orm.py`, `jerome/types.py`
- Test: `tests/test_schema.py`, `tests/core/test_books.py`

**Interfaces:**
- Produces:
  - **`Book` columns:**
    - `work: Mapped[str]`: `'proofread'` or `'translate'`, server default `'proofread'`, with a check constraint `ck_books_work`;
    - `source_book_id: Mapped[int | None]`: FK `books.id`, `ondelete="SET NULL"`;
    - `is_source: Mapped[bool]`: server default false;
    - `translated_title: Mapped[str | None]`.
  - **`Block` columns:**
    - `source_block_id: Mapped[int | None]`: FK `blocks.id`, `ondelete="SET NULL"`, indexed;
    - `translation_status: Mapped[str | None]`: one of `'pending'`, `'done'`, `'flagged'`, `'kept'`, or NULL on a book that is not translated; check constraint `ck_blocks_translation_status`;
    - `placeholders: Mapped[list[int] | None]`: JSONB. For each U+FFFC in the translated text, in order, the 0-based index of the source placeholder it stands for. NULL means the identity order.
  - `Category.TRANSLATION = "translation"` in `jerome/types.py`. The migration rewrites `ck_suggestions_category` to include it.
  - `Job.type` stays a free string; the values are now `'proofread'` and `'translate'`. `JobTask.kind` is unchanged: translate jobs use `'chapter'` tasks.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schema.py
def test_migration_0008_matches_orm(engine): ...  # follow the existing "migrations match ORM" test's pattern

# tests/core/test_books.py
def test_book_work_defaults_to_proofread(session, book):
    assert book.work == "proofread" and book.source_book_id is None and book.is_source is False

def test_translation_status_rejects_unknown_value(session, book):
    blk = book.chapters[0].blocks[0]
    blk.translation_status = "maybe"
    with pytest.raises(IntegrityError):
        session.flush()

def test_translation_category_is_allowed(session, ...):  # insert a Suggestion with category="translation"; flush succeeds
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run pytest tests/test_schema.py tests/core/test_books.py -q`. They should fail on the missing columns.
- [ ] **Step 3: Implement the ORM columns and migration 0008.** Name it `0008_translation`, with down revision `0007_app_settings`. Downgrade drops the columns and restores the old category constraint.
- [ ] **Step 4: Run the full backend suite.** All tests pass.
- [ ] **Step 5: Commit** with `feat: schema for translated books (migration 0008)`.

### Task 2: Import for translation, the hidden source book and its guards

**Files:**
- Create: `jerome/core/translation.py`
- Modify: `jerome/core/books.py`, `jerome/core/projects.py`
- Test: `tests/core/test_translation_import.py`

**Interfaces:**
- Consumes: Task 1 columns.
- Produces, in `jerome/core/translation.py`:
  - `import_for_translation(session, storage, *, filename: str, data: bytes, source_variant: Variant, target_variant: Variant, translated_title: str | None = None, title: str | None = None, local_only: bool = False, auto_apply: AutoApplyPolicy = DEFAULT_POLICY) -> Book`. It returns the **translated** book.
    - It raises `InvalidSetting`-style `JeromeError` (use a new `UnsupportedLanguagePair(JeromeError)` mapped to 400) when the two languages are the same.
    - **Source book:** made with `books.import_book(...)` using the source variant, then `is_source=True`.
    - **Translated book:**
      - `work="translate"`, `source_book_id`, `format` = source format, `title = translated_title or source.title`, `translated_title`, `original_filename` = source's;
      - style sheet: `target_variant` and `DEFAULT_QUOTES[target_variant]`;
      - chapters copied (`ordinal`, `title`, `proofread`, `source_ref`);
      - blocks copied (`ordinal`, `kind`, `source_ref`), with `text=""`, `marks=[]`, `hash` of `""`, `source_block_id`, `translation_status="pending"`, and no segments.
    - The original file is stored once, under the source book only.
  - `source_of(session, book: Book) -> Book | None`: the source book of a translated book.
  - `ensure_not_source(session, book_id: int) -> None`: raises `Conflict(f"book {book_id} is the source of translated book {tid}; work on book {tid}")`.
  - `translation_counts(session, book_id: int) -> dict[str, int]`: counts per `translation_status`.
- Changes in `jerome/core/books.py`:
  - `list_books` excludes `is_source` books.
  - `delete_book`:
    - on a translated book, also deletes its source book and both storage folders;
    - on a source book, raises `Conflict` via `ensure_not_source`.
  - `set_chapter_proofread` calls `ensure_not_source`.
  - `block_texts(session, book_id, chapter_id=None, *, proofreadable_only: bool = False)`. With `proofreadable_only=True` it skips blocks whose `translation_status` is `pending`, `flagged` or `kept`.
- `core/projects.list_projects` excludes `is_source` books.
- `ensure_not_source` is also called by:
  - `core/style_sheets` (set);
  - `core/auto_apply` (set);
  - `jobs/service.create_job`;
  - `core/exports.export_book`;
  - the review-import entry function in `core/review_imports`.

- [ ] **Step 1: Write the failing tests** in `tests/core/test_translation_import.py`. Use `make_docx(["# One", "Hello **bold** world.[^1]", "Second."], footnotes={1: "A note."})` and `tmp_storage`:

```python
def test_import_creates_hidden_source_and_pending_translated_book(session, tmp_storage):
    t = import_for_translation(session, tmp_storage, filename="b.docx", data=DOCX,
                               source_variant="en-GB", target_variant="de-DE")
    src = source_of(session, t)
    assert src.is_source and not t.is_source and t.work == "translate"
    assert get_style_sheet(session, t.id).variant == "de-DE"
    assert get_style_sheet(session, t.id).quote_style == "„“‚‘"
    s_blocks, t_blocks = all_blocks_of(src), all_blocks_of(t)   # helper in the test file
    assert [b.source_ref for b in t_blocks] == [b.source_ref for b in s_blocks]
    assert [b.source_block_id for b in t_blocks] == [b.id for b in s_blocks]
    assert {b.translation_status for b in t_blocks} == {"pending"} and all(b.text == "" for b in t_blocks)
    assert [b.id for b in list_books(session)] == [t.id]

def test_same_language_pair_is_refused(session, tmp_storage):  # en-GB -> en-US raises UnsupportedLanguagePair

def test_translated_title_is_used_as_book_title(...):  # translated_title="Hallo" -> t.title == "Hallo"

def test_deleting_translated_book_deletes_source(session, tmp_storage): ...
def test_deleting_source_book_is_refused(session, tmp_storage): ...
def test_proofreadable_only_skips_untranslated_blocks(session, tmp_storage): ...
def test_proofread_only_import_is_unchanged(session, tmp_storage):  # import_book: work == "proofread", no source
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run pytest tests/core/test_translation_import.py -q`.
- [ ] **Step 3: Implement** the functions and guards listed above. Map `UnsupportedLanguagePair` to 400 in the API `_ERROR_STATUS` and in the MCP `_CODES`.
- [ ] **Step 4: Run the full backend suite.** All tests pass.
- [ ] **Step 5: Commit** with `feat: import a book for translation with a hidden source book`.

### Task 3: Tags, chunking and the translate pass (pure, no database)

**Files:**
- Create: `jerome/translate/__init__.py`, `jerome/translate/tags.py`, `jerome/translate/pass_.py`, `jerome/translate/prompts/translate_en_de.md`, `jerome/translate/prompts/translate_de_en.md`
- Test: `tests/translate/__init__.py`, `tests/translate/test_tags.py`, `tests/translate/test_pass.py`, `tests/translate/fakes.py`

**Interfaces:**
- **`tags.py`, the exact syntax:**
  - Tags by mark kind: `<b>`, `<i>`, `<u>`, `<sc>` (smallcaps), `<sup>`, `<sub>`, `<s>` (strike).
  - A link is `<aN>…</aN>`, where `N` is the 1-based index of the link marks in source order. The href stays in Jerome.
  - Placeholders are `⟦N⟧`: the N-th U+FFFC of the source, 1-based.
  - Literal `<`, `>`, `&`, `⟦` and `⟧` in the text are escaped as `&lt;`, `&gt;`, `&amp;`, `&#x27E6;` and `&#x27E7;`.
  - **Encoding:** text is split into runs of a constant mark set. Each run is wrapped in its tags in a fixed order (`a`, `b`, `i`, `u`, `sc`, `s`, `sup`, `sub`, outermost first). Adjacent runs that share an outer tag share it, so there is no reopen churn for nested formatting.
  - Functions:
    - `encode(text: str, marks: list[Mark]) -> str`.
    - `decode(tagged: str, source_marks: list[Mark], placeholder_count: int) -> Decoded`, with `@dataclass class Decoded: text: str; marks: list[Mark]; placeholders: list[int]; marks_dropped: int`.
  - **`decode` raises `TagError`** when:
    - a placeholder token is missing, duplicated or out of range;
    - tags are unbalanced or crossed;
    - an unknown tag appears.
  - `decode` keeps the text and drops the mark (counting it in `marks_dropped`) for a link index or mark kind not present in the source. Adjacent equal marks are merged. Link marks get the source href by index.
- **`pass_.py`:**
  - `TranslateItem(block_id: int, key: str, text: str, marks: list[Mark])` (frozen dataclass). `block_id` is the **translated** block id; `text` and `marks` are the source's.
  - `TranslateChunk(items: list[TranslateItem])`.
  - `chunk_items(items: Sequence[TranslateItem], target_words: int = 1200) -> list[TranslateChunk]` never splits an item. An item over `target_words` gets a chunk of its own.
  - `TRANSLATION_SCHEMA = {"type": "object", "properties": {"blocks": {"type": "array", "items": {"type": "object", "properties": {"block_id": {"type": "integer"}, "text": {"type": "string"}}, "required": ["block_id", "text"], "additionalProperties": False}}}, "required": ["blocks"], "additionalProperties": False}`.
  - `build_translate_request(source: StyleSheet, target: StyleSheet, chunk: TranslateChunk, context: list[tuple[str, str]]) -> tuple[list[SystemPart], str]`.
    - The system part is the prompt file for the direction, cached. `{source_variant}`, `{target_variant}` and `{quote_style}` are replaced, and the ignore list is appended.
    - In the user part, each `(source_text, translated_text)` context pair becomes a `<context>` element. Each item becomes `<block id="…">encoded text</block>`.
  - `max_tokens_for(chunk) -> int` is `min(32000, max(4000, ceil(source_chars / 3.5 * 1.6) + 1000))`.
  - `run_translate_pass(chunk, source, target, context, provider, model, effort) -> TranslatePassResult`, with `@dataclass class TranslatePassResult: done: dict[int, Decoded]; failed: list[int]; marks_dropped: int; result: CompletionResult`.
    - An item is **failed** when its id is missing from the reply, appears twice, or its `decode` raises `TagError`.
    - Ids in the reply that are not in the chunk are ignored.
- **`tests/translate/fakes.py`:**
  - `echo_translator(prefix: str = "DE ") -> Callable[[dict], dict]`. It is a FakeProvider response callable that parses the `<block id>` elements out of `kwargs["user"]` and returns each block's tagged text with `prefix` put in front.
  - `scripted(replies: dict[int, str | None])`. A `None` value omits that block.

- [ ] **Step 1: Write the failing tests**

```python
# tests/translate/test_tags.py
def test_round_trip_plain_marks_link_and_placeholders():
    text = "Hello bold world￼ see site."
    marks = [Mark(6, 10, MarkKind.BOLD), Mark(6, 16, MarkKind.ITALIC), Mark(21, 25, MarkKind.LINK, "https://x.test")]
    tagged = encode(text, marks)
    assert "⟦1⟧" in tagged and "<a1>" in tagged
    d = decode(tagged, marks, placeholder_count=1)
    assert d.text == text and d.marks == marks and d.placeholders == [0]

def test_reordered_placeholders_are_recorded():  # "⟦2⟧ x ⟦1⟧" -> placeholders == [1, 0]
def test_missing_or_duplicate_placeholder_raises():  # "⟦1⟧⟦1⟧" and "" with count 1 -> TagError
def test_unbalanced_tags_raise():  # "<b>x" -> TagError ; "<b><i>x</b></i>" -> TagError
def test_unknown_link_index_keeps_text_drops_mark():  # "<a3>x</a3>" with one source link -> text "x", marks_dropped == 1
def test_literal_angle_brackets_survive():  # text "a < b & c" round-trips

# tests/translate/test_pass.py
def test_long_block_gets_own_chunk_and_scaled_max_tokens():
    items = [item(1, "w " * 100), item(2, "w " * 3000), item(3, "w " * 100)]
    chunks = chunk_items(items, target_words=1200)
    assert [[i.block_id for i in c.items] for c in chunks] == [[1], [2], [3]]
    assert max_tokens_for(chunks[1]) > max_tokens_for(chunks[0])

def test_pass_decodes_reply_and_reports_failures():
    provider = FakeProvider([scripted({1: "<b>Hallo</b> Welt", 2: None, 3: "kaputt <i>"})])
    r = run_translate_pass(chunk_of(1, 2, 3), EN, DE, [], provider, "m", None)
    assert r.done[1].text == "Hallo Welt" and r.failed == [2, 3]

def test_request_carries_context_ignore_list_and_direction():
    system, user = build_translate_request(EN_GB, DE_DE_WITH_IGNORE(["Usher"]), chunk, [("Old.", "Alt.")])
    assert "en-GB" in system[0].text and "de-DE" in system[0].text and "Usher" in system[-1].text
    assert "<context>" in user and '<block id="' in user

def test_de_to_en_uses_its_own_prompt(): ...
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run pytest tests/translate -q`.
- [ ] **Step 3: Implement `tags.py`, `pass_.py`, the two prompt files and `fakes.py`.**
  - Each prompt must say:
    - translate faithfully and completely, without summarizing;
    - keep every tag and `⟦N⟧` token exactly once, around the matching words;
    - keep names on the ignore list as they are;
    - use the target quote style;
    - return each block exactly once, by id;
    - `<context>` is read-only.
  - Add `jerome/translate/prompts/*.md` to the package data the same way `jerome/proofread/prompts` is included.
- [ ] **Step 4: Run the tests and lint.** They pass.
- [ ] **Step 5: Commit** with `feat: translate pass with tagged marks and placeholders`.

### Task 4: The translate job (estimate, create, worker, retry, keep)

**Files:**
- Modify: `jerome/jobs/estimate.py`, `jerome/jobs/service.py`, `jerome/jobs/worker.py`, `jerome/core/translation.py`, `jerome/config.py`
- Test: `tests/jobs/test_translate_job.py`

**Interfaces:**
- Consumes: Task 2 (`translation_counts`, `source_of`) and Task 3 (`chunk_items`, `run_translate_pass`, `TRANSLATION_SCHEMA`, `Decoded`).
- Produces:
  - `config.Settings.models` default gains `"translate": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}`.
  - `translate_items(session, book_id: int, chapter_id: int) -> list[TranslateItem]`, in `core/translation.py`.
    - It covers **every** block of the chapter in order, whatever its status, so chunk indexes stay stable across resume.
    - Text and marks come from the source block.
  - `estimate_translation(session, registry, book_id) -> Estimate`, in `jobs/estimate.py`.
    - `per_pass = {"translate": CostRange}`.
    - Output tokens are `ceil(1.4 * input_tokens)`.
    - It covers all chapters that have `pending` blocks.
    - It uses `registry.for_pass("translate", local_only=book.local_only)`.
    - With no pass available it raises `Conflict("no translation model is available for this book")`; this is the `local_only` case.
  - `create_translation_job(session, registry, book_id, *, max_cost: Decimal, callback_url: str | None, actor: str) -> Job`, in `jobs/service.py`.
    - It creates `type="translate"`, `passes=["translate"]`, one `'chapter'` task per chapter with pending blocks, and the estimate JSON.
    - It calls `ensure_not_source` first. It then raises `Conflict` when:
      - the book's `work != "translate"`;
      - nothing is pending;
      - another job is active (the existing slot rule).
  - `create_job` (proofread) on a `work="translate"` book raises `Conflict("translate the book before proofreading it")` while any block is `pending`.
  - **Worker:**
    - `_Plan` gains `job_type: str`.
    - `_start` builds `TranslateChunk`s for translate jobs.
    - `_run_chunk` dispatches to a new `_run_translate_chunk` with the same precheck, billing, commit and requeue structure. Per item it skips blocks whose status is not `pending`.
    - `_run_translate_chunk`:
      - The context is the two blocks before the chunk's first item in the chapter, as `(source text, stored translation)` pairs. Pairs whose translation is not `done` are left out.
      - Each failed item is retried **once alone** in the same chunk run, with its own `run_translate_pass` call, also billed.
      - The commit writes the translated text, marks (as JSON, like import), `hash`, `placeholders`, `translation_status` (`done`, or `flagged` with the source text and marks stored as the block text) and segments (via `split_sentences` with the target language).
      - It bumps `job.stats` with `translated`, `flagged` and `marks_dropped`.
      - When the chapter's last chunk commits, it sets `chapter.title` to the translated text of the chapter's first heading block if that block's source text equals the source chapter title.
    - `usage_row` gains a keyword `pass_name: str = "ai"`. The translate path passes `"translate"`. `_record_failure` prices with `models["translate"]` for translate jobs.
  - `retranslate_block(session, registry, block_id: int, *, actor: str) -> Block`, in `core/translation.py`.
    - It runs synchronously on one flagged or done block, with the same context a chunk gets: the two previous source paragraphs and their stored translations.
    - Its cost goes to the book's latest translate job, with `cost_actual` increased and a `ModelUsage` row with `pass_name="translate"`.
    - It raises `Conflict` while a job is active on the book.
    - On a `TagError` the block stays `flagged`.
  - `keep_source_block(session, block_id: int, *, actor: str) -> Block`. It sets a flagged block to `kept`. It is allowed only for DOCX books; otherwise it raises `Conflict("only DOCX books can keep a paragraph untranslated; translate it again")`.

- [ ] **Step 1: Write the failing tests** in `tests/jobs/test_translate_job.py`. Use `session_factory`, `import_for_translation` on `make_docx` and on `tests/fixtures/public/pg932.epub`, a registry with `{"translate": {"provider": "fake", "model": "claude-opus-5-5"}}`, and `echo_translator()`:

```python
def test_translate_job_fills_every_block_and_completes(...):
    job = create_translation_job(s, reg, t.id, max_cost=Decimal("10"), callback_url=None, actor="a")
    drain(session_factory, deps)            # run claim_task/run_task until no task is left (helper in test file)
    assert job_status(job.id) == "completed"
    assert all(b.translation_status == "done" and b.text.startswith("DE ") for b in blocks(t.id))
    assert cost_actual(job.id) > 0 and usage_pass_names(job.id) == {"translate"}

def test_reply_missing_and_duplicate_blocks_are_retried_then_flagged(...):
    # first reply omits block 2 and repeats block 1; the single-block retry for block 2 returns broken tags
    # -> block 2 flagged with its source text, block 1 done once, stats["flagged"] == 1, job completed

def test_resume_after_stop_does_not_retranslate_done_blocks(...):
    # stop event set after the first chunk commit; resume; FakeProvider records calls:
    # no call carries a block id already done; total billed calls == chunks + retries

def test_cost_cap_pauses_translate_job(...):  # max_cost below one chunk's cost -> paused_cost_cap after first chunk
def test_estimate_translation_scales_output_and_needs_a_model(...):  # local_only book with remote-only provider -> Conflict
def test_proofread_job_refused_while_blocks_pending(...):
def test_proofreading_skips_flagged_and_kept_blocks(...):  # after translation, proofread chunks contain no flagged/kept block
def test_chapter_title_follows_translated_heading(...):
def test_retranslate_flagged_block_bills_latest_translate_job(...):
def test_keep_source_only_for_docx(...):
def test_de_to_en_on_public_german_epub(...):  # pg22367.epub, de-DE -> en-GB, echo prefix "EN "
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Keep the worker's invariants in its module docstring true for translate jobs too: lock order job then task, model calls with no transaction open, a failed chunk commits only its spend. The proofreading worker path calls `block_texts(..., proofreadable_only=True)`.
- [ ] **Step 4: Run the full backend suite.** All tests pass, including every existing worker test.
- [ ] **Step 5: Commit** with `feat: translate job in the worker, with retry and keep-source`.

### Task 5: Proofreading a translated book sees the source

**Files:**
- Modify: `jerome/proofread/ai_pass.py`, `jerome/proofread/prompts/proofread_en.md`, `jerome/proofread/prompts/proofread_de.md`, `jerome/jobs/worker.py`, `jerome/jobs/estimate.py`
- Test: `tests/proofread/test_ai_pass_translation.py`

**Interfaces:**
- `Chunk` gains `sources: dict[int, str] | None = None`, mapping a block id to its source paragraph text. It is filled by the worker and the estimate for `work="translate"` books, via a new `source_texts(session, block_ids) -> dict[int, str]` in `core/translation.py`.
- `build_request(style, chunk)`: when `chunk.sources` is set, each `<block id>` is preceded by `<source_block for="ID" read_only="true">…</source_block>`, and one extra cached system part from `prompts/translation_check_{language}.md` is added.
  - The system part says: flag only meaning errors against the source, as category `translation`. Its `original` and `replacement` follow the same rules as other suggestions.
- `_ITEM_PROPERTIES["category"]` already enumerates `Category`, so `translation` becomes valid once Task 1 has added it. Check that `run_ai_pass` keeps a `translation` candidate.
- The web category label for `translation` is "Translation" (EN UI), in Task 10.

- [ ] **Step 1: Write the failing tests:**
  - `test_request_includes_source_blocks_for_translated_book`;
  - `test_request_unchanged_without_sources`: the exact equality of `build_request` output with today's for a chunk without sources;
  - `test_translation_category_candidate_is_kept`;
  - `test_worker_fills_sources_only_for_translated_books`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, including the two new prompt files.
- [ ] **Step 4: Run the full backend suite.** All tests pass.
- [ ] **Step 5: Commit** with `feat: proofreading a translation sees the source paragraph`.

### Task 6: Translated DOCX base file

**Files:**
- Create: `jerome/documents/translated.py` (the shared `TranslatedBlock`), `jerome/documents/docx_translate.py`
- Test: `tests/documents/test_docx_translate.py`

**Interfaces:**
- `@dataclass(frozen=True) class TranslatedBlock: text: str; marks: list[Mark]; placeholders: list[int] | None`, in `documents/translated.py`.
- `write_translated_docx(original: bytes, blocks: Mapping[str, TranslatedBlock], *, language: str, title: str | None) -> bytes`. The keys of `blocks` are block keys, as the reader's `ParsedBlock.key` gives them.
  - **Each keyed paragraph is rebuilt.** Its text runs are replaced by runs per constant mark span of the translation.
    - Each run copies the `w:rPr` of the first source run whose mark set equals the span's. Otherwise it takes the `w:rPr` of the paragraph's first text run, with the mark toggles (`w:b`, `w:i`, `w:u`, `w:smallCaps`, `w:vertAlign`, `w:strike`) set or cleared.
    - Link spans become `w:hyperlink` elements with the source link's `r:id`.
    - Placeholder runs (footnote references, images, fields, tabs and breaks) are moved, not copied, to their translated positions in `placeholders` order.
    - Paragraph properties, bookmarks and the run order of non-text content stay as they are.
  - `language` (e.g. `de-DE`) is written to the `w:lang w:val` of `styles.xml` `w:docDefaults` and replaces any `w:lang` on rebuilt runs.
  - `w:updateFields w:val="true"` is added to `word/settings.xml`, creating the part and its relationship and content-type override if they are missing.
  - When `title` is given it goes to `dc:title` in `docProps/core.xml`.
  - Paragraphs not in `blocks` are left byte-identical.
- `read_docx(write_translated_docx(o, B, ...))` yields, for every key in `B`, a block with exactly `B[key].text` and `B[key].marks`. This is the invariant the rest of the system relies on.

- [ ] **Step 1: Write the failing tests:**

```python
def test_translated_docx_reads_back_as_the_translation():
    src = make_docx(["# Title", "Hello **bold** and *it* text.", "Plain."])
    blocks = translate_all(src, lambda t: "DE " + t)        # helper: TranslatedBlock per key, marks shifted by 3
    out = write_translated_docx(src, blocks, language="de-DE", title="Titel")
    assert {b.key: (b.text, b.marks) for b in all_blocks(out)} == {k: (v.text, v.marks) for k, v in blocks.items()}
    assert 'w:val="de-DE"' in part_xml(out, "word/styles.xml")
    assert "updateFields" in part_xml(out, "word/settings.xml")
    assert "Titel" in part_xml(out, "docProps/core.xml")

def test_translated_docx_keeps_footnote_reference_and_link():
    src = make_docx(["See [the site](https://x.test) now.[^1]"], footnotes={1: "A note."})
    # translation moves the footnote placeholder to the front and keeps the link
    out = write_translated_docx(src, {...}, language="de-DE", title=None)
    xml = part_xml(out, "word/document.xml")
    assert 'w:footnoteReference w:id="1"' in xml and "hyperlink" in xml
    assert read_docx(out) footnote block text is the translated note text

def test_reordered_placeholders_move_elements(): ...
def test_untouched_paragraphs_are_byte_identical(): ...
def test_tracked_export_on_translated_base_opens_and_has_revisions():  # write_docx(translated, changes, tracked) then read back revision ids
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, reusing `docx_xml.iter_paragraph_runs`, `paragraph_runs`, `Piece` and `docx_runs.text_run`/`copy_rpr` where they fit.
- [ ] **Step 4: Run the document tests and the full suite.** They pass.
- [ ] **Step 5: Commit** with `feat: write a translated DOCX on the original's structure`.

### Task 7: Translated EPUB base file

**Files:**
- Create: `jerome/documents/epub_translate.py`
- Test: `tests/documents/test_epub_translate.py`

**Interfaces:**
- `write_translated_epub(original: bytes, blocks: Mapping[str, TranslatedBlock], *, language: str, title: str | None, toc_labels: Mapping[str, str]) -> bytes`.
  - Each keyed block element's text nodes and inline elements are rebuilt from the marks:
    - `b`/`strong`, `i`/`em`, `u`, `sup`, `sub`, `s`, and smallcaps as the source's element and class;
    - `a` with the source `href` by link index.
  - The first source element of a mark kind is reused as the template for its attributes.
  - Placeholder elements are moved in `placeholders` order.
  - It sets `dc:language` in the OPF and `lang`/`xml:lang` on every content document's root `html`.
  - `title`, when given, replaces `dc:title`.
  - `toc_labels` maps a TOC target (the same `file#anchor` form as `Chapter.source_ref` for TOC-based chapters) to its new label. It is applied to nav `a` text and NCX `navLabel/text`. Entries with no mapping are kept.
- `read_epub(write_translated_epub(...))` yields, for every key in `blocks`, exactly the given text and marks.

- [ ] **Step 1: Write the failing tests:**
  - `test_translated_epub_reads_back_as_the_translation`, using `make_epub([("One", ["Hello *it* world."]), ("Two", ["Bye."])])`;
  - `test_toc_labels_rewritten_in_nav_and_ncx` (`toc="nav"` and `toc="ncx"`);
  - `test_language_set_in_opf_and_documents`;
  - `test_public_english_epub_round_trip`: on `pg932.epub`, translate every block with the prefix `"DE "`, write, read back, and get equal texts;
  - `test_untouched_documents_are_byte_identical`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, reusing `epub_xml` (`read_container`, `parse_content_document`, `iter_epub_blocks`) and the serialization helpers in `epub_writer` (`_serialize`, `_keep_end_tags`). Move shared helpers to `epub_xml.py` if they must be imported.
- [ ] **Step 4: Run the document tests and the full suite.** They pass.
- [ ] **Step 5: Commit** with `feat: write a translated EPUB with translated TOC labels`.

### Task 8: Export, REST and MCP for translated books

**Files:**
- Modify:
  - `jerome/core/exports.py`, `jerome/core/translation.py`, `jerome/core/projects.py`;
  - `jerome/api/routes_books.py`, `jerome/api/routes_jobs.py`, `jerome/api/schemas.py`;
  - new `jerome/api/routes_translation.py`, `jerome/api/app.py`;
  - `jerome/mcp/tools_files.py`, `jerome/mcp/tools_jobs.py`, `jerome/mcp/tools_books.py`, new `jerome/mcp/tools_translation.py`, `jerome/mcp/server.py`;
  - `README.md`.
- Test: `tests/core/test_translated_export.py`, `tests/api/test_translation_api.py`, `tests/mcp/test_translation_tools.py`

**Interfaces:**
- **Base file:** `translated_base(session, storage, book: Book) -> bytes`, in `core/translation.py`.
  - It reads `storage.read_original(source.id)` and calls `write_translated_docx` or `write_translated_epub`.
  - The language is the translated style sheet's variant. The title is `book.translated_title`.
  - `toc_labels` maps each translated chapter's `source_ref` to its title.
  - It raises `Conflict(f"{n} paragraphs are not translated yet: blocks {ids[:10]}")` while any block is `pending` or `flagged`. `kept` blocks are written with their stored text.
- **`core/exports`:**
  - For `work == "translate"`, every format, the review copy included, uses `translated_base(...)` in place of `storage.read_original(book.id)`.
  - Exports of the translated book are stored under the translated book's id.
  - The review import of a translated book compares against the same base. Check `core/review_imports` for any read of the original file and route it through a small `original_for(session, storage, book)` helper in `core/translation.py` that returns the base for translated books and the stored original otherwise.
- **`core/projects`:**
  - `Stage` gains `"translating"` and `"translated"`.
  - `derive_stage` gains the keyword-only parameters `work: str = "proofread"`, `latest_job_type: str | None = None` and `pending_blocks: int = 0`. For `work == "translate"` the first matching rule wins:
    1. an active `translate` job gives `translating`;
    2. a latest job of type `translate` that is `failed` or `paused_cost_cap` gives `needs_attention`;
    3. `pending_blocks > 0` gives `set_up`;
    4. a latest job of type `translate` that is `completed` gives `translated`;
    5. otherwise the existing rules apply.
  - `list_projects` rows gain:
    - `work`;
    - `source_variant` (null for proofread);
    - `translation`: `translation_counts` for translated books, else null.
  - `chapter_review` blocks gain `"source": str | None`, the source block text on translated books, and `"translation_status"`.
- **REST:**
  - `POST /v1/books` gains the form fields `work: Literal["proofread", "translate"] = "proofread"`, `target_variant: Variant | None = None` and `translated_title: str | None = None`.
    - With `work="translate"`, `variant` is the source variant, `target_variant` is required (otherwise 422), and the endpoint returns the translated book.
  - `BookOut` gains `work`, `source_book_id` and `translated_title`.
  - `EstimateIn` gains `type: Literal["proofread", "translate"] = "proofread"`. `JobIn.type` accepts `^(proofread|translate)$`. With `translate`, `passes` and `chapters` are ignored and `create_translation_job` / `estimate_translation` are called.
  - New `routes_translation.py`:
    - `POST /v1/blocks/{block_id}/translate` calls `retranslate_block` and returns the block. It is metered by the same text-limits dependency as `/v1/text`.
    - `POST /v1/blocks/{block_id}/keep-source` calls `keep_source_block`.
    - `GET /v1/books/{book_id}/translation` returns `{"counts": translation_counts, "flagged": [{"block_id", "chapter_id", "source"}]}`.
- **MCP:**
  - `import_book` gains `work`, `target_variant` and `translated_title`.
  - `estimate_job` and `start_job` gain `type`.
  - `list_books` hides source books, the same as REST, via `books.list_books`.
  - New tools in `tools_translation.py`: `get_translation(book_id)`, `retranslate_block(block_id)` and `keep_source_block(block_id)`. Annotations follow the existing CREATES/SETTING conventions.
- **README:** a "Translation" section with the work choice, the flow, the stages, flagged blocks and the `translate` model pass.

- [ ] **Step 1: Write the failing tests:**

```python
# tests/core/test_translated_export.py
def test_clean_docx_export_of_translated_book_has_translation_and_accepted_fix(...):
    # translate with echo, accept one proofreading suggestion on a translated block, export docx-clean,
    # read back: block text == translated text with the fix applied
def test_review_copy_and_review_import_round_trip_on_translated_book(...):
def test_export_refused_while_flagged(...):   # Conflict lists block ids
def test_kept_block_is_exported_with_its_text(...):
def test_epub_clean_export_of_public_english_epub(...):  # pg932, nav labels translated

# tests/api/test_translation_api.py
def test_upload_translate_returns_translated_book_and_hides_source(client, auth): ...
def test_upload_translate_without_target_variant_is_422(client, auth): ...
def test_upload_same_language_pair_is_400(client, auth): ...
def test_translate_job_estimate_and_start_over_rest(client, auth): ...
def test_source_book_is_read_only_everywhere(client, auth):
    # for the source id: POST /jobs, POST /estimate, PUT style-sheet, PUT auto-apply, PATCH chapter,
    # GET export, POST review import, DELETE book -> all 409, each detail names the translated book id
def test_projects_list_shows_translating_then_translated_stage(client, auth): ...
def test_retranslate_and_keep_source_endpoints(client, auth): ...

# tests/mcp/test_translation_tools.py
def test_import_translate_and_start_translate_job_over_mcp(...): ...
def test_source_book_hidden_from_mcp_list_books(...): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** the changes listed above. Keep every existing API and MCP test green; their request shapes are unchanged by default.
- [ ] **Step 4: Run the full backend suite and lint.** All pass.
- [ ] **Step 5: Commit** with `feat: export, REST and MCP for translated books`.

### Task 9: The `translate` model setting

**Files:**
- Modify: `jerome/core/app_settings.py`, `jerome/core/settings_store.py` if needed, `web/src/components/settings/ModelSettings.tsx`, `web/src/pages/SettingsPage.tsx`
- Test: `tests/core/test_app_settings.py`, `tests/api/test_settings_api.py`, `web/src/pages/SettingsPage.test.tsx`

**Interfaces:**
- `RUNTIME_KEYS` gains `"models.translate"`, right after `"models.ai"`.
  - It has the same validator as `models.ai`. Generalize `_models_ai` into `_models_pass(name)`, and use the name in error messages.
  - `value_of`, `overlay` and the env-source detection handle it the same way.
  - `overlay` injects `openai_compat_api_key` into **both** `ai` and `translate` when their provider is not `anthropic`.
- `registry_inputs` already hashes all of `settings.models`, so a change to `models.translate` rebuilds the registry.
- The Settings page "AI model" section shows two model editors, **Proofreading model** (`models.ai`) and **Translation model** (`models.translate`), with the same controls, source badge, Save and Reset.

- [ ] **Step 1: Write the failing tests:**
  - backend:
    - `test_models_translate_validates_like_ai`;
    - `test_app_value_for_translate_overrides_env_and_keeps_ai`;
    - `test_openai_compat_key_injected_for_translate`;
    - `test_put_models_translate_applies_to_registry`;
  - web: `renders the translation model editor and saves models.translate`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the backend suite and `cd web && npm test && npm run build`.** All pass.
- [ ] **Step 5: Commit** with `feat: translation model on the Settings page`.

### Task 10: Web — new project, projects list, project page

**Files:**
- Modify:
  - `web/src/lib/api.ts`;
  - `web/src/pages/NewProjectPage.tsx`, `web/src/pages/ProjectsPage.tsx`, `web/src/pages/ProjectPage.tsx`;
  - `web/src/components/StageBadge.tsx`;
  - `web/src/components/project/StartPanel.tsx`;
  - new `web/src/components/project/TranslationPanel.tsx`.
- Test: `web/src/pages/NewProjectPage.test.tsx`, `web/src/pages/ProjectsPage.test.tsx`, `web/src/pages/ProjectPage.test.tsx`, `web/src/components/project/TranslationPanel.test.tsx`

**Interfaces:**
- Consumes: the Task 8 REST shapes (`work`, `target_variant`, `translated_title`, `type`, the `/translation` endpoints, the project row fields and the stages).
- **New project:**
  - A "Work" radio: **Proofread only** (the default) or **Translate + proofread**.
  - With translate:
    - the language select is labeled "From". A "To" select lists only variants of the other language, defaulting to `de-DE` from English and `en-GB` from German.
    - An optional "Translated title" field appears.
    - The quote style default follows the "To" variant.
  - The form sends `work`, `variant` (the source), `target_variant` and `translated_title`.
- **Projects list:** the language shows as `en-GB → de-DE` for translate projects. `StageBadge` labels `translating` as "Translating" and `translated` as "Translated".
- **Project page:**
  - **set_up on a translate project:** `TranslationPanel` shows the translation estimate (`POST /estimate {type: "translate"}`) and Start with a cost cap (`POST /jobs {type: "translate", max_cost}`).
  - **translating:** the existing progress panel, which works on the job's tasks.
  - **translated:** a summary of counts. If any blocks are flagged, it lists them (source text, chapter) with **Translate again** and, on DOCX books, **Keep original**. It also shows the existing proofreading `StartPanel`. While blocks are flagged it carries a note that proofreading will skip them and that export waits until they are resolved; this follows the spec, so Start is not disabled.
  - A link to the source book's details (title, language) appears in the header.
- The export menu shows the export's 409 message as an error toast; that is today's behavior.

- [ ] **Step 1: Write the failing tests:**
  - "sends work=translate with the source and target variants";
  - "To lists only the other language";
  - "shows the language pair and the Translating stage";
  - "set_up translate project shows the translation estimate and starts a translate job";
  - "translated project lists flagged blocks with Translate again and Keep original for DOCX";
  - "proofreading start shows the flagged-blocks note".
- [ ] **Step 2: Run them and see them fail.** Run `cd web && npx vitest run src/pages src/components/project`.
- [ ] **Step 3: Implement**, following the existing shadcn/ui, react-query and toast patterns, in light and dark themes.
- [ ] **Step 4: Run `cd web && npm test && npm run build`.** It passes. The known flake "set_up shows the start panel…" in `ProjectPage.test.tsx` may need a rerun. A second failure is real.
- [ ] **Step 5: Commit** with `feat(web): translate + proofread projects`.

### Task 11: Web — review shows the source paragraph

**Files:**
- Modify: `web/src/pages/ReviewPage.tsx`, `web/src/components/review/Paragraph.tsx`, `web/src/components/review/FocusCard.tsx`, `web/src/lib/review.ts`, and the category label map (wherever `consistency` is labeled)
- Test: `web/src/components/review/Paragraph.test.tsx`, `web/src/pages/ReviewPage.test.tsx`

**Interfaces:**
- Consumes the Task 8 `chapter_review` block field `source` (a string or null).
- When `source` is set, each paragraph shows the source text beside it at widths ≥ 1024 px, and under it behind a "Show original" toggle on narrower screens. With `source` null, nothing changes.
- The focus card shows the source paragraph for the focused suggestion's block.
- The category filter and labels include "Translation".

- [ ] **Step 1: Write the failing tests:**
  - "shows the source paragraph next to a translated block";
  - "no source column for a proofread-only book";
  - "translation category is filterable".
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `cd web && npm test && npm run build`.** It passes.
- [ ] **Step 5: Commit** with `feat(web): source paragraph beside translated text in review`.

### Task 12: End-to-end check

**Files:**
- Test: `tests/test_translation_end_to_end.py`

- [ ] **Step 1: Write the end-to-end test.** Over the REST `client`, with a fake `translate` provider (`echo_translator("DE ")`) and a fake `ai` provider that proposes one fix on a translated block:
  1. upload `make_docx` with `work=translate` (`en-GB`→`de-DE`);
  2. estimate and start a translate job; drain the worker; the stage is `translated`;
  3. start proofreading; drain; the stage is `to_review`;
  4. accept the suggestion and export `docx-clean`;
  5. read the export back: every paragraph starts with `"DE "` and has the fix; `w:lang` is `de-DE`; the footnote is linked.

  Repeat steps 1–5 for `tests/fixtures/public/pg22367.epub` (`de-DE`→`en-GB`, echo `"EN "`), exporting `epub-clean`, with the TOC labels translated.
- [ ] **Step 2: Run it.** It passes. If it does not, fix the owning task's code and add the missing unit test there.
- [ ] **Step 3: Run the full backend suite, lint, and the web test and build.** All pass.
- [ ] **Step 4: Commit** with `test: translate + proofread end to end`.
