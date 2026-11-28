# Book Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a read-only Preview page that shows a book's original, translation and proofread
text, in a Text view and a Book view, single or side by side. Also download of the original
file.

**Architecture:**
- A new core module, `jerome/core/preview.py`, builds per-paragraph states from the blocks,
  their source blocks and their accepted or edited suggestions. It uses the same change rule
  as the clean export.
- Two new GET routes serve the preview and the original file. The API also serves the
  EB Garamond fonts at `/fonts/`.
- The web app gets a `/projects/:id/preview/:chapterId?` page. It has one paragraph per
  grid row and one column per state, and draws each cell as Text or Book.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest; React 19, TanStack Query, react-router 7, vitest, Tailwind.

**Spec:** `docs/superpowers/specs/2026-11-27-book-preview-design.md`

## Global Constraints

- **Read only.** The preview never changes a book, a block or a suggestion. No POST, PATCH
  or DELETE route is added.
- **Proofread is the clean export's text.** It is the block text with exactly the accepted
  and edited suggestions applied:
  - edited ones use `final_text`;
  - they are ordered by start, end and id, as `jerome.core.exports.recorded_changes` orders
    them;
  - they are applied by `jerome.documents.changes.apply_changes`.
- **Paging** works as on `/v1/chapters/{id}/review`: `offset ≥ 0` and `1 ≤ limit ≤ 200`,
  default 50.
- **Access:**
  - any authenticated principal (web session or API key);
  - a hidden source book gets the 409 from `jerome.core.translation.ensure_not_source`;
  - a book that is still importing or whose import failed gets the 409 from
    `jerome.core.books.ensure_imported`.
- **States offered:**
  - Original, Translation and Proofread for a translated book (`work == "translate"`);
  - Original and Proofread for a proofread-only book.
- **Defaults:** Side by side, with Original and Proofread, in the Text view, with Show
  changes on.
- **The address:** the choice is kept as query parameters
  `view=text|book`, `layout=single|side`, `states=original,translation,proofread` (comma list)
  and `changes=0|1`. The last choice is also kept in `localStorage` under
  `jerome.preview`, with every access wrapped in try/catch.
- **Copy (exact):**
  - state headers "Original", "Translation", "Proofread";
  - the note "Not proofread yet" on the Proofread header when the chapter's `proofread` is
    false;
  - "Not translated yet" in a Translation cell whose block status is `pending`;
  - "Picture" in the Book view box for U+FFFC;
  - buttons and links "Preview", "Download original" and "Download original PDF".
- **The download file name** is `<visible book title>.<ext of the stored original.* file>`.
  The title is made safe like `exports._safe_title`, and the header is sent through
  `routes_exports.content_disposition`.
- **No new runtime dependencies,** in Python or npm.
- **Public repository:** no book files and no owner-specific names in code or docs. Tests use
  the builders in `tests/fixtures/`.
- **Style:** ruff with line length 100. Commits end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A paragraph with an accepted change inside a bold or italic span, and one replacing a
   whole italic word. The formatting must stay on the right letters in the Proofread
   column, not shift by the length difference. Pinned in Task 1:
   `test_marks_follow_changes`.
2. A translated book whose chapter is still being translated. Pending blocks must show
   "Not translated yet" and must not crash on empty text. Pinned in Task 1
   (`test_pending_translation_block`) and Task 3.
3. A long chapter, more than 200 paragraphs. Every paragraph must arrive through paging,
   with none repeated or lost. Pinned in Task 2: `test_preview_pages_without_gaps`.
4. Overlapping or touching accepted suggestions (the AI auto-apply can produce adjacent
   ones). The preview must equal the export, or fail the same way. Pinned in Task 1:
   `test_proofread_equals_clean_export_rule`.
5. A downloaded original with a non-ASCII title. The `Content-Disposition` header must stay
   valid. Pinned in Task 2: `test_original_download_name_non_ascii`.

---

### Task 1: Core: the three states of a paragraph

**Files:**
- Create: `jerome/core/preview.py`
- Test: `tests/core/test_preview.py`

**Interfaces:**
- Consumes:
  - `Block` and `Suggestion` from `jerome.orm`;
  - `Change` and `apply_changes` from `jerome.documents.changes`;
  - `Mark` and `MarkKind` from `jerome.documents.model`;
  - `marks_from_json`, `marks_to_json` and `ensure_not_source` from
    `jerome.core.translation`;
  - `ensure_imported` from `jerome.core.books`.
