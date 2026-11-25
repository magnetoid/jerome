# PDF Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import a PDF, whether it has a text layer or is scanned (OCR by an AI model or by Tesseract), as a clean DOCX that becomes the book's original. Everything after that works as for any DOCX book.

**Architecture:** A new package `jerome/pdfimport/` does five things:

- reads a PDF's pages (text layer via pdfminer.six, renders via pypdfium2, in a child process with limits);
- OCRs page images (Tesseract CLI, or the `ocr` model pass with an image input);
- assembles the pages into blocks: headers and footers dropped, hyphens joined, paragraphs joined across pages, footnotes matched;
- writes those blocks as a DOCX;
- runs as an `import` job in the existing worker, in three task kinds.

The finished DOCX goes through the existing `import_book` population code.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, pdfminer.six, pypdfium2, Pillow, the `tesseract` program; React and vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-pdf-import-design.md`

## Global Constraints

- **Public repo:**
  - tests use only PDFs generated in code (WeasyPrint, Pillow) or the public fixtures in `tests/fixtures/public/`;
  - never the owner's books;
  - no generated PDFs, DOCX files or images are committed.
- **New dependencies:** `pdfminer.six>=20250506` and `pypdfium2>=4.30` (runtime). Docker installs `tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu`.
- **Limits:**
  - at most 2000 pages;
  - `max_pdf_upload_mb` defaults to 200;
  - DOCX and EPUB keep `max_upload_mb`.
- **Page tasks:** a page range is 25 pages. Scans render at 300 dpi.
- **OCR languages:** `en-*` uses Tesseract `eng`, `de-*` uses `deu`.
- **`local_only` books:** they never use the AI engine, and the API refuses `ocr_engine=ai` for them with 422.
- **AI OCR spend:** none before the owner confirms a `max_cost`, except that the free sorting step runs at once. Model usage is recorded with pass name `"ocr"`.
- **Default engine:** `ai` when the `ocr` pass resolves to a provider (`registry.for_pass("ocr", local_only=False)` is not None) and the book is not local_only, otherwise `tesseract`. `reocr` defaults to true.
- **Storage:**
  - the generated DOCX is the original, saved as `<pdf stem>.docx`;
  - the PDF is saved as `source.pdf` in the book dir;
  - page results go to `import/page-NNNN.json` in the book dir.
- **Failed pages:** never written into the DOCX. The book still becomes ready, and the failures are listed in `import_report["failed_pages"]`.
- **MCP:** `import_book` refuses a `.pdf` with "PDF import is not available through Janus yet; upload it on the New project page".
- **Exact copy:**
  - project page status: "Importing… page {done} of {pages}";
  - estimate banner: "Estimate ready: {n} pages need AI OCR, about {cost}";
  - buttons: "Confirm" and "Use Tesseract instead";
  - failed import: "Import failed: {error}";
  - finished import with failed pages: "{n} pages could not be read: {list}".

## Review Focus

1. **A PDF with an invisible OCR layer over a full-page image, and `reocr=false`.** The existing layer is used and no OCR runs. With `reocr=true`, the chosen engine runs. Tested in Task 1 and Task 7.
2. **A page that crashes or times out the child process.** That page alone is a failed page, and the rest of the book imports. Tested in Task 1 (`test_a_page_that_kills_the_reader_fails_alone`).
3. **The owner turns AI down at the estimate.** The AI pages are read by Tesseract, and no usage row with pass `ocr` is written. Tested in Task 7.
4. **A running header that repeats with the page number in it** ("Chapter One · 12"). It is dropped on every page. Tested in Task 4.
5. **Review, jobs or publish asked for while the book is importing.** Each gets a 409 with "the book is still importing". Tested in Task 6.

---

### Task 1: Dependencies, test PDFs, and reading a page's text layer

**Files:**
- Modify: `pyproject.toml`, `Dockerfile`
- Create: `jerome/pdfimport/__init__.py`, `jerome/pdfimport/model.py`, `jerome/pdfimport/reader.py`, `tests/fixtures/pdf_builders.py`
- Test: `tests/pdfimport/test_reader.py`

**Interfaces:**
- Produces, in `model.py`:
  - `Run(text: str, italic: bool = False, bold: bool = False)`
  - `PageBlock(kind: Literal["heading","paragraph","footnote","image"], runs: list[Run], level: int = 1, note: int | None = None, image: bytes | None = None, image_type: str | None = None, top: float = 0.0, bottom: float = 0.0, size: float = 0.0, centered: bool = False)`
    - `top` and `bottom` are fractions of the page height, from the top.
    - `note` is the footnote number on a footnote, or the reference number on a paragraph run marked with `[^n]`.
  - `PageKind = Literal["text","scan","scan_with_layer","image","blank"]`
  - `PageResult(page: int, source: Literal["text","tesseract","ai","image","failed"], blocks: list[PageBlock], error: str | None = None)`
  - `to_json(r: PageResult) -> dict` and `from_json(d: dict) -> PageResult`. Image bytes are base64.
- Produces, in `reader.py`:
  - `count_pages(pdf: bytes) -> int`. Raises `UnsupportedDocumentError` for an encrypted or unreadable PDF, or one over 2000 pages.
  - `classify_pages(pdf: bytes) -> list[PageKind]`
  - `read_text_page(pdf: bytes, page: int) -> PageResult`
  - `run_isolated(fn, *args, timeout: float = 120, memory_mb: int = 1500)`. This is the spawn-child pattern of `jerome/publish/pdf.py`. It raises `PageReadError` on a timeout, a crash or an exception.
- Produces, in `tests/fixtures/pdf_builders.py`:
  - `text_pdf(html_body: str, *, header: str | None = None, page_numbers: bool = False) -> bytes`, made with WeasyPrint;
  - `scan_pdf(text_pdf_bytes: bytes, *, with_layer: bool = False) -> bytes`. It renders each page to PNG with pypdfium2, then builds a PDF of full-page images with Pillow. With `with_layer`, invisible text is drawn over the images (WeasyPrint HTML: a full-page `<img>` plus text with `color: transparent`).

Steps:

- [ ] **Add dependencies.**
  - Add `pdfminer.six>=20250506` and `pypdfium2>=4.30` to `[project].dependencies` and run `uv lock`.
  - In the Dockerfile `apt-get install` list, add `tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu`.
- [ ] **Write the failing tests** in `tests/pdfimport/test_reader.py`:
  - `test_count_pages`: a 3-page `text_pdf` returns 3. Bytes `b"not a pdf"` raise `UnsupportedDocumentError`.
  - `test_classify_text_scan_and_layer`:
    - a text page gives `"text"`;
    - `scan_pdf(...)` gives `"scan"`;
    - `scan_pdf(..., with_layer=True)` gives `"scan_with_layer"`;
    - an empty page gives `"blank"`.
  - `test_read_text_page_finds_heading_italic_and_paragraphs`:
    - input: `<h1>Arrival</h1><p>The <em>ship</em> came in.</p><p>It rained.</p>`;
    - expected: a heading block `"Arrival"` with `level == 1`, then a paragraph whose runs give `"The "`, `"ship"` (italic) and `" came in."`, then a paragraph `"It rained."`.
  - `test_a_page_that_kills_the_reader_fails_alone`: `run_isolated` given a function that calls `os._exit(1)` raises `PageReadError`.
- [ ] **Run the tests:** `uv run pytest tests/pdfimport/test_reader.py -q`. Expected: FAIL (import errors).
- [ ] **Implement `model.py` and `reader.py`.**
  - Group pdfminer `LTTextLine`s into paragraphs by line gap and indent.
  - A line is a heading when its median font size is at least 1.2 times the page's body size, or when it is alone, centered and shorter than 80 characters.
  - A character is italic or bold when its font name contains `Italic`/`Oblique` or `Bold`.
  - Classifying:
    - `scan` is an image covering at least 90% of the page with no text;
    - `scan_with_layer` is the same image with text;
    - `image` is a page that has images but covers less than 90% and has no text;
    - `blank` is a page with no text and no images.
- [ ] **Run the tests again.** Expected: PASS. Then run `uv run ruff check jerome tests`.
- [ ] **Commit:** `feat: read a PDF's pages and their text layer`.

