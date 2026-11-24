# Finished PDF and EPUB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every book, whether Word-born or EPUB-born, proofread or translated, gets a finished
EPUB and a finished digital PDF. A worker job makes them from the clean export and the
book's publication details.

**Architecture:**
- A new package `jerome/publish/` reads the clean export (DOCX through mammoth, EPUB
  through Jerome's own package reader) into one shared form, `PubBook`. It checks that
  form's text against Jerome's reader of the same clean file. From it, it writes either an
  EPUB 3 (DOCX-born books) or a PDF (WeasyPrint, in a child process).
- An EPUB-born book's finished EPUB is its `epub-clean` file, with only the OPF metadata and
  the cover patched.
- A `publish` job runs that work in the worker. It saves the file under the book's exports
  and serves it from `GET /v1/jobs/{id}/file`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, lxml, mammoth, WeasyPrint,
pypdf (dev), pytest/respx, React 19 + react-query + vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-finished-pdf-epub-design.md`

## Global Constraints

- The repository is public. Tests use only `tests/fixtures/public/` (pg932.epub, pg22367.epub)
  and the DOCX/EPUB builders. Never commit book files, generated EPUB/PDF files,
  `eval/data` or `eval/results`. Do not open translated books on this machine.
- Both files contain exactly the reviewed text of the clean export. A text difference is a
  hard stop with no override.
- Nothing is fetched over the network while rendering.
- Fonts are SIL OFL, shipped in the repository or image, and never downloaded at runtime.
- Formats are named `epub-book` and `pdf-book`. Trim sizes are `6x9in` (the default),
  `5.5x8.5in`, `5x8in` and `a5`.
- Export is refused for a source book, and for a translated book while paragraphs are
  pending or flagged. A proofread book is never refused for open suggestions; it gets a
  warning instead.
- The identifier is `urn:isbn:<isbn_epub>` when set. Otherwise an EPUB-born proofread book
  keeps its own `dc:identifier`, and every other book gets `urn:uuid:<uuid5(NAMESPACE_URL,
  "jerome-book-<id>")>`. A translated book never carries the source's identifier.
- Limits: the job stops after 900 s; the render's memory is capped at 2 GiB; more than
  200 MB of images is refused.
- Migration `0009`, `down_revision = "0008"`, in the style of `0008_translation.py`.
- Never downgrade the product model `claude-opus-5-5`. No secrets in the repository.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Checks:
  - backend: `JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test uv run pytest -q`;
  - lint: `uv run ruff check . && uv run ruff format --check .`;
  - web: `cd web && npm test && npm run build`.

## Review Focus

1. **Markup characters in titles and details.** `&`, `<`, German `ß`/umlauts and „“ quotes in
   the title, author or chapter titles must come out escaped and intact in the OPF, nav,
   XHTML and PDF metadata. Pinned in Tasks 6 and 8.
2. **A heading with no text** (an image-only or empty Heading 1) still gives a valid EPUB nav
   and PDF contents, labelled `Chapter N`. Pinned in Tasks 4 and 6.
3. **A bad cover upload** (a GIF, a corrupt PNG, a 1000 px image) gets a 422 that says why,
   and the old cover is kept. Pinned in Task 3.
4. **Publishing twice, or while another job runs.** The second publish job gives the
   existing 409 "already active" while one is running. Two finished runs keep two files,
   because each file name carries its job id. Pinned in Task 9.
5. **A translated book.** It publishes with its translated title and its target language,
   and is refused before the job starts while paragraphs are pending. Pinned in Task 9.

---

### Task 1: Dependencies, fonts, Docker, test gate

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `Dockerfile`
- Create: `jerome/publish/__init__.py`, `jerome/publish/fonts/` (EBGaramond-Regular.ttf,
  -Italic.ttf, -Bold.ttf, -BoldItalic.ttf, OFL.txt), `tests/publish/__init__.py`,
  `tests/publish/conftest.py`

**Interfaces:**
- Produces: the fixture `weasy` in `tests/publish/conftest.py`. It returns the `weasyprint`
  module, or skips the test with "WeasyPrint needs Pango" when the import raises
  `OSError`/`ImportError`.
- Produces: `FONT_DIR: Path` in `jerome/publish/__init__.py`.

- [ ] **Step 1:** Add the dependencies `weasyprint>=68`, `mammoth>=1.11`, and `pypdf>=6` to
  the dev group. Run `uv lock`.
- [ ] **Step 2:** Get the four static EB Garamond TTFs and `OFL.txt` from the font's upstream
  repository (octaviopardo/EBGaramond12, `fonts/ttf/`). Check the licence text is SIL OFL
  1.1, and put the files in `jerome/publish/fonts/`. Check that
  `[tool.hatch.build.targets.wheel]` packages the `.ttf` files (the package is `jerome`).
- [ ] **Step 3:** In the Dockerfile's runtime stage, before `USER jerome`, add
  `apt-get install --no-install-recommends -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig`
  and clean the apt lists. Do not install `epubcheck` in production.
- [ ] **Step 4:** Write `tests/publish/conftest.py` with `weasy`. Add
  `test_weasyprint_renders_a_page(weasy)`: render `<p>x</p>` and assert the bytes start with
  `b"%PDF-"`.
- [ ] **Step 5:** Run `uv run pytest tests/publish -q`. Expect a PASS, or a SKIP when Pango is
  missing locally. Run `docker build .`. Expect success, then run the same import inside the
  image: `docker run --rm <image> python -c "import weasyprint"`.
- [ ] **Step 6:** Commit with the message `build: WeasyPrint, mammoth and EB Garamond for finished files`.

### Task 2: Migration 0009, ORM and storage

**Files:**
- Create: `migrations/versions/0009_publication.py`
- Modify: `jerome/orm.py` (Book, JobTask), `jerome/storage.py`
- Test: `tests/test_storage.py` (or the existing storage test file), `tests/test_migrations.py` (if one exists; otherwise the ORM round trip in Task 3 covers it)

**Interfaces:**
- Produces, on `Book`:
  - `authors: list[str]` (JSONB, server default `'[]'`);
  - `translator`, `publisher`, `isbn_epub`, `isbn_pdf`, `rights`, `description`,
    `cover_type`: `str | None`. `cover_type` is `"image/jpeg"`, `"image/png"` or None;
  - `year: int | None`;
  - `trim_size: str`, server default `'6x9in'`, constrained by `ck_books_trim_size`.
- Produces: `ck_job_tasks_kind`, now `kind IN ('chapter','consistency','publish')`, in
  migration and ORM.
- Produces, on `Storage`:
  - `save_cover(book_id, data: bytes, ext: str) -> Path` writes `cover.<ext>` and removes
    any other `cover.*`;
  - `read_cover(book_id) -> bytes | None`;
  - `delete_cover(book_id) -> None`;
  - `read_export(book_id, name: str) -> bytes`. It raises `NotFound` and refuses a name
    containing `/`.

- [ ] **Step 1:** Write the failing storage tests:
  - `test_cover_round_trip`: save a PNG, then save a JPEG. Only `cover.jpg` remains and
    `read_cover` returns it. After `delete_cover`, it returns None.
  - `test_read_export_refuses_paths`: `read_export(1, "../x")` raises.
- [ ] **Step 2:** Run them. Expect a FAIL: no such methods.
- [ ] **Step 3:** Implement the storage methods with `_write_atomic`. Write the migration:
  - `upgrade` adds the columns above and the trim-size check, and drops and recreates
    `ck_job_tasks_kind`.
  - `downgrade` reverses it, with a docstring saying it fails while publish tasks exist.
  - Mirror both in `orm.py`.
- [ ] **Step 4:** Run the storage tests and the full backend suite (the test DB is created
  from migrations or metadata; both must agree). Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: publication columns, publish task kind, cover and export storage`.

### Task 3: Publication details — rules, import prefill, translation copy, API, MCP

**Files:**
- Create: `jerome/core/publication.py`, `jerome/api/routes_publication.py`,
  `jerome/mcp/tools_publication.py`
- Modify:
  - `jerome/documents/model.py` (`ParsedBook`);
  - `jerome/documents/epub_xml.py` and `jerome/documents/epub_reader.py` (creator,
    publisher and date);
  - `jerome/documents/docx_reader.py` (core creator);
  - `jerome/core/books.py` (`import_book`) and `jerome/core/translation.py`
    (`import_for_translation`);
  - `jerome/api/schemas.py` and `jerome/api/app.py`, plus the MCP registration file the other
    tools use.
- Test: `tests/core/test_publication.py`, `tests/api/test_publication_api.py`, `tests/mcp/test_tools_publication.py`

**Interfaces:**
- Consumes: Task 2 columns and `Storage.save_cover/read_cover/delete_cover`.
- Produces in `jerome/core/publication.py`:
  - `TRIM_SIZES = ("6x9in", "5.5x8.5in", "5x8in", "a5")`;
  - `@dataclass Publication`, with:
    - `title: str`, which is `translated_title or title`;
    - `language: str`, from the style-sheet variant, such as `"en-GB"`;
    - `authors: list[str]`;
    - `translator`, `publisher`, `isbn_epub`, `isbn_pdf`, `description`: `str | None`;
    - `year: int | None`;
    - `rights: str`, the stored value or the default `© {year} {authors joined by ", "}`,
      left out when there is no year;
    - `trim_size: str`;
    - `has_cover: bool`;
  - `get_publication(session, book_id) -> Publication`;
  - `update_publication(session, book_id, values: PublicationIn) -> Publication`;
  - `isbn13(value: str) -> str`. It returns the value with hyphens kept, or raises
    `InvalidPublication`: a `JeromeError` mapped to 422, with "ISBN … is not a valid
    ISBN-13";
  - `set_cover(session, storage, book_id, data: bytes) -> Publication`. It reads the PNG
    IHDR or JPEG SOF size with no new dependency, and refuses another type ("the cover must
    be a JPEG or PNG"), an unreadable image, or a long side under 1400 px ("the cover is N
    px on its long side; shops need at least 1400"). A refused upload leaves the stored
    cover as it was;
  - `delete_cover(session, storage, book_id) -> Publication`.
- Produces on `ParsedBook`: `authors: list[str] = []`, `publisher: str | None = None`,
  `year: int | None = None`.
- Produces in the API:
  - `GET /v1/books/{id}/publication` returns `PublicationOut`;
  - `PUT /v1/books/{id}/publication` takes `PublicationIn` (every field optional; `null`
    clears; `authors` is a list);
  - `PUT /v1/books/{id}/cover` takes the raw image body, with Content-Type
    `image/jpeg`/`image/png`, and a limit of 20 MB;
  - `GET /v1/books/{id}/cover` returns the image;
  - `DELETE /v1/books/{id}/cover`.
  - Access is the same as for the other `/v1/books/{id}` routes. A source book gives the
    existing Conflict, through `ensure_not_source`.
- Produces in MCP: `get_publication(book_id)`, `update_publication(book_id, **fields)`, and
  `set_cover(book_id, upload_id)`, which reuses the upload mechanism `import_book` uses.

- [ ] **Step 1:** Write the failing tests.
  - `test_isbn13`:
    - accepts `"978-3-16-148410-0"`, returned as given;
    - refuses `"978-3-16-148410-1"` (checksum) and `"12345"`.
  - `test_default_rights`: `© 2026 Ana Novak, Ben Roe`.
  - `test_import_prefills_from_metadata`. Use a `make_epub` OPF with `dc:creator`,
    `dc:publisher` and `dc:date` `2019-05-01`, and a `package_docx` with a core creator.
    Add those options to the builders: `creators=`, `publisher=` and `date=` on `make_epub`;
    `creator=` on `package_docx`. Expect `authors`, `publisher` and `year == 2019`.
  - `test_translation_copies_details_but_not_isbns`.
  - `test_cover_rules` (Review Focus 3):
    - a 1400×2100 PNG is stored;
    - a GIF, a truncated PNG and a 1000×1500 JPEG each give 422 with the message above;
    - after a refusal, `GET` still returns the earlier cover.
  - `test_publication_api_round_trip`: PUT then GET. Clearing with null works. A bad trim
    size gives 422. A source book gives 409.
  - `test_mcp_publication_tools`.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement the code. The EPUB reader takes the `dc:creator` elements in
  order and the first four digits of `dc:date`. `import_book` writes `authors`, `publisher`
  and `year` from `ParsedBook`. `import_for_translation` copies `authors`, `publisher`,
  `rights`, `description`, `year`, `trim_size` and the cover (`read_cover` → `save_cover`),
  never the ISBNs. Existing books are not backfilled.
- [ ] **Step 4:** Run the new tests and the full suite. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: publication details per book, with cover, prefill and API`.

### Task 4: Shared form, DOCX to HTML, text check

**Files:**
- Create: `jerome/publish/model.py`, `jerome/publish/from_docx.py`, `jerome/publish/textcheck.py`
- Modify: `tests/fixtures/docx_builders.py` (lists and images)
- Test: `tests/publish/test_from_docx.py`, `tests/publish/test_textcheck.py`

**Interfaces:**
- Produces in `jerome/publish/model.py`:
  - `@dataclass PubImage(name: str, media_type: str, data: bytes)`;
  - `@dataclass PubChapter(title: str | None, html: str, in_toc: bool = True)`. `html` is
    an XHTML body fragment. Headings are `<h1>`–`<h4>`; the chapter's own `<h1>` is kept in
    `html`. Images are `<img src="images/{name}" alt="…"/>`. **A footnote is
    `<span class="jr-note">…</span>` at its reference point.** A `title` of None, or one
    that is empty after `title_text`, means the nav/contents label is `Chapter {n}`, where n
    counts the chapters that are `in_toc`;
  - `@dataclass PubBook(language: str, chapters: list[PubChapter], images: dict[str, PubImage], left_out: list[str], warnings: list[str])`;
  - `class PublishError(JeromeError)`, the error that fails a publish job at once, and
    `class PublishTextError(PublishError)`.
- Produces in `jerome/publish/from_docx.py`: `docx_to_pub(clean_docx: bytes, language: str) -> PubBook`.
- Produces in `jerome/publish/textcheck.py`: `check_text(pub: PubBook, parsed: ParsedBook, *, documents: set[str] | None = None) -> None`.
  It raises `PublishTextError`, whose message is
  `"The finished file's text differs from the reviewed text near '{chapter}': '{ours}' / '{reviewed}'"`.
  The quoted parts are about 12 words around the first difference.

- [ ] **Step 1:** Extend `make_docx`:
  - list lines `- item` and `1. item`, with `numbering.xml`;
  - an image line `![alt](png)`, which embeds a small generated PNG;
  - a linked-image option for the dropped-image warning.
  
  Write the failing tests:
  - `test_docx_chapters_split_at_level_0`: `["# One", "a", "## Sub", "b", "# Two", "c"]`
    gives 2 chapters, titled One and Two, with `<h2>Sub</h2>` inside One.
  - `test_docx_custom_style_with_outline_level_is_a_heading`. Use `styles={"Kapitel": 0}`.
    Build mammoth's style map from the DOCX styles' outline levels: a style at level L maps
    to `h{L+1}`, and levels above 3 map to `h4`.
  - `test_docx_text_before_first_heading_is_untitled_front_content`: `in_toc=False`, kept,
    and placed first.
  - `test_docx_footnote_becomes_inline_note`. Use `"Text[^1] more"` with
    `footnotes={1: "Note *it*"}`. Expect `<span class="jr-note">Note <em>it</em></span>`
    right after "Text". Mammoth's notes list and back-links are gone.
  - `test_docx_lists_tables_images`: `ol`/`ul`, `table`, and one `PubImage` referenced by
    its `img`.
  - `test_docx_linked_image_dropped_with_warning`.
  - `test_docx_toc_field_left_out`. It is in `left_out` as `"Word table of contents"`.
  - `test_empty_heading_is_labelled_later` (Review Focus 2): the title is None and the
    chapter is still a chapter.
  - `test_textcheck_passes_on_real_conversion`.
  - `test_textcheck_fails_on_doctored_html`: replace one word and expect `PublishTextError`
    naming the chapter.
  - `test_textcheck_ignores_whitespace_soft_hyphens_and_numbers`.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement the code.
  - **The text check compares:**
    - body text: the non-footnote blocks of `parsed`, with PLACEHOLDER removed, against the
      PubBook text outside `jr-note` spans;
    - footnote text: the footnote blocks in order, against the `jr-note` spans in order.
    - Both sides go through `" ".join(text.replace("­", "").split())`. List numbers are
      `<ol>` markup, not text, so they never appear.
  - **`documents`** limits the EPUB comparison to the given spine paths (Task 5). It is
    unused for DOCX.
  - **Images:** take them from mammoth's image converter, named `image{n}.{ext}`. An image
    whose type is not JPEG, PNG, GIF or SVG becomes a warning and is dropped.
- [ ] **Step 4:** Run the tests. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: clean DOCX to the shared publishing form, with a text check`.

### Task 5: EPUB to the shared form

**Files:**
- Create: `jerome/publish/from_epub.py`
- Modify:
  - `jerome/documents/epub_xml.py`: parse the nav landmarks, the EPUB 2 `<guide>`, and the
    cover image item (the `cover-image` property, or the `<meta name="cover">` item);
  - `tests/fixtures/epub_builders.py`: options `cover=bytes|None`, `landmarks=bool`,
    `notes=bool` (noteref plus an aside), `images=bool`.
- Test: `tests/publish/test_from_epub.py`

**Interfaces:**
- Consumes: Task 4 model and `check_text`.
- Produces:
  - `EpubPackage.landmarks: dict[str, str]`, mapping `epub:type` to its document path;
  - `EpubPackage.cover_item: str | None`, a manifest id;
  - `epub_to_pub(clean_epub: bytes, language: str) -> tuple[PubBook, set[str]]`. The set
    holds the spine paths `read_epub` takes blocks from, which `check_text(documents=…)`
    uses.

- [ ] **Step 1:** Write the failing tests:
  - `test_epub_chapters_follow_nav`: chapters and titles are the nav's top-level entries,
    with split documents joined as `epub_reader` does.
  - `test_epub_front_pages_left_out`: cover, title page, copyright page and the toc
    document, found by landmarks, `epub:type` on `body`/`section`, or the guide. All are
    named in `left_out` (for example `"cover (cover.xhtml)"`). A dedication stays, with
    `in_toc=False`.
  - `test_epub_noteref_becomes_inline_note`: the aside's content moves into a `jr-note` at
    the reference, and the aside is gone.
  - `test_epub_book_css_not_used`: no `<link>` or `style` attributes survive. Semantic tags,
    `img`, `table` and `blockquote` do.
  - `test_epub_images_collected`: referenced images go into `images`, with paths rewritten
    to `images/{name}`.
  - `test_public_fixtures_convert_and_pass_textcheck`: for `pg932.epub` and `pg22367.epub`,
    run `write_epub(original, {})`, then `epub_to_pub`, then `check_text`. Expect no
    exception.
  - `test_remote_resources_never_fetched`: an `<img src="https://…">` gives a warning, and
    respx sees no call.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement with `read_container`, `content_documents` and
  `parse_content_document`. Strip `class`, `style`, `id` and `epub:type` attributes except
  the ones Jerome adds.
- [ ] **Step 4:** Run the tests. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: clean EPUB to the shared publishing form`.

### Task 6: EPUB 3 writer for Word-born books

**Files:**
- Create: `jerome/publish/epub_book.py`, `jerome/publish/ebook.css`, `tests/publish/epubcheck.py`
- Test: `tests/publish/test_epub_book.py`

**Interfaces:**
- Consumes: `PubBook`, `Publication`, the identifier rule in Global Constraints, and the cover
  bytes.
- Produces:
  - `book_identifier(book_id: int, pub: Publication, existing: str | None = None) -> str`,
    shared with Task 7;
  - `write_epub_book(pub_book: PubBook, pub: Publication, *, identifier: str, cover: tuple[bytes, str] | None, now: datetime) -> bytes`;
  - `run_epubcheck(data: bytes) -> list[str]`, the error messages, in
    `tests/publish/epubcheck.py`. It skips the test when `epubcheck` is not on PATH.

- [ ] **Step 1:** Write the failing tests:
  - `test_epub_book_structure`:
    - `mimetype` is first and stored;
    - the OPF holds dc:title, creator(s) in order, a translator as `dc:contributor` with
      role `trl`, language, identifier, publisher, `dc:date` (the year), description,
      rights and `dcterms:modified`;
    - `nav.xhtml` and `toc.ncx` list only the `in_toc` chapters, with `Chapter N` for
      untitled ones;
    - the order is cover page (when a cover is given), title page, copyright page, then the
      chapters.
  - `test_notes_are_epub3_footnotes`: a `jr-note` becomes
    `<a epub:type="noteref" href="#note-1">1</a>`, plus an
    `<aside epub:type="footnote" id="note-1">` at the end of the chapter.
  - `test_escaping` (Review Focus 1): title `"Tom & Jerry <Teil 2> – „Straße“"` and author
    `"Ö & Ä"`. They parse back intact from the OPF, nav and title page.
  - `test_epubcheck_clean` on a builder DOCX with headings, a list, a table, a footnote and
    an image. `run_epubcheck` returns `[]`.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement it. Build XML with lxml, never with string concatenation. Use one
  XHTML file per chapter (`chapter-{n:03}.xhtml`). The cover image is listed with
  `properties="cover-image"`. Write `ebook.css` with no fonts.
- [ ] **Step 4:** Run the tests. Expect a PASS; EPUBCheck may skip locally.
- [ ] **Step 5:** Commit with the message `feat: EPUB 3 writer for Word-born books`.

### Task 7: Finished EPUB for EPUB-born books

**Files:**
- Create: `jerome/publish/epub_patch.py`
- Test: `tests/publish/test_epub_patch.py`

**Interfaces:**
- Consumes: Task 5 `EpubPackage.cover_item`, `write_package`, and Task 6 `book_identifier`.
- Produces: `patch_epub(clean_epub: bytes, pub: Publication, *, identifier: str, cover: tuple[bytes, str] | None) -> bytes`.

- [ ] **Step 1:** Write the failing tests:
  - `test_patch_sets_metadata_and_keeps_other_entries`:
    - the OPF identifier, creators, translator, publisher, date, description and rights are
      set;
    - every other zip entry is byte-identical (`unchanged_entries`);
    - a field that is None leaves the book's own element as it was.
  - `test_patch_replaces_or_adds_cover`: a book with a cover item gets its image bytes
    replaced, under the same path and media type when the type matches, else a new item. A
    book without one gets a new item with `cover-image`.
  - `test_patch_identifier_rules`: the ISBN wins. A proofread book keeps its own
    identifier. A translated book gets `urn:uuid` (the caller passes it).
  - `test_patch_adds_no_epubcheck_errors`: on `pg932.epub`, the errors after patching are a
    subset of the errors before.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement it. Serialize the OPF the way `epub_translate._set_metadata`
  does. Keep the package's unique-identifier element id.
- [ ] **Step 4:** Run the tests. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: finished EPUB for EPUB-born books patches metadata and cover`.

### Task 8: PDF

**Files:**
- Create: `jerome/publish/pdf.py`, `jerome/publish/print.css`
- Test: `tests/publish/test_pdf.py`

**Interfaces:**
- Consumes: `PubBook`, `Publication`, cover bytes, `FONT_DIR`.
- Produces:
  - `pdf_html(pub_book: PubBook, pub: Publication, cover: tuple[bytes, str] | None) -> str`.
    It returns the whole document: cover page (when given), title page, copyright page
    (rights, publisher, `ISBN {isbn_pdf}`, the year, a translator line), the contents with
    `target-counter(attr(href), page)`, then the chapters. Each `jr-note` gets class
    `footnote` and the CSS `float: footnote`.
  - `render_pdf(pub_book: PubBook, pub: Publication, cover: tuple[bytes, str] | None, *, timeout: float = 900, memory: int = 2 * 1024**3) -> bytes`.
    - It writes the HTML, images and fonts into a `TemporaryDirectory`, then renders in a
      `multiprocessing` `spawn` child.
    - The child sets `resource.RLIMIT_AS` to `memory` on Linux and ignores the limit on
      other systems.
    - Its URL fetcher allows only `file://` paths inside that directory and raises for any
      other URL.
    - A timeout kills the child and raises
      `PublishError("Rendering the PDF took longer than 15 minutes")`.
    - Any child failure raises `PublishError` with the last error line.

**`print.css` values:**
- `@page` size from the trim size: `6in 9in`, `5.5in 8.5in`, `5in 8in` or `148mm 210mm`.
- Margins: about 0.75in top and bottom, 0.65in sides.
- Running heads: `string-set` gives the book title on left pages and the chapter title on
  right pages, with none on a chapter's first page (`@page :first`, or a named page per
  chapter start).
- Page number at the bottom centre.
- Body text:
  - `@font-face` for EB Garamond's four styles, from `FONT_DIR`;
  - `font-size: 11pt; line-height: 1.35; text-align: justify; hyphens: auto; widows: 2; orphans: 2`;
  - `html` carries `lang` = `pub.language`.
- Each chapter: `break-before: page`.
- `img { max-width: 100%; max-height: 100% }` within the text block.
- Bookmarks: `bookmark-level` on the chapter `h1` only.
- PDF metadata comes from `<title>`, `<meta name="author">` and `lang`.

- [ ] **Step 1:** Write the failing tests (all use the `weasy` fixture and pypdf):
  - `test_page_size_per_trim`: 6x9in is 432×648 pt; a5 is about 419.5×595.3 pt.
  - `test_text_order_and_front_pages`: extracted text has the title page, then the
    copyright page with `ISBN 978-…`, then the contents with chapter titles and page
    numbers, then the chapter text in order.
  - `test_fonts_embedded`: every font has a FontFile2 or FontFile3 and its name contains
    `EBGaramond`.
  - `test_bookmarks_and_metadata`: the outline titles equal the `in_toc` chapter titles;
    `/Title` and `/Author` are set; Review Focus 1 characters come through intact.
  - `test_footnote_on_reference_page`: the page holding "Text" also holds "Note it".
  - `test_german_hyphenation`: a narrow trim with `de-DE` and long compounds gives at least
    one line ending in `-` that the source does not have.
  - `test_url_fetcher_refuses_outside_files`: HTML with `file:///etc/hosts` and
    `https://example.com/x.png` renders, the fetcher records both as refused, and respx sees
    no call.
  - `test_timeout_raises`: `timeout=0.001` raises `PublishError` with "15 minutes".
- [ ] **Step 2:** Run them. Expect a FAIL, or a SKIP without Pango.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the tests. Expect a PASS where WeasyPrint loads.
- [ ] **Step 5:** Commit with the message `feat: finished PDF through WeasyPrint`.

### Task 9: The publish job, API, CLI and MCP

**Files:**
- Create: `jerome/publish/build.py`, `jerome/api/routes_publish.py`, `jerome/mcp/tools_publish.py`
- Modify:
  - `jerome/jobs/service.py`;
  - `jerome/jobs/worker.py`: `WorkerDeps`, `_start`, `run_task`, plus a new `_run_publish`;
  - `jerome/core/translation.py`: factor out `untranslated_conflict`;
  - the `WorkerDeps(...)` call sites in `jerome/cli.py:98`, `jerome/api/deps.py:64` and
    `jerome/mcp/context.py:104`;
  - `jerome/cli.py` and `jerome/api/app.py`;
  - `jerome/core/projects.py`: leave `publish` jobs out of the proofreading counts;
  - the MCP registration file.
- Test: `tests/publish/test_build.py`, `tests/jobs/test_publish_job.py`, `tests/api/test_publish_api.py`, `tests/mcp/test_tools_publish.py`

**Interfaces:**
- Consumes: everything above, plus `export_book(..., "docx-clean"/"epub-clean")` for the
  clean bytes, `read_docx`/`read_epub` for `check_text`, and
  `transfer_links.create_download`.
- Produces in `jerome/publish/build.py`:
  - `PublishFormat = Literal["epub-book", "pdf-book"]`;
  - `@dataclass Prepared(fmt, book_id, filename_stem, pub: Publication, pub_book: PubBook | None, clean: bytes, identifier: str, cover: tuple[bytes, str] | None, warnings: list[str])`;
  - `prepare(session, storage, book_id: int, fmt: PublishFormat) -> Prepared`. It uses the
    database and is fast:
    - it runs the guards, makes the clean export, converts, runs `check_text` and the
      200 MB image check (`PublishError("The book has N MB of images; the limit is 200 MB")`);
    - its warnings are: no cover; left-out parts; open suggestions or unproofread chapters
      on a proofread book (`"{n} suggestions are still open"`,
      `"{n} chapters are not proofread"`); dropped images;
  - `render(prepared: Prepared) -> tuple[bytes, str]`, the data and the media type. It has
    no database access and may be slow:
    - `epub-book` on a DOCX book: `write_epub_book`;
    - `epub-book` on an EPUB book: `patch_epub`;
    - `pdf-book`: `render_pdf`.
    - After an EPUB is made, when `shutil.which("epubcheck")` finds it, its errors are added
      to the warnings as `"EPUBCheck: …"`. Production does not install it, so there this is
      a no-op.
- Produces in `jerome/jobs/service.py`: `create_publish_job(session, book_id, *, fmt: PublishFormat, callback_url: str | None, actor: str) -> Job`.
  - It creates `Job(type="publish", passes=[fmt], max_cost=Decimal(0), estimate=None)` with
    one `JobTask(kind="publish")`.
  - Guards: `ensure_not_source`; `untranslated_conflict`; no authors gives
    `Conflict("Set the author under Publication first")`; and the existing "already active"
    rule through `_add`.
- Produces in the worker:
  - `WorkerDeps.storage: Storage | None = None`.
  - `_run_publish`:
    - It runs `prepare` in a short session, `render` with no session open, then a session
      that locks job and task and saves the file as `{stem}-{job_id}.{epub|pdf}`.
    - It sets `job.stats = {"file": name, "warnings": [...]}` and calls `_complete_task`.
    - It skips `_precheck`'s cost cap.
    - A `PublishError` fails the task and job at once, with the message in `task.error`.
      Other exceptions go to `_record_failure`.
- Produces in the API:
  - `POST /v1/books/{id}/publish` with `{"format": PublishFormat, "callback_url"?}` returns
    201 and `_job_json`;
  - `GET /v1/jobs/{id}/file` returns the file with `content_disposition`. It answers 409
    "the job has not finished" before completion, and 404 for a job that is not `publish`.
- Produces in the CLI: `jerome publish BOOK_ID --format pdf-book -o out.pdf`. It runs
  `prepare` and `render` inline, with no job, prints the warnings, and writes the file.
- Produces in MCP:
  - `publish_book(book_id, format)` returns the job JSON;
  - `finished_file(job_id)` returns `{"download_url", "filename", "media_type", "size", "expires_at"}`
    like `export_book`.

- [ ] **Step 1:** Write the failing tests.
  - **The worker path:**
    - `test_publish_job_makes_pdf_and_epub`: a DOCX book with authors runs through the worker
      (`drain` from `tests/fixtures/translation.py`, with deps that have storage). The job is
      completed, `stats.file` exists in storage, `GET /v1/jobs/{id}/file` serves it, and the
      webhook fires with `job.completed`.
    - `test_text_difference_fails_job_at_once`: patch `check_text` to raise. The job fails
      on attempt 1, and `task.error` has the message.
  - **Guards and warnings:**
    - `test_publish_guards`:
      - no author gives 409 with the message;
      - a source book gives 409;
      - a translated book with a pending block gives 409 before the job exists (Review
        Focus 5);
      - a second publish while one is queued gives 409 "already active" (Review Focus 4).
    - `test_proofread_book_with_open_suggestion_warns_not_refuses`.
    - `test_two_runs_keep_two_files` (Review Focus 4).
  - **Translated and EPUB-born books:**
    - `test_translated_book_uses_translated_title_and_language`: the PDF metadata `/Title`
      is the translated title and `lang` is the target (Review Focus 5).
    - `test_epub_born_book_epub_book_is_patched_clean_export`.
  - **Limits:**
    - `test_image_limit`: monkeypatch the limit to 1 KB and expect a `PublishError`.
  - **CLI and MCP:**
    - `test_cli_publish_writes_file`.
    - `test_mcp_publish_and_finished_file`.
- [ ] **Step 2:** Run them. Expect a FAIL.
- [ ] **Step 3:** Implement it. Factor out
  `untranslated_conflict(session, book) -> Conflict | None` from `translated_base`'s
  pending/flagged check, and use it in both places.
- [ ] **Step 4:** Run the new tests and the full backend suite. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: publish jobs make finished EPUB and PDF files`.

### Task 10: Web — Publication section and finished files

**Files:**
- Create: `web/src/lib/publication.ts`, `web/src/components/project/PublicationSection.tsx`, `web/src/components/project/FinishedFiles.tsx` (+ `.test.tsx` for both)
- Modify:
  - `web/src/lib/jobs.ts`: `JobTask.kind` gains `"publish"`; `jobNoun` gives
    "finished EPUB"/"finished PDF" by `passes[0]`; `Job.stats` gains `file?: string` and
    `warnings?: string[]`;
  - `web/src/components/project/JobsList.tsx`;
  - `web/src/pages/ProjectPage.tsx`.

**Interfaces:**
- Consumes: the Task 3 and Task 9 routes and `apiFetch`.
- Produces in `publication.ts`: `usePublication(bookId)`, `useSavePublication(bookId)`,
  `useUploadCover(bookId)`, `useDeleteCover(bookId)`, `usePublish(bookId)`.

- [ ] **Step 1:** Write the failing tests:
  - `PublicationSection saves authors as a list, clears a field with null, shows the ISBN error from a 422`.
  - `PublicationSection warns "Shops need a cover" without one and shows the cover preview with one`.
  - `FinishedFiles: Make EPUB / Make PDF POST /v1/books/{id}/publish; disabled with "Set the author first" when there is no author, and while untranslated > 0`.
  - `JobsList shows a publish job's warnings and a Download link to /v1/jobs/{id}/file when completed, and task.error when failed`.
- [ ] **Step 2:** Run `npm test`. Expect a FAIL.
- [ ] **Step 3:** Implement it with shadcn `Input`/`Label`/`Button` and `NATIVE_SELECT` for
  the trim size, following `ModelSettings.tsx`. Show Publication and Finished files under
  the existing ExportMenu on the project page. Keep the plain `<a download>` pattern for the
  download link.
- [ ] **Step 4:** Run `cd web && npm test && npm run build`. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat(web): publication details and finished files on the project page`.

### Task 11: Measure, document, final check

**Files:**
- Modify: `README.md` (a "Finished files" section; `jerome publish` in the CLI list), and the
  spec's limits only if the measurements are far off.
- Scratchpad only (not committed): a measuring script.

- [ ] **Step 1:** Render `pg932.epub` and `pg22367.epub` as `pdf-book` through
  `prepare`/`render` in the Docker image. Record the pages, seconds and peak RSS. Scale to
  600 pages. If the estimate exceeds 5 minutes or 2 GiB, write down the numbers and change
  the spec's limits to the measured numbers plus a margin of half again. Do not change the
  code's defaults without that record.
- [ ] **Step 2:** Write the README section:
  - what the two formats are;
  - the Publication fields and the cover rule;
  - which front pages are left out;
  - the text check, and that it cannot be overridden;
  - the warnings;
  - that a print-ready PDF is not made.
- [ ] **Step 3:** Run the backend, lint and web checks. Expect all to PASS.
- [ ] **Step 4:** Commit with the message `docs: finished files`.