- Produces:
  - `proofread_changes(suggestions: Iterable[Suggestion]) -> list[Change]`: accepted and
    edited only, ordered by `(start, end, id)`;
  - `shift_marks(marks: list[Mark], changes: list[Change]) -> list[Mark]`;
  - `applied_spans(text: str, changes: list[Change]) -> list[dict]`: one
    `{"start", "end", "removed"}` per change, on the proofread text;
  - `chapter_preview(session: Session, chapter_id: int, offset: int, limit: int) -> dict`,
    shaped:
    ```
    {"chapter_id", "book_id", "title", "proofread": bool, "total_blocks", "offset",
     "work": "proofread"|"translate", "variant", "source_variant": str|None, "trim_size",
     "blocks": [{"id", "ordinal", "kind",
                 "original": {"text", "marks"} | None,
                 "translation": {"text", "marks", "status"} | None,
                 "proofread": {"text", "marks", "changes"}}]}
    ```
    Marks are in the `marks_to_json` shape. For a proofread-only book `original` is the
    block's own text and marks, and `translation` is None. For a translated book,
    `original` is the source block (None when `source_block_id` is null), `translation` is
    the block, and `proofread` is built on the block's text. `variant` is the book's style
    sheet variant, and `source_variant` is the source book's (translated books only).

- [ ] **Step 1: Write the failing tests** in `tests/core/test_preview.py`
  - `test_proofread_changes_keeps_accepted_and_edited_only`:
    - with open, rejected, accepted (replacement "B") and edited (`final_text` "E")
      suggestions on one block, `proofread_changes` returns 2 Changes;
    - the replacements are "B" and "E", in start order.
  - `test_proofread_equals_clean_export_rule`:
    - import a DOCX with `make_docx`, then add accepted, edited, adjacent and rejected
      suggestions;
    - for each block, `chapter_preview(...)["blocks"][i]["proofread"]["text"]` equals
      `apply_changes(block.text, recorded_changes(session, book.id).get(block.source_ref, []))`.
  - `test_marks_follow_changes`: `shift_marks` on the base text "aa *bbbb* cc", with
    italic (3,7):
    - replacing (4,6)→"XYZ" gives italic (3,8): inside, so the mark grows;
    - replacing (0,2)→"A" gives italic (2,6): before, so it shifts;
    - replacing (7,8)→"" gives italic (3,7): at the edge after, so it is unchanged;
    - replacing (3,7)→"q" gives italic (3,4): the whole span replaced, so the mark wraps
      the replacement;
    - deleting (2,9)→"" drops the mark.
  - `test_applied_spans`:
    - on "the cat", the accepted change (4,7)→"dog" gives the proofread text "the dog";
    - the span is `[{"start": 4, "end": 7, "removed": "cat"}]`.
  - `test_translated_block_states`:
    - a translated book from `import_for_translation` (see
      `tests/core/test_translation_import.py::_import`) has a block set to text "Hallo Welt"
      with status `done` and one accepted change;
    - `original.text` is the source block's text;
    - `translation.text` is "Hallo Welt" and `translation.status` is "done";
    - `proofread.text` has the change applied;
    - `source_variant` is "en-GB" and `variant` is "de-DE".
  - `test_pending_translation_block`: a pending block gives `translation.status ==
    "pending"` and `proofread.text == ""` with no error.
  - `test_proofread_only_book`:
    - `translation` is None and `original` equals the block's own text;
    - `work == "proofread"` and `source_variant is None`.
  - `test_hidden_source_and_importing_are_refused`:
    - a chapter of the hidden source book raises `Conflict`;
    - a book with `import_status="importing"` raises `Conflict`;
    - an unknown chapter raises `NotFound`.

- [ ] **Step 2: Run** `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q tests/core/test_preview.py`.
  Expected: all FAIL with `ModuleNotFoundError: jerome.core.preview`.