### Task 2: Rendering pages and Tesseract OCR

**Files:**
- Create: `jerome/pdfimport/ocr_tesseract.py`, `jerome/pdfimport/render.py`
- Test: `tests/pdfimport/test_tesseract.py`

**Interfaces:**
- Produces:
  - `render_page(pdf: bytes, page: int, dpi: int = 300) -> bytes` returns a PNG. It runs through `run_isolated`.
  - `tesseract_page(png: bytes, page: int, language: Literal["en","de"]) -> PageResult`, with source `"tesseract"`.
    - It runs `tesseract stdin stdout -l eng|deu --psm 3 tsv`, with a 120 s timeout.
    - It groups the TSV into paragraphs by `block_num`/`par_num`.
    - A paragraph is a heading when its mean word height is at least 1.3 times the page median.
    - `top` and `bottom` come from the boxes.
  - `TesseractMissing(JeromeError)` is raised when the program is not installed.
  - A page whose rendered image has fewer than 5 words of OCR text gives a single `image` block holding the whole page PNG (source `"image"`).
- Ruling: Tesseract's TSV does not mark picture regions, so on a Tesseract page only a picture-only page keeps its picture. The AI engine crops picture regions (Task 3). If this is wrong, pictures on mixed scanned pages read by Tesseract are lost; the report counts such pages as text pages.

Steps:

- [ ] **Write the failing tests:**
  - `test_render_page_gives_a_png_at_300_dpi`: a 6×9 in page gives a PNG 1800 px wide, within 2 px.
  - `test_tesseract_reads_a_scanned_page`:
    - input: `scan_pdf` of `<p>The quick brown fox jumps over the lazy dog.</p>`;
    - expected: the paragraph text contains `"quick brown fox"`;
    - mark the test `integration`, skipped when `shutil.which("tesseract")` is None.
  - `test_tesseract_missing_is_reported`: when `shutil.which` is monkeypatched to return None, `TesseractMissing` is raised.
  - `test_a_picture_only_page_becomes_an_image_block`: a page holding only a generated PNG gives `source == "image"` and one `image` block. Mark it `integration`.
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS, with the integration tests skipped if Tesseract is absent. Before running them, ask the owner whether to install Tesseract locally with `brew install tesseract tesseract-lang`.
- [ ] **Commit:** `feat: render PDF pages and read them with Tesseract`.

### Task 3: An image input for models, and AI page OCR

**Files:**
- Modify: `jerome/models/base.py`, `jerome/models/claude.py`, `jerome/models/openai_compat.py`, `jerome/models/fake.py`, `jerome/config.py` (an `ocr` default pass), the Settings → Models pass list (`jerome/core/settings*.py` and the web `ModelsSection`)
- Create: `jerome/pdfimport/ocr_ai.py`
- Test: `tests/models/test_images.py`, `tests/pdfimport/test_ocr_ai.py`

**Interfaces:**
- `Provider.complete(..., images: Sequence[tuple[bytes, str]] = ())` takes (data, media type) pairs.
  - Claude: the user content becomes a list of image blocks (base64) followed by the text block.
  - OpenAI-compatible: `image_url` parts with `data:` URLs.
  - Fake: records the images.