- [ ] **Step 3: Implement `jerome/core/preview.py`.**
  - **`shift_marks`** maps each mark's ends through every change, with a running length
    difference, as follows:
    ```
    start s → if s <= cs: s           (before or at the change start)
              elif s >= ce: s + d     (after the change; d = len(rep) - (ce - cs))
              else: cs + len(rep)     (starts inside removed text: after the insertion)
    end e   → if e >= ce: e + d       (covers the change: the insertion takes the mark)
              elif e <= cs: e
              else: cs                (ends inside removed text: before the insertion)
    ```
    - Exceptions for a pure insertion (`cs == ce`) at a mark's edge, which never takes the
      mark:
      - at its end (`e == cs`), `e` stays;
      - at its start (`s == cs`), `s` moves to `cs + len(rep)`.

      Add a test case for each: on "aa *bbbb* cc", inserting "Z" at 7 gives italic (3,7),
      and inserting "Z" at 3 gives italic (4,8).
    - Drop marks whose new start is not less than their new end.
    - Apply the changes right to left, so each change's offsets are still in base text
      coordinates.
  - **`applied_spans`:** walk the changes left to right, accumulating the shift, and give
    each change's inserted range on the proofread text together with the removed base
    text.
  - **`chapter_preview`:**
    - load the chapter's blocks by `(ordinal, id)` with offset and limit;
    - load their suggestions in one query;
    - load the source blocks in one aliased join, as `translation.source_texts` does, but
      also selecting `marks`;
    - call `ensure_not_source(session, chapter.book_id)` and
      `ensure_imported(chapter.book)` first.

- [ ] **Step 4: Run the same command.** Expected: PASS. Then run
  `uv run ruff check jerome tests && uv run ruff format --check jerome tests`.

- [ ] **Step 5: Commit**
  `feat: the original, translation and proofread states of a chapter's paragraphs`.

### Task 2: API: preview route, original download, fonts

**Files:**
- Create: `jerome/api/routes_preview.py`
- Modify:
  - `jerome/api/app.py`: include the router with the `auth` dependency, like the others;
  - `jerome/api/web.py`: add `"fonts"` to `_API_PREFIXES` and mount the fonts;
  - `jerome/storage.py`: add `original_suffix(book_id) -> str | None` and
    `has_source_pdf(book_id) -> bool`;
  - `web/vite.config.ts`: proxy `/fonts` like `/v1`;
  - `README.md`: a "Preview" paragraph under "Web interface", with the two routes.
- Test: `tests/api/test_preview_api.py`

**Interfaces:**
- Consumes: `chapter_preview` (Task 1); `content_disposition` from
  `jerome.api.routes_exports`; `_safe_title` from `jerome.core.exports`. Rename it to
  `safe_title` and keep the old name as an alias if other modules import it.
- Produces:
  - `GET /v1/chapters/{chapter_id}/preview?offset=&limit=` returns the Task 1 dict;
  - `GET /v1/books/{book_id}/original` returns the stored original. For a translated book
    it returns the source book's file;
  - `GET /v1/books/{book_id}/original?pdf=true` returns `source.pdf` from the book or its
    source book, and 404 when neither has one;
  - `GET /fonts/{name}` serves the files in `jerome/publish/fonts/` with the content type
    `font/ttf`, without auth, and 404 for anything else. This route goes in `create_app`,
    so it works whether or not the web app is mounted.

- [ ] **Step 1: Write the failing tests:**
  - `test_preview_route`: for an imported DOCX book, the request returns 200 with
    `blocks[0]["proofread"]["text"]`. It returns 401 without auth and 422 with `limit=201`.
  - `test_preview_pages_without_gaps`:
    - a DOCX of 230 paragraphs from `make_docx` is read with `limit=200` and then
      `offset=200`;
    - the ordinals are 0..229, each exactly once.
  - `test_original_download_docx`:
    - the bytes equal the uploaded DOCX;
    - the media type is the DOCX one;
    - `Content-Disposition` contains `filename="Test Book.docx"`.
  - `test_original_download_translated`: on the translated book the request serves the
    source file's bytes, and on the hidden source book id it returns 409.
  - `test_original_download_pdf`: for a book with `storage.save_source_pdf` (use
    `tests/fixtures/pdf_builders.text_pdf`), `?pdf=true` returns the PDF bytes as
    `application/pdf`. A DOCX-only book returns 404.
  - `test_original_download_name_non_ascii`:
    - with the title "Čarobnjak / Ž", the header holds an ASCII fallback with no `/` and
      the RFC 5987 `filename*`, as `content_disposition` makes them.
  - `test_fonts_are_served`: `GET /fonts/EBGaramond-Regular.ttf` returns 200 with no auth,
    and `GET /fonts/../orm.py` returns 404.

- [ ] **Step 2: Run** `uv run pytest -q tests/api/test_preview_api.py` (with the test DB
  URL). Expected: all FAIL with 404.

- [ ] **Step 3: Implement the routes, storage helpers, font route, Vite proxy and README
  paragraph.**
  - **Media types:** `.docx` →
    `application/vnd.openxmlformats-officedocument.wordprocessingml.document` and `.epub` →
    `application/epub+zip`.
  - **The font route** takes only names matching `^[A-Za-z0-9-]+\.ttf$` that exist in the
    folder.

- [ ] **Step 4: Run the tests, plus `tests/test_schema.py` and `tests/mcp/test_errors.py`.**
  Expected: PASS. Then run ruff.

- [ ] **Step 5: Commit** `feat: preview and original-file routes; serve the book fonts`.

### Task 3: Web: Preview page, Text view, layouts and states

**Files:**
- Create:
  - `web/src/lib/preview.ts`: the types, `usePreview(chapterId)` (an infinite query over
    `/v1/chapters/{id}/preview`, page size 100), and `readPrefs()` / `writePrefs()` for
    localStorage;
  - `web/src/pages/PreviewPage.tsx`;
  - `web/src/components/preview/PreviewToolbar.tsx`;
  - `web/src/components/preview/TextCell.tsx`.
- Modify: `web/src/App.tsx` (route `/projects/:id/preview/:chapterId?`).
- Test: `web/src/pages/PreviewPage.test.tsx`, `web/src/components/preview/TextCell.test.tsx`

**Interfaces:**
- Consumes:
  - the Task 2 route;
  - `GET /v1/books/{id}` (its `work` field);
  - `GET /v1/books/{id}/chapter-summary` for the chapter list, as `useChapterReview.ts`
    does.
- Produces (for Task 4):
  - `type PreviewState = "original" | "translation" | "proofread"`;
  - `type Prefs = { view: "text" | "book"; layout: "single" | "side"; states:
    PreviewState[]; changes: boolean }`;
  - `type MarkJson = { start: number; end: number; kind: string; href: string | null }`;
  - `type Span = { start: number; end: number; removed: string }`;
  - `type StateText = { text: string; marks: MarkJson[]; changes?: Span[]; status?: string }`;
  - `PreviewPage` renders each cell through
    `cellFor(view, state, block, prefs): ReactNode`, so Task 4 only adds the `"book"`
    branch.

- [ ] **Step 1: Write the failing tests** (mock the API with `mockApi` from
  `@/test-utils`):
  - `TextCell`:
    - for the proofread text "the dog" with the span `{start: 4, end: 7, removed: "cat"}`
      and `changes` on, it renders `<del>cat</del>` before `<ins>dog</ins>`;
    - with `changes` off it renders the plain "the dog" and no `del` or `ins`.
  - **The translated default:** a translated book at `/projects/7/preview/3` shows the
    column headers "Original" and "Proofread", side by side. Each paragraph's two cells
    are in the same row (`role="row"`).
  - **Single layout:** picking Layout → Single and States → Translation shows one column,
    "Translation", and the URL search contains `layout=single&states=translation`.
  - **A proofread-only book:** the state choices do not offer Translation.
  - **A pending block:** its Translation cell shows "Not translated yet".
  - **An unproofread chapter:** when the chapter's `proofread` is false, the Proofread
    header shows "Not proofread yet".
  - **Address over memory:** opening `/projects/7/preview/3?view=text&layout=side&states=original,translation,proofread`
    shows three columns, even when `localStorage` holds other prefs.
  - **Paging:** a second page is requested with `offset=100` when the "Load more" button is
    clicked.

- [ ] **Step 2: Run** `npx vitest run src/pages/PreviewPage.test.tsx src/components/preview`
  (in `web/`). Expected: FAIL, because the modules do not exist.

- [ ] **Step 3: Implement the page.**
  - **The grid:** CSS grid with `role="table"`, one `role="row"` per block and one
    `role="cell"` per chosen state. The column headers are sticky (`top-0`).
  - **Invalid state lists:**
    - in Single, keep only the first state;
    - in Side, keep 2 or 3 states, refilling to the defaults when fewer than 2 are left;
    - drop `translation` on a proofread-only book.
  - **Paging:** an IntersectionObserver on the "Load more" button fetches the next page
    when it scrolls into view. The button stays as the fallback.
  - **Chapter selector:** a `NativeSelect`, the same as the review toolbar's. A change
    navigates to `/projects/:id/preview/:chapterId` and keeps the query string.
  - **Links:** a "Back to the project" link.
  - **No chapter in the address:** without `:chapterId`, replace the route with the first
    chapter from the chapter summary.