- `count_tokens` is unchanged.
- The `ocr` pass default is the same as the `ai` pass: `{"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}`.
- `ai_page(provider: Provider, cfg: PassConfig, png: bytes, page: int, language: str) -> tuple[PageResult, Usage]`, with source `"ai"`.
  - The schema is `{"blocks": [{"kind": "heading"|"paragraph"|"footnote"|"image", "text": str, "level": int, "note": int|null, "box": [x0,y0,x1,y1]|null}]}`.
  - In `text`, `*italic*`, `**bold**` and `[^n]` are parsed into runs.
  - An `image` block's `box` (fractions of the page) is cropped from the PNG with Pillow.
- `estimate_ocr(registry: Registry, pages: int) -> Decimal` returns `pages × (1600 input + 900 output tokens)` at the ocr model's price. It is `Decimal(0)` when there is no price.

Steps:

- [ ] **Write the failing tests:**
  - `test_claude_sends_images_before_the_text`, with respx: the request body's user content is `[{"type":"image",...},{"type":"text",...}]`.
  - `test_openai_compat_sends_image_url_parts`.
  - `test_ai_page_parses_runs_and_crops_images`:
    - setup: the fake provider returns a heading, a paragraph `"The *ship* came[^1] in."`, a footnote with `note` 1, and an image with box `[0,0.5,1,1]`;
    - expected: italic runs, the `note` set, and the image's PNG height about half the page's.
  - `test_estimate_ocr_uses_the_ocr_price`.
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS. Also run `uv run pytest tests/models -q` to see that nothing else broke.
- [ ] **Commit:** `feat: models take images, and the ocr pass reads a page`.

### Task 4: Assembling pages into a book

**Files:**
- Create: `jerome/pdfimport/assemble.py`
- Test: `tests/pdfimport/test_assemble.py`

**Interfaces:**
- `assemble(pages: list[PageResult]) -> list[PageBlock]`, in reading order. It does the following:
  - **Drops** a block in the top or bottom 8% of its page when:
    - its text with digits removed repeats in the same band on at least half the pages (minimum 3 pages); or
    - it is a lone number or roman numeral.
  - **Joins** a paragraph that ends a page without `. ! ? : » “ ”` and is followed by a paragraph starting lowercase on the next page.
  - **Rejoins** `word-` + `next` across a line or page break when both sides are lowercase letters. `Nord-` + `Süd` keeps its hyphen.
  - **Renumbers footnotes** across the book: `[^n]` on page p matches footnote n on page p. A footnote with no matching reference becomes a paragraph at the end of its page.
- `report(pages: list[PageResult]) -> dict` returns `{"pages", "text_pages", "ocr_pages", "engine_pages": {"ai","tesseract"}, "image_pages", "failed_pages": [int]}`.

Steps:

- [ ] **Write the failing tests**, using hand-built `PageResult`s, with no PDFs:
  - `test_running_header_with_page_number_is_dropped`: "Chapter One · 12", then "· 13" and "· 14", at top 0.03.
  - `test_lone_page_numbers_are_dropped`
  - `test_paragraph_continues_across_pages`
  - `test_hyphen_joined_only_between_lowercase`
  - `test_footnotes_match_their_references_and_strays_stay_paragraphs`
  - `test_report_counts_and_lists_failed_pages`
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS.
- [ ] **Commit:** `feat: assemble PDF pages into clean blocks`.

### Task 5: Writing the DOCX

**Files:**
- Create: `jerome/pdfimport/docx_build.py`
- Test: `tests/pdfimport/test_docx_build.py`

**Interfaces:**
- `build_docx(blocks: list[PageBlock], *, title: str, language: str) -> bytes`. It writes minimal WordprocessingML parts with lxml and zipfile, in the same shape as `tests/fixtures/docx_builders.make_docx`:
  - headings use the `Heading1` and `Heading2` styles;
  - runs carry `w:i` and `w:b`;
  - footnotes go in `word/footnotes.xml` with references;
  - images are embedded PNG or JPEG in `word/media/`;
  - `docProps/core.xml` carries the title.

Steps:

- [ ] **Write the failing tests:**
  - `test_round_trip_through_read_docx`: `read_docx(build_docx(...))` gives:
    - one chapter per Heading 1, with the heading text as the title;
    - paragraph text equal to the joined runs;
    - an italic mark on "ship";
    - the footnote text as a note block.
  - `test_images_survive_to_the_publish_model`: `publish/from_docx` finds one image of type `image/png`.
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS.
- [ ] **Commit:** `feat: write assembled PDF blocks as a DOCX`.

### Task 6: Book import state, the import job's shape, and guards

**Files:**
- Create: `migrations/versions/0013_pdf_import.py`
- Modify: `jerome/orm.py`, `jerome/core/books.py`, `jerome/core/translation.py`, `jerome/jobs/service.py`, `jerome/core/projects.py`, `jerome/storage.py`, `jerome/config.py`
- Test: `tests/core/test_pdf_import_start.py`, `tests/core/test_projects.py`

**Interfaces:**
- **Migration 0013:**
  - adds `books.import_status` (String, null, check in `importing`/`failed`), `books.import_error` (Text) and `books.import_report` (JSONB);
  - adds `job_tasks.pages` (JSONB, `[first, last]`);
  - `TASK_KINDS` gains `import_scan`, `import_pages` and `import_build`, and the check constraint is recreated.
- `Storage.save_source_pdf(book_id, data)`, `read_source_pdf(book_id)`, `save_page(book_id, page, d: dict)`, `read_page(book_id, page) -> dict | None`.
- `Settings.max_pdf_upload_mb: int = 200`.
- **`books.start_pdf_import(...) -> Book`**
  - Signature: `session, storage, *, filename, data, variant, title, local_only, auto_apply, engine: Literal["ai","tesseract"], reocr: bool, work: Work, target_variant: Variant | None, translated_title: str | None, actor: str`.
  - It calls `count_pages` (an `UnsupportedDocumentError` gives the existing 415/422 path).
  - It creates the visible Book with `format="docx"`, `original_filename="<stem>.docx"`, `import_status="importing"`, and `work`/`translated_title` as given. For a translation it checks the language pair at once, as `import_for_translation` does.
  - It saves `source.pdf`.
  - It creates a Job with `type="import"`, `passes=[engine]`, `max_cost=Decimal(0)` and `stats={"pages": n, "pages_done": 0, "reocr": reocr, "variant": variant, "target_variant": target_variant}`, plus one task `import_scan`.
- `books.populate_from_docx(session, storage, book: Book, data: bytes, variant: Variant) -> None` is the body of `import_book` after the Book row, refactored out. `import_book` calls it.
- `translation.make_translation(session, storage, *, source: Book, book: Book, target_variant: Variant) -> None` is the body of `import_for_translation` after the source import, refactored out to fill an existing translated Book.
- `ensure_imported(book: Book) -> None` raises `Conflict("the book is still importing")` or `Conflict("the book's import failed: …")`. It is called by `create_job`, `create_translation_job`, `create_publish_job`, the review chapter route and `estimate_job`.
- `derive_stage(..., import_status: str | None = None)`: `importing` gives the new stage `"importing"`, and `failed` gives `"needs_attention"`. The `Stage` literal gains `"importing"`.

Steps:

- [ ] **Write the failing tests:**
  - `test_start_pdf_import_makes_an_importing_book_and_a_scan_task`
  - `test_local_only_refuses_ai`: `ValueError`, which becomes 422 in Task 8.
  - `test_jobs_review_and_publish_refuse_an_importing_book`: each raises Conflict with "still importing".
  - `test_stage_is_importing_then_needs_attention_on_failure`
  - `test_import_book_still_works_for_docx`: existing tests cover this. Run `tests/core/test_books.py`.
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.** Run `uv run alembic upgrade head` against the test DB through the test fixtures.
- [ ] **Run the tests again.** Expected: PASS, with `tests/core` green.
- [ ] **Commit:** `feat: books can be importing, and an import job has its own tasks`.