- [ ] **Step 4: Run the tests above, then `npx tsc -p tsconfig.app.json --noEmit`.**
  Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: Preview page with the Text view, single or side by side`.

### Task 4: Web: the Book view

**Files:**
- Create: `web/src/components/preview/BookCell.tsx`, `web/src/components/preview/book.css`
- Modify: `web/src/pages/PreviewPage.tsx` (the `"book"` branch of `cellFor`, footnotes after
  the grid)
- Test: `web/src/components/preview/BookCell.test.tsx`, plus one case in `PreviewPage.test.tsx`

**Interfaces:**
- Consumes: `StateText`, `Prefs` and `cellFor` (Task 3); `/fonts/EBGaramond-*.ttf` (Task 2).
- Produces: `BookCell({ block, state, lang, firstAfterHeading }): JSX.Element`.

- [ ] **Step 1: Write the failing tests:**
  - **Formatting:** marks `italic` (0,3), `bold` (4,7), `smallcaps`, `superscript`,
    `subscript`, `underline` and `link` (with an href) render as `em`, `strong`, a
    small-caps span, `sup`, `sub`, `u` and `a[href]` around the right letters.
  - **Pictures:** a text with U+FFFC renders an element with the text "Picture" and
    `role="img"`, `aria-label="Picture"`.
  - **Block kinds:**
    - `kind="heading"` renders an `h2`;
    - `quote` renders a `blockquote`;
    - `list_item` renders an `li` inside a `ul`;
    - `table_cell` renders a `p`.
  - **Language:** the cell's root has `lang` set from the column's variant, the source
    variant for Original and the book variant for the others.
  - **Footnotes (page test):** in the Book view, footnote blocks are not in the grid
    rows. They are listed under the grid in an `ol` titled "Notes", in order, in each
    chosen column.
  - **Indents:** a paragraph right after a heading has the class `no-indent`.

- [ ] **Step 2: Run** `npx vitest run src/components/preview src/pages/PreviewPage.test.tsx`.
  Expected: the new cases FAIL.

- [ ] **Step 3: Implement.**
  - **Fonts:** `book.css` declares `@font-face` for EB Garamond Regular, Italic, Bold and
    BoldItalic from `/fonts/`.
  - **The `.book` class:**
    - `font-family: "EB Garamond", serif`;
    - `text-align: justify`, `hyphens: auto` and `line-height: 1.45`;
    - `p + p { text-indent: 1.2em }`;
    - headings centered, larger and with spacing.
  - **Column width:** the trim size sets the max width of a Single column: 6x9in → 30rem,
    5.5x8.5in → 27rem, 5x8in → 25rem, a5 → 26rem.
  - **Marks:** split the text at every mark boundary and nest the marks in this fixed
    order: link, bold, italic, underline, smallcaps, sup, sub.

- [ ] **Step 4: Run the tests and `tsc`.** Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: the Book view of the preview, set like a book page`.

### Task 5: Web: Preview button and downloads on the Project page

**Files:**
- Modify: `web/src/pages/ProjectPage.tsx` (the header)
- Test: `web/src/pages/ProjectPage.test.tsx`

**Interfaces:**
- Consumes: the routes from Task 2 and the page from Task 3.
- Produces: the header links.

- [ ] **Step 1: Write the failing tests:**
  - **Preview:** an imported book's header has a "Preview" link to `/projects/5/preview`.
  - **Download original:** it has a "Download original" link with
    `href="/v1/books/5/original"`.
  - **PDF imports:** the "Download original PDF" link (`/v1/books/5/original?pdf=true`)
    appears only when the book's `import_report` is not null, which marks a PDF import.
  - **Unfinished imports:** a book with `import_status` set to "importing" or "failed"
    shows neither Preview nor the downloads.

- [ ] **Step 2: Run** `npx vitest run src/pages/ProjectPage.test.tsx`. Expected: the new
  cases FAIL.

- [ ] **Step 3: Implement the links** in the header, before "Activity for this book".
  Preview is a small outline Button wrapping a `Link`, and the downloads are plain `<a>`
  with the `download` attribute.

- [ ] **Step 4: Run the whole web suite with `npx vitest run --testTimeout=30000`, then
  `tsc`.** Expected: all pass. Rerun alone any file that fails in the full run before
  calling it a failure, because this Mac times out under load.

- [ ] **Step 5: Commit** `feat: Preview and Download original on the project page`.