### Task 7: The worker runs an import

**Files:**
- Create: `jerome/pdfimport/job.py`
- Modify: `jerome/jobs/worker.py` (dispatch on `import_*` kinds), `jerome/jobs/service.py`
- Test: `tests/pdfimport/test_import_job.py`

**Interfaces:**
- **`import_scan`:**
  - classifies the pages in `run_isolated`;
  - writes `job.stats["kinds"]` (a list of kinds) and `stats["ocr_pages"]` (scan pages, plus scan_with_layer pages when `reocr`);
  - creates `import_pages` tasks of 25 pages each.
  - With engine `ai` and `ocr_pages > 0`, it also sets `job.estimate = {"ocr_pages": n, "cost": str(estimate_ocr(...))}` and `stats["awaiting_estimate"] = True`.
  - Text pages are never held back by the cap.
- **`import_pages`:** for each page in its range that has no saved result:
  - **text** (and `scan_with_layer` when not `reocr`): `read_text_page`;
  - **scan** with engine `tesseract`: render, then `tesseract_page`;
  - **scan** with engine `ai`: first the existing `_precheck`, which pauses at `paused_cost_cap` while `cost_actual >= max_cost`; then render and `ai_page`. The usage row has pass name `"ocr"`. On `BilledError`, `ModelRefusal`, an empty result or the cost cap reached mid-task, the page falls back to `tesseract_page`;
  - **image:** an image block of the rendered page;
  - **blank:** no blocks;
  - any `PageReadError`, or Tesseract failing after the AI did: `PageResult(source="failed", error=…)`.
  - It saves each page with `storage.save_page` and bumps `stats["pages_done"]`.
  - When the last `import_pages` task is done, it creates `import_build`.
- **`import_build`:**
  - loads all pages, then `assemble`, `build_docx` and `storage.save_original`;
  - runs `populate_from_docx` on the visible book, or for a translation creates the hidden source Book and calls `populate_from_docx` on it, then `make_translation`;
  - sets `import_status=None` and `import_report=report(pages)`;
  - completes the job.
  - On an exception after 3 attempts, it sets `import_status="failed"` and `import_error`.
- `_record_failure` uses the pass name `"ocr"` for jobs of type `import`, so billed calls of a failed task are priced and recorded as OCR.
- **`service.confirm_import(session, book_id, max_cost: Decimal) -> Job`** clears `awaiting_estimate` and calls `resume_job(max_cost=max_cost)`.
- **`service.use_tesseract(session, book_id) -> Job`** sets `passes=["tesseract"]`, clears the flag and resumes.

Steps:

- [ ] **Write the failing tests.** Run the worker loop with `run_task` until no task is left, as `tests/jobs/test_worker.py` does, with the fake provider.
  - `test_text_pdf_imports_without_ocr`: the book becomes ready, the chapters come from the headings, and there is no usage row.
  - `test_ai_import_waits_for_the_estimate_then_reads`: before confirm, the job is `paused_cost_cap`, `awaiting_estimate` is true and the provider has no calls; after `confirm_import(..., Decimal("5"))`, the provider gets one call per scan page and the usage rows have pass `"ocr"`.
  - `test_use_tesseract_instead_spends_nothing`. Mark it `integration` (it needs Tesseract), or monkeypatch `tesseract_page`.
  - `test_ai_failure_falls_back_to_tesseract`, with a monkeypatched `tesseract_page`.
  - `test_reocr_off_uses_the_existing_layer`
  - `test_failed_page_is_reported_and_the_book_is_ready`, with a monkeypatched `read_text_page` that raises `PageReadError` on page 2.
  - `test_translate_from_pdf_makes_source_and_translation`
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS, and `tests/jobs` stays green.
- [ ] **Commit:** `feat: the worker imports a PDF in scan, page and build tasks`.

### Task 8: API, CLI, MCP, README

**Files:**
- Modify: `jerome/api/routes_books.py`, `jerome/api/schemas.py` (`BookOut` gains `import_status`, `import_error`, `import_report`), `jerome/cli.py`, `jerome/mcp/tools_files.py`, `jerome/core/books.py` (`_detect_format` accepts `.pdf` starting with `%PDF-`), `README.md`
- Test: `tests/api/test_pdf_import_api.py`, `tests/mcp/test_tools_files.py`, `tests/test_cli.py`

**Interfaces:**
- **`POST /v1/books`:**
  - It gains `ocr_engine: Literal["ai","tesseract"] | None = None` (None means the default from the Global Constraints) and `reocr: bool = True`.
  - A `.pdf` is read with `max_pdf_upload_mb` and goes to `start_pdf_import`. The response is 201 with `import_status: "importing"`.
- `POST /v1/books/{id}/import/confirm` with `{max_cost}` returns `JobOut`. `POST /v1/books/{id}/import/use-tesseract` returns `JobOut`.
- **CLI:** `jerome import book.pdf --variant en-GB --ocr tesseract|ai` prints the book id and "importing in the background (job N)".
- **MCP:** `import_book` refuses `.pdf` with the copy from the Global Constraints.

Steps:

- [ ] **Write the failing tests:**
  - `test_pdf_upload_starts_an_import`
  - `test_pdf_over_its_limit_is_413`
  - `test_ocr_ai_refused_for_local_only_is_422`
  - `test_confirm_and_use_tesseract_routes`
  - `test_mcp_refuses_pdf`
  - `test_cli_imports_a_pdf`
- [ ] **Run them.** Expected: FAIL.
- [ ] **Implement.** Then add a README section "PDF import" covering the engines, the estimate, `reocr`, the limits and the report.
- [ ] **Run the tests again.** Expected: PASS, and `tests/api tests/mcp` stay green.
- [ ] **Commit:** `feat: upload a PDF over REST and the CLI`.

### Task 9: The web pages

**Files:**
- Modify: `web/src/pages/NewProjectPage.tsx`, `web/src/pages/ProjectPage.tsx`, `web/src/pages/ProjectsPage.tsx`, and `web/src/lib/jobs.ts` (types)
- Create: `web/src/components/project/ImportPanel.tsx`
- Test: `NewProjectPage.test.tsx`, `ImportPanel.test.tsx`, `ProjectsPage.test.tsx`

**Interfaces:**
- **New project:**
  - the file input accepts `.pdf`;
  - when a `.pdf` is picked, the form shows "OCR for scanned pages" (AI model / Tesseract; AI is disabled with "Local only books use Tesseract" when local only) and the "Re-OCR scanned pages" checkbox, checked;
  - it posts `ocr_engine` and `reocr`.
- **`ImportPanel`**, shown on the project page while `import_status` is set or the report has failed pages:
  - progress "Importing… page {done} of {pages}";
  - the estimate banner with a max-cost field prefilled with the estimate, plus Confirm and "Use Tesseract instead";
  - the failed state "Import failed: {error}", with Retry (the existing resume route);
  - after the import, the warning "{n} pages could not be read: {list}".
- **Projects list:** stage `importing` reads "Importing".

Steps:

- [ ] **Write the failing tests:**
  - the PDF options appear only for `.pdf`;
  - the posted form has `ocr_engine` and `reocr`;
  - the banner posts confirm with `max_cost`;
  - "Use Tesseract instead" posts;
  - the progress copy;
  - the failed pages warning;
  - the Importing stage label.
- [ ] **Run them:** `npx vitest run`. Expected: FAIL.
- [ ] **Implement.**
- [ ] **Run them again.** Expected: PASS, and `npx tsc --noEmit -p tsconfig.app.json` is clean.
- [ ] **Commit:** `feat: PDF import on the New project and project pages`.
