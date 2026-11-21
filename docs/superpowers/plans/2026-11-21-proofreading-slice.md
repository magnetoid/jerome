# Jerome Proofreading Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a book proofreading engine that does all of the following:
- imports a DOCX or EPUB in English or German;
- proposes fixes from LanguageTool, an AI pass and a consistency pass, in resumable cost-capped jobs;
- records every editor decision, made through the REST API or through a Word round trip;
- exports the book with its formatting intact.

**Architecture:**
- One Python package, `jerome`.
- A file-only document layer (`jerome/documents`) parses and patches DOCX/EPUB, with no database or AI.
- Pure services (`jerome/core`, `jerome/proofread`, `jerome/jobs`, `jerome/models`) work on Postgres through SQLAlchemy.
- FastAPI routes and a Typer CLI are thin wrappers over the services.
- A worker process claims chapter tasks from a Postgres queue table and commits after every chunk.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2 (sync) + psycopg 3, Alembic, Postgres 16, lxml, python-docx (test fixtures only), pysbd, httpx, anthropic SDK, Typer, pytest, respx, ruff, Docker Compose, LanguageTool server.

**Spec:** `docs/superpowers/specs/2026-11-21-proofreading-slice-design.md`. Read it before any task; section numbers below (§) refer to it.

## Global Constraints

- **The repo is public.** No manuscripts, uploads, exports or job data go in it.
  - `.gitignore` ignores `*.docx` and `*.epub` everywhere except `tests/fixtures/public/`.
  - Runtime files live under `JEROME_DATA_DIR` (default `~/.local/share/jerome`).
- **Test fixtures:** never use books from the owner's machine. Use only fixtures generated in code, plus public-domain Project Gutenberg texts.
- **Fixed vocabularies:**

  | Field | Allowed values |
  |---|---|
  | Categories | `typo`, `grammar`, `punctuation`, `consistency`, `style` |
  | Severities | `high`, `medium`, `low` |
  | Sources | `languagetool`, `model`, `consistency`, `editor` |
  | Statuses | `open`, `accepted`, `rejected`, `edited`, `superseded` |
  | Variants | `en-US`, `en-GB`, `de-DE`, `de-AT`, `de-CH` |

- **Default quote styles,** as four characters: primary open/close, then secondary open/close.

  | Variant | Quotes |
  |---|---|
  | en-US | `“”‘’` |
  | en-GB | `‘’“”` |
  | de-DE, de-AT | `„“‚‘` |
  | de-CH | `«»‹›` |

- **Placeholders:** every non-text inline object in a block becomes `"￼"`. No change may cover a placeholder.
- **Word output conventions:**
  - Tracked changes and comments use the author `Jerome`.
  - Comment text format: `[category · severity] explanation`.
  - Block bookmarks are named `_jb<block_id>`.
  - The review copy carries the custom document property `jerome_review_export_id`.
- **Chapters and chunks:**
  - A DOCX chapter starts at outline level 0. With none, the book splits into parts of about 5,000 words.
  - AI chunks are about 2,500 words, plus the previous block as read-only context.
- **Cost:**
  - When a provider can't count tokens, assume about 3.5 characters per token.
  - Estimated output is 15% of input.
- **Retries:** at most 3 per chunk, with backoff.
- **Jobs:** one active proofreading job per book. Statuses: `queued`, `running`, `paused_cost_cap`, `cancelled`, `failed`, `completed`.
- **Short-text endpoint:**
  - accepts at most 5,000 characters;
  - has a daily cost cap per key, `JEROME_TEXT_DAILY_COST_CAP`;
  - has a rate limit per key, `JEROME_TEXT_RPM` (default 30).
- **Default AI model:** `claude-opus-5-5`, with an explicit effort setting from config. Before writing the Claude adapter, read the `claude-api` skill's Python README.
- **Consistency pass:** near-duplicate names must appear at least 3 times and be one edit apart.
- **Uploads:** 50 MB maximum.

## Review Focus

1. **Real Word files split words across many runs.** Word inserts rsid splits, `w:proofErr`, `w:bookmarkStart` and `w:smartTag` between runs. Reading and patching must still find the right characters and keep each character's formatting. Pinned in Tasks 2 and 3 (`make_fragmented_docx`).
2. **LanguageTool counts offsets in UTF-16 units, not Python code points.** An emoji or other astral character earlier in a block shifts every later match. Pinned in Task 12 (`test_utf16_offsets_converted`).
3. **The AI echoes `original` with straight quotes, `...` or collapsed whitespace** while the book has `“”`, `…` or non-breaking spaces. Matching must tolerate this and map back to the true offsets, or most AI suggestions are silently dropped. Pinned in Task 13 (`test_tolerant_match_typographic_quotes`).
4. **A stale review copy is uploaded after newer API decisions.** A Word decision must not overwrite a status that changed after export: it is reported as a conflict. Pinned in Task 20 (`test_stale_copy_conflict_not_overwritten`).
5. **Large books (3,000+ paragraphs)** must import and export without timing out. Pinned in Task 7 (`test_large_book_import_and_export_under_20s`).

---

## Milestone 1 — Document layer (files only, no DB, no AI)

### Task 1: Project skeleton, shared types, changes, sentence splitting

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `jerome/__init__.py`, `jerome/types.py`, `jerome/errors.py`, `jerome/documents/__init__.py`, `jerome/documents/model.py`, `jerome/documents/changes.py`, `jerome/documents/segments.py`
- Test: `tests/documents/test_changes.py`, `tests/documents/test_segments.py`

**Interfaces:**
- Produces, in `jerome/types.py`:
  - enums (`StrEnum`) `Category`, `Severity` (with `.rank`: high=3, medium=2, low=1), `Source`, `Status`, and `Variant = Literal[...]`;
  - `@dataclass(frozen=True) BlockText(id: int, text: str)`;
  - `@dataclass(frozen=True) Candidate(block_id: int, start: int, end: int, original: str, replacement: str, category: Category, severity: Severity, source: Source, explanation: str)`;
  - `@dataclass(frozen=True) StyleSheet(variant: Variant, quote_style: str, house_spellings: dict[str, list[str]], ignore_words: list[str])` with `.language -> Literal["en","de"]`;
  - `DEFAULT_QUOTES: dict[Variant, str]`.
- Produces, in `jerome/errors.py`: `JeromeError`, and its subclasses `NotFound`, `Conflict`, `UnsupportedDocumentError`, `InvalidChangeError`.
- Produces, in `jerome/documents/model.py`:
  - `PLACEHOLDER = "￼"`;
  - `MarkKind` (`bold`, `italic`, `underline`, `smallcaps`, `superscript`, `subscript`, `strike`, `link`);
  - `Mark(start, end, kind, href: str | None = None)`;
  - `BlockKind` (`paragraph`, `heading`, `list_item`, `quote`, `table_cell`, `footnote`);
  - `ParsedBlock(key: str, kind: BlockKind, text: str, marks: list[Mark])` with `.hash` (sha256 hex of text);
  - `ParsedChapter(title: str, proofread: bool, source_ref: str, blocks: list[ParsedBlock])`;
  - `ParsedBook(format: Literal["docx","epub"], title: str | None, language: str | None, chapters: list[ParsedChapter])`.
- Produces, in `jerome/documents/changes.py`:
  - `@dataclass(frozen=True) Change(start: int, end: int, replacement: str, ref: str | None = None, comment: str | None = None, author: str = "Jerome")`;
  - `validate_changes(text: str, changes: Sequence[Change]) -> list[Change]` (returns them sorted; raises `InvalidChangeError`);
  - `apply_changes(text: str, changes: Sequence[Change]) -> str`.
- Produces, in `jerome/documents/segments.py`: `split_sentences(text: str, language: Literal["en","de"]) -> list[tuple[int, int]]`.

- [ ] **Step 1: Create the uv project.**
  - Python 3.12. Runtime deps: fastapi, uvicorn, sqlalchemy>=2, psycopg[binary], alembic, pydantic-settings, python-multipart, lxml, pysbd, httpx, anthropic, typer.
  - Dev deps: pytest, respx, ruff, python-docx.
  - `.gitignore`: Python defaults, `.env`, `eval/data/`, `eval/results/`, `*.docx`, `*.epub`, `!tests/fixtures/public/**`.
  - Run: `uv sync && uv run pytest -q`. Expected: "no tests ran".

- [ ] **Step 2: Write the failing tests**

```python
# tests/documents/test_changes.py
def test_apply_non_overlapping_changes_right_to_left():
    assert apply_changes("teh cat sat", [Change(0, 3, "the"), Change(8, 11, "sits")]) == "the cat sits"

def test_overlapping_changes_raise():
    with pytest.raises(InvalidChangeError):
        validate_changes("abcdef", [Change(0, 3, "x"), Change(2, 4, "y")])

def test_change_covering_placeholder_raises():
    with pytest.raises(InvalidChangeError):
        validate_changes(f"ab{PLACEHOLDER}cd", [Change(1, 3, "z")])

def test_out_of_range_raises():
    with pytest.raises(InvalidChangeError):
        validate_changes("abc", [Change(2, 5, "x")])

# tests/documents/test_segments.py
def test_english_sentences():
    assert split_sentences("Mr. Smith left. He was late.", "en") == [(0, 15), (16, 28)]

def test_german_sentences_with_abbreviation():
    assert len(split_sentences("Das ist z. B. gut. Ja.", "de")) == 2
```

- [ ] **Step 3: Run** `uv run pytest tests/documents -v`. Expected: FAIL (import errors).
- [ ] **Step 4: Implement** the types, errors, model dataclasses, `validate_changes`/`apply_changes`, and `split_sentences`. Use pysbd with `char_span=True` and strip trailing whitespace from each span. Zero-width insertions (`start == end`) are allowed, but two of them at the same offset count as overlapping.
- [ ] **Step 5: Run** `uv run pytest tests/documents -v && uv run ruff check .`. Expected: PASS, no lint errors.
- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: project skeleton, shared types, change application, sentence splitting"
```

### Task 2: DOCX reader

**Files:**
- Create: `jerome/documents/docx_xml.py`, `jerome/documents/docx_reader.py`, `tests/fixtures/__init__.py`, `tests/fixtures/docx_builders.py`
- Test: `tests/documents/test_docx_reader.py`

**Interfaces:**
- Consumes: Task 1 model, `UnsupportedDocumentError`.
- Produces, in `jerome/documents/docx_xml.py`. The writer (Task 3) and the reviewed reader (Task 19) use these too.
  - `NS: dict[str, str]`.
  - `iter_paragraphs(package: dict[str, bytes]) -> Iterator[tuple[str, etree._Element, etree._ElementTree]]`. It yields `(key, w:p element, tree)` for every proofreadable paragraph in document order:
    - first `word/document.xml` (body and tables, skipping anything inside `w:txbxContent`, headers and footers);
    - then `word/footnotes.xml` and `word/endnotes.xml` (skipping separator notes).
    - Key format: `"<part>#<n>"`, where `n` is the 0-based index in that part's filtered paragraph list.
  - `paragraph_runs(p) -> ParagraphText`, returning `ParagraphText(text: str, marks: list[Mark], pieces: list[Piece])`.
    - A `Piece(start, end, run, node)` maps text offsets to the `w:r` and `w:t` that hold them.
    - Placeholders cover `w:tab`, `w:br`, `w:cr`, `w:softHyphen`, `w:noBreakHyphen`, `w:sym`, `w:drawing`, `w:pict`, `w:object`, footnote/endnote references, `w:fldSimple`, and a whole complex field (`fldChar` begin…end) as one placeholder.
  - `read_package(data: bytes) -> dict[str, bytes]` and `write_package(package: dict[str, bytes], original: bytes) -> bytes` (keeps zip order and compression per entry).
- Produces, in `jerome/documents/docx_reader.py`: `read_docx(data: bytes) -> ParsedBook`.
- Produces, in `tests/fixtures/docx_builders.py`:
  - `make_docx(lines: list[str], footnotes: dict[int, str] | None = None, styles: dict[str, int] | None = None) -> bytes`. A mini-markup:
    - `# ` starts a Heading 1 and `## ` a Heading 2;
    - `**bold**`, `*italic*`, `[text](url)`;
    - `[^n]` is a footnote reference, `\t` a tab;
    - `|a|b|` is a table row;
    - `@Style: text` uses a custom paragraph style, whose outline level comes from `styles`.
  - `make_fragmented_docx() -> bytes`: one paragraph "The quick brown fox." with "quick" split into 4 runs, with `w:proofErr`, `w:bookmarkStart/End` and an empty run between them, and "brown" in bold.
  - `make_tracked_docx() -> bytes`: contains an existing `w:ins`.
  - `make_textbox_docx() -> bytes`.
  - Inspection helpers used by later tasks: `all_blocks(data) -> list[ParsedBlock]`, `only_block(data) -> ParsedBlock`, `part_xml(data, part) -> str`, `para_xml(data, n) -> bytes` (serialized n-th body `w:p`), `revision_ids(data) -> list[str]`, `custom_props(data) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reads_text_and_marks():
    b = read_docx(make_docx(["Plain **bold** and *italic*."])).chapters[0].blocks[0]
    assert b.text == "Plain bold and italic."
    assert Mark(6, 10, MarkKind.BOLD) in b.marks and Mark(15, 21, MarkKind.ITALIC) in b.marks

def test_link_mark_has_href():
    b = only_block(make_docx(["See [here](https://example.org)."]))
    assert Mark(4, 8, MarkKind.LINK, "https://example.org") in b.marks

def test_footnote_reference_is_placeholder_and_footnote_is_block():
    book = read_docx(make_docx(["Text[^1] more."], footnotes={1: "A note."}))
    blocks = [b for c in book.chapters for b in c.blocks]
    assert blocks[0].text == f"Text{PLACEHOLDER} more."
    assert blocks[-1].kind == BlockKind.FOOTNOTE and blocks[-1].key.startswith("word/footnotes.xml#")

def test_tab_is_placeholder():
    assert only_block(make_docx(["a\tb"])).text == f"a{PLACEHOLDER}b"

def test_chapters_split_at_heading_1():
    book = read_docx(make_docx(["# One", "a", "## Sub", "b", "# Two", "c"]))
    assert [c.title for c in book.chapters] == ["One", "Two"]
    assert book.chapters[0].blocks[0].kind == BlockKind.HEADING

def test_custom_style_with_outline_level_0_starts_chapter():
    book = read_docx(make_docx(["@ChapterTitle: Eins", "x", "@ChapterTitle: Zwei", "y"], styles={"ChapterTitle": 0}))
    assert [c.title for c in book.chapters] == ["Eins", "Zwei"]

def test_no_headings_splits_into_5000_word_parts():
    book = read_docx(make_docx([" ".join(["word"] * 100)] * 120))  # 12,000 words
    assert [c.title for c in book.chapters] == ["Part 1", "Part 2", "Part 3"]

def test_table_cells_are_blocks():
    kinds = {b.kind for b in all_blocks(make_docx(["|a|b|"]))}
    assert kinds == {BlockKind.TABLE_CELL}

def test_text_box_paragraphs_skipped():
    assert all("textbox" not in b.text for b in all_blocks(make_textbox_docx()))

def test_refuses_unresolved_tracked_changes():
    with pytest.raises(UnsupportedDocumentError, match="tracked changes"):
        read_docx(make_tracked_docx())

def test_fragmented_runs_read_as_one_text():  # Review Focus 1
    b = only_block(make_fragmented_docx())
    assert b.text == "The quick brown fox."
    assert Mark(10, 15, MarkKind.BOLD) in b.marks

def test_block_keys_unique_and_ordered():
    keys = [b.key for b in all_blocks(make_docx(["a", "b", "c"]))]
    assert keys == ["word/document.xml#0", "word/document.xml#1", "word/document.xml#2"]
```

- [ ] **Step 2: Run** `uv run pytest tests/documents/test_docx_reader.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `docx_xml.py` and `read_docx`.
  - Resolve each paragraph's outline level from `w:pPr/w:outlineLvl`, or from its style following the `w:basedOn` chain in `word/styles.xml`.
  - Marks come from direct run properties only.
  - Tracked changes already in the input (`w:ins`, `w:del`, `w:moveFrom`, `w:moveTo` anywhere in the document part) → raise `UnsupportedDocumentError("Document contains unresolved tracked changes; accept or reject them in Word first")`.
  - Merge adjacent marks of the same kind.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: DOCX reader with chapters, marks, placeholders"` (add new files first).

### Task 3: DOCX writer (clean and tracked, comments, bookmarks, custom properties)

**Files:**
- Create: `jerome/documents/docx_writer.py`
- Test: `tests/documents/test_docx_writer.py`

**Interfaces:**
- Consumes: `iter_paragraphs`, `paragraph_runs`, `read_package`, `write_package` (Task 2); `Change`, `validate_changes` (Task 1).
- Produces:
  - `@dataclass DocxWriteOptions(mode: Literal["clean","tracked"], date: datetime, bookmarks: Mapping[str, str] = {}, custom_properties: Mapping[str, str] = {})`, where `bookmarks` maps block key to bookmark name;
  - `write_docx(original: bytes, changes: Mapping[str, Sequence[Change]], options: DocxWriteOptions) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```python
NOW = datetime(2026, 11, 21, tzinfo=UTC)

def test_roundtrip_without_changes_preserves_text_and_marks():
    src = make_docx(["# One", "Plain **bold** [l](https://x.org)[^1].", "|a|b|"], footnotes={1: "n"})
    assert read_docx(write_docx(src, {}, DocxWriteOptions("clean", NOW))) == read_docx(src)

def test_untouched_paragraph_xml_byte_identical():
    src = make_docx(["first para", "second para"])
    out = write_docx(src, {"word/document.xml#1": [Change(0, 6, "Second")]}, DocxWriteOptions("clean", NOW))
    assert para_xml(out, 0) == para_xml(src, 0)

def test_clean_change_across_runs_keeps_per_char_formatting():
    src = make_docx(["Plain **bold** text"])  # "bold" at 6..10
    out = write_docx(src, {"word/document.xml#0": [Change(8, 13, "LD TE")]}, DocxWriteOptions("clean", NOW))
    b = only_block(out)
    assert b.text == "Plain boLD TExt" and Mark(6, 13, MarkKind.BOLD) in b.marks  # insertion inherits char before

def test_fragmented_change_applies():  # Review Focus 1
    out = write_docx(make_fragmented_docx(), {"word/document.xml#0": [Change(4, 9, "slow")]}, DocxWriteOptions("clean", NOW))
    assert only_block(out).text == "The slow brown fox."

def test_tracked_change_emits_ins_and_del():
    out = write_docx(make_docx(["teh cat"]), {"word/document.xml#0": [Change(0, 3, "the")]}, DocxWriteOptions("tracked", NOW))
    doc = part_xml(out, "word/document.xml")
    assert doc.count("<w:del ") == 1 and doc.count("<w:ins ") == 1 and 'w:author="Jerome"' in doc
    with pytest.raises(UnsupportedDocumentError):
        read_docx(out)  # it is now a document with pending changes

def test_revision_ids_unique_across_document():
    changes = {"word/document.xml#0": [Change(0, 1, "A")], "word/document.xml#1": [Change(0, 1, "B")]}
    ids = revision_ids(write_docx(make_docx(["a", "b"]), changes, DocxWriteOptions("tracked", NOW)))
    assert len(ids) == len(set(ids))

def test_comment_written_for_change_with_comment():
    ch = Change(0, 3, "the", comment="[typo · high] Misspelling.")
    out = write_docx(make_docx(["teh"]), {"word/document.xml#0": [ch]}, DocxWriteOptions("tracked", NOW))
    assert "[typo · high] Misspelling." in part_xml(out, "word/comments.xml")
    assert "commentRangeStart" in part_xml(out, "word/document.xml")

def test_bookmarks_wrap_paragraphs():
    opts = DocxWriteOptions("tracked", NOW, bookmarks={"word/document.xml#0": "_jb7"})
    assert 'w:name="_jb7"' in part_xml(write_docx(make_docx(["x"]), {}, opts), "word/document.xml")

def test_custom_property_written():
    opts = DocxWriteOptions("tracked", NOW, custom_properties={"jerome_review_export_id": "42"})
    assert custom_props(write_docx(make_docx(["x"]), {}, opts)) == {"jerome_review_export_id": "42"}

@pytest.mark.integration
def test_libreoffice_converts_tracked_output(tmp_path):  # skip if `soffice` not on PATH
    ...  # write tracked output, run `soffice --headless --convert-to pdf`, assert exit 0 and the PDF exists
```

- [ ] **Step 2: Run** `uv run pytest tests/documents/test_docx_writer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `write_docx`.** For each block key with changes, process its changes right to left. For each change:
  1. Split the `w:t` nodes at `start` and `end` with `Piece` offsets. Each split run gets a deep copy of the original `w:rPr`.
  2. **Clean mode:** delete the covered runs and insert one new run with the `rPr` of the run holding character `start-1` (or of the first covered run when `start == 0`) and `xml:space="preserve"`.
  3. **Tracked mode:** wrap the covered runs in `w:del`, turning each `w:t` into `w:delText`. Insert a `w:ins` holding the new run. Both carry `w:author`, `w:date` and a unique `w:id`, counted from the maximum existing id + 1. With a comment, add `w:commentRangeStart`/`End` around both, a `w:commentReference` run, and a `w:comment` in `word/comments.xml`. Create the part, its relationship and its content type if missing.
  4. Bookmarks: insert `w:bookmarkStart` as the first child after `w:pPr`, and `w:bookmarkEnd` as the last child, with unique ids.
  5. Custom properties: write `docProps/custom.xml` with `vt:lpwstr` values, plus its relationship and content type.

  Only the parts that changed are re-serialized. Untouched parts keep their original bytes.
- [ ] **Step 4: Run** the same command. Expected: PASS (the integration test is skipped without LibreOffice).
- [ ] **Step 5: Commit** `git commit -am "feat: DOCX writer with clean and tracked modes"` (add new files first).

### Task 4: EPUB reader

**Files:**
- Create: `jerome/documents/epub_xml.py`, `jerome/documents/epub_reader.py`, `tests/fixtures/epub_builders.py`, `scripts/fetch_public_fixtures.py`, `tests/fixtures/public/` (two committed Gutenberg EPUBs, each with its license header intact: EN "The Fall of the House of Usher", pg932; DE "Die Verwandlung", pg22367)
- Test: `tests/documents/test_epub_reader.py`

**Interfaces:**
- Produces, in `epub_xml.py`:
  - `iter_epub_blocks(root: etree._Element) -> Iterator[tuple[int, etree._Element]]`: the leaf block elements (`p`, `h1`–`h6`, `li`, `blockquote` without block children, `td`) in document order;
  - `element_text(el) -> ParagraphText`. Same shape as DOCX: `pieces` map offsets to (element, `"text"`/`"tail"`). Adjacent inline elements with identical formatting are merged, and empty spans are ignored;
  - `read_container(data: bytes) -> EpubPackage(opf_path, manifest, spine, nav_points, files: dict[str, bytes], order: list[str])`.
- Produces: `read_epub(data: bytes) -> ParsedBook`.
  - Block key: `"<xhtml path>#<n>"`.
  - Chapter `source_ref`: `"<xhtml path>#<anchor or ''>"`.
- Produces, in `epub_builders.py`:
  - `make_epub(chapters: list[tuple[str, list[str]]], *, toc: Literal["nav","ncx","none"] = "nav", front_matter: bool = False, split_chapter: bool = False, two_in_one_file: bool = False, cluttered: bool = False) -> bytes`. Paragraphs use the same `**`/`*`/`[t](u)` mini-markup as `make_docx`, and each chapter's title becomes its first block (an `h1`).
  - Inspection helpers: `titles(data_or_book)`, `blocks_of(book) -> list[tuple[str, list[Mark]]]`, `block_key(data, chapter, block) -> str`, `block(data, chapter, block) -> ParsedBlock`, `zip_names(data)`, `zip_info(data, name)`, `unchanged_entries(a, b) -> set[str]`, `all_entries_except(data, name) -> set[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_chapters_follow_nav_toc():
    assert titles(read_epub(make_epub([("One", ["a"]), ("Two", ["b"])]))) == ["One", "Two"]

def test_ncx_toc_used_for_epub2():
    assert titles(read_epub(make_epub([("One", ["a"])], toc="ncx"))) == ["One"]

def test_chapter_split_across_two_files_is_one_chapter():
    book = read_epub(make_epub([("One", ["a", "b"])], split_chapter=True))
    assert len(book.chapters) == 1 and [b.text for b in book.chapters[0].blocks] == ["One", "a", "b"]

def test_two_chapters_in_one_file_split_at_anchor():
    assert titles(read_epub(make_epub([("One", ["a"]), ("Two", ["b"])], two_in_one_file=True))) == ["One", "Two"]

def test_front_matter_not_proofread():
    book = read_epub(make_epub([("One", ["a"])], front_matter=True))
    assert [c.proofread for c in book.chapters] == [False, True]

def test_no_toc_falls_back_to_spine():
    assert len(read_epub(make_epub([("One", ["a"]), ("Two", ["b"])], toc="none")).chapters) == 2

def test_inline_marks():
    b = read_epub(make_epub([("C", ["x **b** *i* [l](https://e.org)"])])).chapters[0].blocks[1]
    assert {m.kind for m in b.marks} == {MarkKind.BOLD, MarkKind.ITALIC, MarkKind.LINK}

def test_cluttered_markup_reads_same_as_clean():
    clean = read_epub(make_epub([("C", ["Hello **big** world"])]))
    messy = read_epub(make_epub([("C", ["Hello **big** world"])], cluttered=True))
    assert blocks_of(clean) == blocks_of(messy)

@pytest.mark.parametrize("name,lang", [("pg932.epub", "en"), ("pg22367.epub", "de")])
def test_public_fixtures_parse(name, lang):
    book = read_epub((FIXTURES / "public" / name).read_bytes())
    assert book.language.startswith(lang) and sum(len(c.blocks) for c in book.chapters) > 50
```

- [ ] **Step 2: Run** `uv run python scripts/fetch_public_fixtures.py` (one-off download into `tests/fixtures/public/`), then `uv run pytest tests/documents/test_epub_reader.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `read_epub`.**
  - Use the EPUB 3 `nav` (`epub:type="toc"`) or the EPUB 2 NCX top-level entries.
  - A chapter runs from its TOC target (file plus optional anchor) to the next entry's target, following spine order.
  - Front and back matter (`proofread=False`): `linear="no"` spine items, `epub:type` in {cover, titlepage, copyright-page, toc, frontmatter, backmatter}, and spine documents before the first TOC target.
  - With no usable TOC: one chapter per spine document, titled by its first heading or `Section n`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: EPUB reader with TOC-based chapters"` (add new files, including the two public fixtures).

### Task 5: EPUB writer

**Files:**
- Create: `jerome/documents/epub_writer.py`
- Test: `tests/documents/test_epub_writer.py`

**Interfaces:**
- Consumes: `iter_epub_blocks`, `element_text`, `read_container` (Task 4); `Change` (Task 1).
- Produces: `write_epub(original: bytes, changes: Mapping[str, Sequence[Change]]) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```python
def test_roundtrip_without_changes():
    src = (FIXTURES / "public" / "pg932.epub").read_bytes()
    out = write_epub(src, {})
    assert read_epub(out) == read_epub(src)
    assert zip_names(out)[0] == "mimetype" and zip_info(out, "mimetype").compress_type == ZIP_STORED

def test_untouched_files_byte_identical():
    src = make_epub([("One", ["a"]), ("Two", ["teh"])])
    key = block_key(src, chapter=1, block=1)
    out = write_epub(src, {key: [Change(0, 3, "the")]})
    assert unchanged_entries(src, out) == all_entries_except(src, key.split("#")[0])

def test_change_across_inline_elements_inherits_previous_char():
    src = make_epub([("C", ["Hello *wor*ld"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(6, 11, "WORLD")]})
    assert block(out, 0, 1).text == "Hello WORLD"
```

- [ ] **Step 2: Run** `uv run pytest tests/documents/test_epub_writer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Patch the text and tail pieces right to left. Inserted text goes into the piece holding character `start-1`. Re-serialize only the XHTML files that changed, keeping the XML declaration and doctype. Copy every other zip entry byte-for-byte, in the original order.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: EPUB writer"` (add new files first).

---

## Milestone 2 — Storage, core services, REST API (no AI yet)

### Task 6: Configuration, database schema, migrations

**Files:**
- Create: `jerome/config.py`, `jerome/db.py`, `jerome/orm.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`, `docker-compose.yml` (only the `db` service for now: postgres:16 on port 5433, user/password/db `jerome`), `tests/conftest.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, prefix `JEROME_`) with these fields:
  - `database_url`, `data_dir` (default `~/.local/share/jerome`), `languagetool_url`, `models` (JSON), `prices` (JSON), `webhook_secret`, `text_daily_cost_cap` (Decimal, default 5), `text_rpm` (default 30), `max_upload_mb` (50);
  - provider keys come from their standard env vars.
- Produces: `get_settings()`, `make_engine(url)`, and `session_scope(engine) -> ContextManager[Session]`.
- Produces ORM models in `jerome/orm.py`, tables and columns exactly as below:
  - `Book(id, title, format, local_only, original_filename, created_at)`
  - `StyleSheetRow(book_id PK/FK, variant, quote_style, house_spellings JSONB, ignore_words JSONB)`
  - `Chapter(id, book_id, ordinal, title, proofread, source_ref)`
  - `Block(id, chapter_id, book_id, ordinal, kind, source_ref, text, marks JSONB, hash)`
  - `Segment(id, block_id, ordinal, start, end)`
  - `Job(id, book_id, type, status, passes JSONB, chapter_ids JSONB null, max_cost NUMERIC(12,4), cost_actual NUMERIC(12,4) default 0, estimate JSONB, stats JSONB default {}, callback_url, created_by, created_at, finished_at)`
  - `JobTask(id, job_id, chapter_id null, kind 'chapter'|'consistency', status 'queued'|'running'|'done'|'failed', chunks_done, attempts, not_before, error, started_at, finished_at)`
  - `Suggestion(id, book_id, block_id, job_id null, start, end, original, replacement, category, severity, source, explanation, status, final_text null, superseded_by null FK, model null, created_at)`
  - `Decision(id, suggestion_id, action, previous_status, new_status, final_text null, actor, channel 'api'|'word'|'system', review_import_id null, created_at)`
  - `ReviewExport(id, book_id, suggestion_statuses JSONB {id: status}, block_ids JSONB, filters JSONB, created_by, created_at)`
  - `ReviewImport(id, review_export_id, book_id, report JSONB, created_by, created_at)`
  - `ApiKey(id, name unique, key_hash, created_at, revoked_at null)`
  - `ModelUsage(id, job_id null, api_key_name null, pass_name, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost NUMERIC(12,6), created_at)`
  - Indexes: `suggestions(block_id, status)`, `job_tasks(status, not_before)`, and a partial unique index on `jobs(book_id)` where status is in (`queued`, `running`, `paused_cost_cap`).
  - Relationships (with cascade delete from `Book`): `Book.chapters`, `Book.style_sheet`, `Chapter.blocks`, `Block.segments`, `Job.tasks`.
- Produces, in `tests/conftest.py`:
  - an `engine` fixture (session scope) that runs `alembic upgrade head` against `JEROME_TEST_DATABASE_URL` (default `postgresql+psycopg://jerome:jerome@localhost:5433/jerome_test`);
  - a `session` fixture wrapping each test in a rolled-back transaction;
  - a `tmp_storage` fixture.

- [ ] **Step 1: Write the failing test**

```python
def test_migration_matches_models(engine):
    from alembic.autogenerate import compare_metadata
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []

def test_one_active_job_per_book(session, book_factory):
    b = book_factory()
    session.add_all([Job(book_id=b.id, type="proofread", status="queued", max_cost=1, passes=[]),
                     Job(book_id=b.id, type="proofread", status="running", max_cost=1, passes=[])])
    with pytest.raises(IntegrityError):
        session.flush()
```

- [ ] **Step 2: Run** `docker compose up -d db && uv run pytest tests/test_schema.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the settings, engine/session helpers, ORM and the initial migration (autogenerate, then review it by hand). Add `book_factory` to conftest.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: settings, ORM schema, initial migration"` (add new files).

### Task 7: Books core (import, storage, style sheets, chapters, delete)

**Files:**
- Create: `jerome/storage.py`, `jerome/core/__init__.py`, `jerome/core/books.py`, `jerome/core/style_sheets.py`
- Test: `tests/core/test_books.py`

**Interfaces:**
- Consumes: `read_docx`, `read_epub`, `split_sentences`, ORM, `DEFAULT_QUOTES`.
- Produces: `Storage(root: Path)` with:
  - `save_original(book_id: int, filename: str, data: bytes) -> Path`, writing to `<root>/books/<id>/original<ext>`;
  - `read_original(book_id) -> bytes`;
  - `save_export(book_id, name, data) -> Path`;
  - `delete_book(book_id) -> None`.
- Produces, in `core/books.py`:
  - `import_book(session, storage, *, filename: str, data: bytes, variant: Variant, title: str | None = None, local_only: bool = False) -> Book`. The format comes from magic bytes plus the extension. Anything else raises `UnsupportedDocumentError("Only DOCX and EPUB are supported")`.
  - `get_book(session, book_id) -> Book`, `list_books(session) -> list[Book]`, `list_chapters(session, book_id) -> list[Chapter]`, `set_chapter_proofread(session, chapter_id, proofread: bool) -> Chapter`, `delete_book(session, storage, book_id) -> None`, `block_texts(session, book_id, chapter_id: int | None = None) -> list[BlockText]`. `NotFound` is raised for missing rows.
- Produces, in `core/style_sheets.py`: `get_style_sheet(session, book_id) -> StyleSheet` and `update_style_sheet(session, book_id, *, variant=None, quote_style=None, house_spellings=None, ignore_words=None) -> StyleSheet`.

- [ ] **Step 1: Write the failing tests**

```python
def test_import_docx_creates_rows(session, tmp_storage):
    book = import_book(session, tmp_storage, filename="m.docx", data=make_docx(["# One", "A b. C d."]), variant="en-GB")
    assert [c.title for c in list_chapters(session, book.id)] == ["One"]
    assert session.query(Segment).count() == 3  # heading = 1, paragraph = 2 sentences

def test_original_stored_under_data_dir(session, tmp_storage):
    book = import_book(session, tmp_storage, filename="m.epub", data=make_epub([("C", ["x"])]), variant="de-DE")
    assert tmp_storage.read_original(book.id).startswith(b"PK")

def test_rejects_unknown_format(session, tmp_storage):
    with pytest.raises(UnsupportedDocumentError):
        import_book(session, tmp_storage, filename="m.pdf", data=b"%PDF-1.7", variant="en-US")

def test_style_sheet_defaults_per_variant(session, tmp_storage):
    book = import_book(session, tmp_storage, filename="m.docx", data=make_docx(["x"]), variant="de-CH")
    assert get_style_sheet(session, book.id).quote_style == "«»‹›"

def test_update_style_sheet(session, imported_docx_book):
    s = update_style_sheet(session, imported_docx_book.id, ignore_words=["Katharina"])
    assert s.ignore_words == ["Katharina"]

def test_delete_book_removes_rows_and_files(session, tmp_storage, imported_docx_book):
    delete_book(session, tmp_storage, imported_docx_book.id)
    assert session.get(Book, imported_docx_book.id) is None
    assert not (tmp_storage.root / "books" / str(imported_docx_book.id)).exists()

@pytest.mark.slow
def test_large_book_import_and_export_under_20s(session, tmp_storage):  # Review Focus 5
    data = make_docx([f"# Chapter {i}" if i % 100 == 0 else "Sentence one. Sentence two." for i in range(3000)])
    t = time.monotonic()
    book = import_book(session, tmp_storage, filename="big.docx", data=data, variant="en-US")
    write_docx(tmp_storage.read_original(book.id), {}, DocxWriteOptions("clean", NOW))
    assert time.monotonic() - t < 20
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_books.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. Bulk-insert blocks and segments with `session.execute(insert(...), rows)`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: book import, storage, style sheets"` (add new files).

### Task 8: API app, auth, keys, book routes

**Files:**
- Create: `jerome/core/keys.py`, `jerome/api/__init__.py`, `jerome/api/app.py`, `jerome/api/deps.py`, `jerome/api/schemas.py`, `jerome/api/routes_books.py`, `jerome/cli.py`
- Test: `tests/api/test_books_api.py`, `tests/test_cli.py`

**Interfaces:**
- Produces, in `core/keys.py`:
  - `create_api_key(session, name: str) -> str`: returns `jk_` + 32 url-safe random characters, shown once; stores only the sha256;
  - `verify_api_key(session, token: str) -> str | None`: returns the key name.
- Produces:
  - `create_app(settings: Settings | None = None) -> FastAPI`;
  - dependencies `get_session`, `get_storage`, `current_actor`. A Bearer token is required, otherwise 401.
  - Error mapping: `NotFound` → 404, `Conflict` → 409, `UnsupportedDocumentError` and `InvalidChangeError` → 422, all as `{"detail": str}`.
- Routes under `/v1`:
  - `POST /books` (multipart: `file`, `variant`, `title?`, `local_only?`; 413 above `max_upload_mb`)
  - `GET /books`, `GET /books/{id}`, `GET /books/{id}/chapters`
  - `PATCH /chapters/{id}` `{proofread}`
  - `GET|PUT /books/{id}/style-sheet`
  - `DELETE /books/{id}`
- CLI (Typer app `jerome`): `keys create NAME` prints the key, `serve` runs uvicorn on `jerome.api.app:create_app --factory`.

- [ ] **Step 1: Write the failing tests**

```python
def test_requires_bearer_key(client):
    assert client.get("/v1/books").status_code == 401

def test_upload_and_get_book(client, auth):
    r = client.post("/v1/books", headers=auth, files={"file": ("m.docx", make_docx(["# One", "x"]))}, data={"variant": "en-US"})
    assert r.status_code == 201
    assert client.get(f"/v1/books/{r.json()['id']}/chapters", headers=auth).json()[0]["title"] == "One"

def test_upload_too_large_413(client, auth, monkeypatch): ...  # max_upload_mb=0 → 413
def test_unsupported_upload_422(client, auth): ...
def test_style_sheet_roundtrip(client, auth, book_id): ...  # PUT ignore_words, then GET returns them
def test_patch_chapter_flag(client, auth, book_id): ...
def test_delete_book_404_after(client, auth, book_id): ...

def test_cli_keys_create(cli_runner, engine):
    out = cli_runner.invoke(app, ["keys", "create", "editor"])
    assert out.exit_code == 0 and out.stdout.strip().startswith("jk_")
```

- [ ] **Step 2: Run** `uv run pytest tests/api/test_books_api.py tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The `client` fixture builds `create_app` with test settings and overrides `get_session` to use the test transaction. The `auth` fixture creates a key named `tester`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: REST app, API keys, book routes, CLI"` (add new files).

### Task 9: Suggestions core and routes

**Files:**
- Create: `jerome/core/suggestions.py`, `jerome/api/routes_suggestions.py`
- Test: `tests/core/test_suggestions.py`, `tests/api/test_suggestions_api.py`

**Interfaces:**
- Consumes: ORM, `Candidate`, `apply_changes`.
- Produces:
  - `@dataclass AddResult(created: int, dropped_decided_overlap: int, dropped_rejected_repeat: int, dropped_open_overlap: int)`.
  - `add_candidates(session, book_id: int, job_id: int | None, candidates: Sequence[Candidate], model: str | None = None) -> AddResult`. It first locks the involved block rows with `SELECT … FOR UPDATE`. It raises `InvalidChangeError` if `block.text[start:end] != original`, and applies the rules in §7.3.
  - `decide(session, suggestion_id: int, action: Literal["accept","reject","edit","undo"], *, actor: str, channel: Literal["api","word"] = "api", replacement: str | None = None, review_import_id: int | None = None) -> Suggestion`. In detail:
    - `accept` marks overlapping open suggestions `superseded`, sets `superseded_by`, and writes a `supersede` decision with channel `system`;
    - `undo` sets the suggestion back to `open` and reopens the ones it superseded;
    - `edit` sets `final_text`.
  - `record_editor_change(session, block_id, start, end, original, replacement, *, pending: bool, actor, review_import_id) -> Suggestion | None`: source `editor`, status `open` if pending, else `accepted`. Returns None when an identical editor suggestion already exists.
  - `@dataclass SuggestionFilter(chapter_id=None, category=None, severity=None, min_severity=None, source=None, status=None)` and `list_suggestions(session, book_id, f: SuggestionFilter, limit=100, offset=0) -> list[Suggestion]`.
  - `bulk_decide(session, book_id, *, ids: list[int] | None, f: SuggestionFilter | None, action: Literal["accept","reject"], actor) -> int`.
  - `current_text(session, block_id) -> str`: the original text with `accepted` and `edited` changes applied.
- Routes:
  - `GET /v1/books/{id}/suggestions` (filter query params, `limit`, `offset`)
  - `GET /v1/books/{id}/blocks/{block_id}` → `{original, current, suggestions}`
  - `POST /v1/suggestions/{id}/{accept|reject|edit|undo}`
  - `POST /v1/suggestions/bulk`

- [ ] **Step 1: Write the failing tests**

```python
def test_drops_overlap_with_accepted(session, block):  # block.text == "teh cat sat"
    s = add_one(session, block, 0, 3, "teh", "the"); decide(session, s.id, "accept", actor="a")
    assert add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "The cat")]).dropped_decided_overlap == 1

def test_drops_repeat_of_rejected(session, block):
    s = add_one(session, block, 0, 3, "teh", "the"); decide(session, s.id, "reject", actor="a")
    assert add_candidates(session, block.book_id, None, [cand(block, 0, 3, "teh", "the")]).dropped_rejected_repeat == 1

def test_keeps_existing_open_on_overlap(session, block):
    add_one(session, block, 0, 3, "teh", "the")
    assert add_candidates(session, block.book_id, None, [cand(block, 0, 7, "teh cat", "the dog")]).dropped_open_overlap == 1

def test_rejects_wrong_original(session, block):
    with pytest.raises(InvalidChangeError):
        add_candidates(session, block.book_id, None, [cand(block, 0, 3, "xyz", "the")])

def test_accept_supersedes_overlapping_open_and_undo_reopens(session, block):
    a = add_one(session, block, 0, 3, "teh", "the"); b = insert_raw_open(session, block, 0, 7, "teh cat", "the dog")
    decide(session, a.id, "accept", actor="a"); assert session.get(Suggestion, b.id).status == "superseded"
    decide(session, a.id, "undo", actor="a"); assert session.get(Suggestion, b.id).status == "open"

def test_every_change_logged(session, block):
    s = add_one(session, block, 0, 3, "teh", "the"); decide(session, s.id, "edit", actor="ed", replacement="The")
    d = session.query(Decision).filter_by(suggestion_id=s.id).one()
    assert (d.action, d.previous_status, d.new_status, d.final_text, d.actor, d.channel) == ("edit", "open", "edited", "The", "ed", "api")

def test_current_text_applies_accepted_and_edited(session, block):
    s1 = add_one(session, block, 0, 3, "teh", "the"); s2 = add_one(session, block, 8, 11, "sat", "sits")
    decide(session, s1.id, "accept", actor="a"); decide(session, s2.id, "edit", actor="a", replacement="sat down")
    assert current_text(session, block.id) == "the cat sat down"

def test_list_filters_and_bulk(session, book_with_mixed_suggestions): ...  # min_severity=medium excludes low; bulk reject by filter returns count
```

API tests: `test_accept_route`, `test_bulk_route`, and `test_block_route_returns_original_current_suggestions`.

- [ ] **Step 2: Run** `uv run pytest tests/core/test_suggestions.py tests/api/test_suggestions_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: suggestions, decisions log, routes"` (add new files).

### Task 10: Exports (docx-clean, docx-tracked, epub-clean)

**Files:**
- Create: `jerome/core/exports.py`, `jerome/api/routes_exports.py`
- Test: `tests/core/test_exports.py`

**Interfaces:**
- Consumes: `write_docx`, `write_epub`, `Storage`, ORM.
- Produces:
  - `ExportFormat = Literal["docx-review","docx-tracked","docx-clean","epub-clean"]`;
  - `@dataclass ExportFile(filename: str, media_type: str, data: bytes)`;
  - `@dataclass ReviewFilters(min_severity: Severity | None = None, categories: list[Category] | None = None, chapter_ids: list[int] | None = None)`;
  - `export_book(session, storage, book_id: int, fmt: ExportFormat, *, filters: ReviewFilters | None = None, actor: str | None = None, now: datetime | None = None) -> ExportFile`. This task implements every format except `docx-review`, which raises `NotImplementedError` until Task 18;
  - `recorded_changes(session, book_id) -> dict[str, list[Change]]`: keyed by `block.source_ref`, built from `accepted` and `edited` suggestions, with `ref=str(suggestion.id)`.
- Route: `GET /v1/books/{id}/export?format=…` returns the file with `Content-Disposition`. A format that doesn't fit the book (`epub-clean` for a DOCX book, or a `docx-*` format for an EPUB book) → 422.

- [ ] **Step 1: Write the failing tests**

```python
def test_docx_clean_applies_only_recorded(session, tmp_storage, docx_book_with_decisions):
    f = export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-clean")
    texts = [b.text for b in all_blocks(f.data)]
    assert "the cat sat down" in texts and "an open suggestion stays teh" in texts

def test_docx_tracked_has_revisions_for_recorded(session, tmp_storage, docx_book_with_decisions):
    doc = part_xml(export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-tracked").data, "word/document.xml")
    assert doc.count("<w:ins ") == 2

def test_epub_clean(session, tmp_storage, epub_book_with_decisions): ...  # accepted text present; open suggestion's original text unchanged; read_epub succeeds
def test_format_mismatch_422(client, auth, epub_book_id): ...  # format=docx-clean → 422
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_exports.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: clean and tracked exports"` (add new files).

---

## Milestone 3 — Proofreading engine

### Task 11: Model layer

**Files:**
- Create: `jerome/models/__init__.py`, `jerome/models/base.py`, `jerome/models/pricing.py`, `jerome/models/registry.py`, `jerome/models/claude.py`, `jerome/models/openai_compat.py`, `jerome/models/fake.py`
- Test: `tests/models/test_models.py`

**Interfaces:**
- Produces, in `base.py`:
  - `@dataclass(frozen=True) SystemPart(text: str, cache: bool = False)`;
  - `@dataclass Usage(input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0)`;
  - `@dataclass CompletionResult(data: dict, usage: Usage, provider: str, model: str)`;
  - `class ModelRefusal(JeromeError)`;
  - `Provider` protocol: `name: str`, `local: bool`, `complete(*, model: str, system: list[SystemPart], user: str, schema: dict, max_tokens: int = 16000, effort: str | None = None) -> CompletionResult`, `count_tokens(*, model, system, user) -> int | None`.
- Produces, in `pricing.py`:
  - `Price(input, output, cache_read, cache_write)` in Decimal per million tokens;
  - `DEFAULT_PRICES = {"claude-opus-5-5": Price(4, 20, Decimal("0.20"), 5)}`. Settings `prices` overrides it; confirm the values against current Anthropic pricing when implementing;
  - `cost(price: Price, usage: Usage) -> Decimal`.
- Produces, in `registry.py`:
  - `PassConfig(provider: str, model: str, effort: str | None)`;
  - `Registry(settings, providers: dict[str, Provider])` with `for_pass(pass_name: str, *, local_only: bool) -> tuple[Provider, PassConfig] | None` and `price(model) -> Price`.
  - The default `models` setting: `{"ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}}`.
- Produces:
  - `ClaudeProvider(client: anthropic.Anthropic | None = None)`, with `local = False`;
  - `OpenAICompatProvider(base_url: str, api_key: str | None, name: str = "openai_compat")`. `local` is True when the host is `localhost`, `127.0.0.1`, `::1`, `host.docker.internal` or ends in `.local`;
  - `FakeProvider(responses: list[dict | Exception], local: bool = False, usage: Usage = Usage(1000, 150))`, which records its `calls`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cost_opus_one_million_each():
    assert cost(DEFAULT_PRICES["claude-opus-5-5"], Usage(1_000_000, 1_000_000)) == Decimal("24")

def test_registry_local_only_returns_local_or_none():
    reg = Registry(settings_with(models={"ai": {"provider": "anthropic", "model": "claude-opus-5-5"}}), {"anthropic": FakeProvider([], local=False)})
    assert reg.for_pass("ai", local_only=True) is None and reg.for_pass("ai", local_only=False) is not None

def test_claude_request_shape(respx_mock):  # anthropic client built with an httpx client so respx can intercept
    route = respx_mock.post("https://api.anthropic.com/v1/messages").respond(json=claude_json_reply({"suggestions": []}))
    ClaudeProvider(client=test_client()).complete(model="claude-opus-5-5", system=[SystemPart("rules", cache=True)], user="u", schema=SCHEMA, effort="high")
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "claude-opus-5-5" and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["effort"] == "high" and body["output_config"]["format"]["type"] == "json_schema"
    assert body["thinking"] == {"type": "adaptive"}

def test_claude_refusal_raises(respx_mock): ...  # stop_reason "refusal" → ModelRefusal
def test_openai_compat_parses_json_and_local_flag(respx_mock): ...  # base_url http://localhost:11434/v1 → local True; data parsed from message content
```

- [ ] **Step 2: Run** `uv run pytest tests/models -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Before writing `claude.py`, read the `claude-api` skill's `python/claude-api/README.md`. Use the official SDK with:
    - structured outputs (`output_config.format` with a JSON schema);
    - `thinking={"type": "adaptive"}`;
    - `output_config.effort` from config;
    - `cache_control` on `SystemPart(cache=True)`;
    - `messages.count_tokens` for `count_tokens`;
    - server-side refusal fallbacks (`fallbacks: "default"` with the beta named in the skill).
  - Raise `ModelRefusal` when `stop_reason == "refusal"`.
  - The OpenAI-compatible adapter posts to `/chat/completions` with `response_format={"type": "json_schema", ...}` and returns `None` from `count_tokens`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: provider-agnostic model layer"` (add new files).

### Task 12: LanguageTool pass

**Files:**
- Create: `jerome/proofread/__init__.py`, `jerome/proofread/languagetool.py`
- Test: `tests/proofread/test_languagetool.py`

**Interfaces:**
- Produces:
  - `LanguageToolClient(base_url: str, http: httpx.Client | None = None)` with `.check(annotation: list[dict], language: str) -> list[dict]`. It posts `data={"annotation": [...]}` and `language` to `/v2/check`;
  - `languagetool_candidates(blocks: Sequence[BlockText], style: StyleSheet, client: LanguageToolClient) -> tuple[list[Candidate], int]`, returning the candidates and the number of matches dropped.
- **Mapping** from `rule.issueType`:

  | issueType | Category | Severity |
  |---|---|---|
  | `misspelling` | typo | high |
  | `grammar` | grammar | high |
  | `typographical` | punctuation | medium |
  | `locale-violation` | consistency | medium |
  | `style`, `register` | style | low |
  | anything else | grammar | medium |

  The builder may refine the table (spec §8).
- Matches with no replacements are dropped. So are spelling matches whose text is in `style.ignore_words`.

- [ ] **Step 1: Write the failing tests**

```python
def test_placeholders_sent_as_markup(respx_mock):
    route = lt_route(respx_mock, matches=[])
    languagetool_candidates([BlockText(1, f"a{PLACEHOLDER}b")], STYLE_EN, client(respx_mock))
    ann = json.loads(parse_form(route)["data"])["annotation"]
    assert ann == [{"text": "a"}, {"markup": PLACEHOLDER, "interpretAs": " "}, {"text": "b"}]

def test_issue_types_map(respx_mock): ...  # misspelling → (typo, high); typographical → (punctuation, medium)
def test_ignore_words_filter_spelling(respx_mock): ...  # "Katharina" flagged misspelling, in ignore_words → dropped
def test_match_without_replacement_dropped(respx_mock): ...

def test_utf16_offsets_converted(respx_mock):  # Review Focus 2
    text = "😀 teh"  # LanguageTool reports offset 3 (UTF-16), Python index 2
    lt_route(respx_mock, matches=[lt_match(offset=3, length=3, repl="the", issue="misspelling")])
    c, _ = languagetool_candidates([BlockText(1, text)], STYLE_EN, client(respx_mock))
    assert (c[0].start, c[0].end, c[0].original) == (2, 5, "teh")

@pytest.mark.integration
def test_real_languagetool_finds_german_typo():  # needs docker compose languagetool
    c, _ = languagetool_candidates([BlockText(1, "Das ist ein Fehlr.")], STYLE_DE, LanguageToolClient(LT_URL))
    assert any(x.original == "Fehlr" for x in c)
```

- [ ] **Step 2: Run** `uv run pytest tests/proofread/test_languagetool.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Convert UTF-16 offsets to code points with a prefix table built from the annotated text. `markup` parts count toward offsets.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: LanguageTool pass"` (add new files).

### Task 13: AI pass and merging candidates

**Files:**
- Create: `jerome/proofread/ai_pass.py`, `jerome/proofread/merge.py`, `jerome/proofread/prompts/proofread_en.md`, `jerome/proofread/prompts/proofread_de.md`
- Test: `tests/proofread/test_ai_pass.py`, `tests/proofread/test_merge.py`

**Interfaces:**
- Consumes: `Provider`, `SystemPart`, `CompletionResult` (Task 11); `BlockText`, `Candidate`, `StyleSheet`.
- Produces:
  - `@dataclass Chunk(blocks: list[BlockText], context: BlockText | None)`;
  - `chunk_blocks(blocks: Sequence[BlockText], target_words: int = 2500) -> list[Chunk]`. Blocks are added in order. A chunk closes when adding the next block would take it over `target_words`, unless the chunk is still empty, so a single oversized block forms its own chunk. Each chunk's `context` is the block just before it;
  - `SUGGESTIONS_SCHEMA: dict`: `{suggestions: [{block_id:int, original:str, replacement:str, category:enum, severity:enum, explanation:str, occurrence:int≥1}]}`, with `additionalProperties: false`;
  - `build_request(style: StyleSheet, chunk: Chunk) -> tuple[list[SystemPart], str]`. The system parts are `[instructions (cache=True), style sheet text (cache=True)]`, and their content depends only on the language and the style sheet;
  - `parse_suggestions(data: dict, chunk: Chunk) -> tuple[list[Candidate], int]`;
  - `@dataclass AiPassResult(candidates: list[Candidate], dropped: int, result: CompletionResult)`;
  - `run_ai_pass(chunk: Chunk, style: StyleSheet, provider: Provider, model: str, effort: str | None) -> AiPassResult`.
- Produces: `merge_candidates(candidates: Sequence[Candidate]) -> list[Candidate]`.
  - Overlapping candidates in the same block keep the higher `severity.rank`.
  - On a tie, sources rank `model` > `languagetool` > `consistency`.
  - When span and replacement are identical, keep one candidate, using the model's explanation if there is one.

- [ ] **Step 1: Write the failing tests**

```python
def test_chunks_respect_target_and_carry_previous_block():
    blocks = [BlockText(i, " ".join(["w"] * 1000)) for i in range(6)]
    chunks = chunk_blocks(blocks)
    assert [len(c.blocks) for c in chunks] == [2, 2, 2] and chunks[1].context == blocks[1] and chunks[0].context is None

def test_system_prefix_identical_across_chunks():
    a, _ = build_request(STYLE_EN, Chunk([BlockText(1, "x")], None))
    b, _ = build_request(STYLE_EN, Chunk([BlockText(2, "y")], BlockText(1, "x")))
    assert a == b

def test_context_block_not_editable():
    c, dropped = parse_suggestions(reply(block_id=1, original="x", replacement="X"), Chunk([BlockText(2, "y")], BlockText(1, "x")))
    assert c == [] and dropped == 1

def test_drops_missing_original_placeholder_and_noop(): ...  # three bad suggestions (original not in block / span covers PLACEHOLDER / replacement == original) → [], dropped == 3
def test_occurrence_selects_nth_match():
    c, _ = parse_suggestions(reply(block_id=1, original="the", replacement="The", occurrence=2), Chunk([BlockText(1, "the cat and the dog")], None))
    assert (c[0].start, c[0].end) == (12, 15)

def test_tolerant_match_typographic_quotes():  # Review Focus 3
    text = "He said “hello”… and left."
    c, _ = parse_suggestions(reply(block_id=1, original='said "hello"...', replacement="said “Hello”…"), Chunk([BlockText(1, text)], None))
    assert text[c[0].start:c[0].end] == "said “hello”…" and c[0].original == "said “hello”…"

def test_run_ai_pass_uses_provider():
    fake = FakeProvider([reply(block_id=1, original="teh", replacement="the")])
    r = run_ai_pass(Chunk([BlockText(1, "teh")], None), STYLE_EN, fake, "m", "high")
    assert r.candidates[0].source == Source.MODEL and fake.calls[0]["schema"] == SUGGESTIONS_SCHEMA

# test_merge.py
def test_merge_prefers_higher_severity(): ...  # LT (0,3,high) vs model (0,7,low) → only the LT candidate
def test_merge_tie_prefers_model(): ...        # LT (0,3,medium) vs model (2,5,medium) → only the model candidate
def test_merge_identical_keeps_one(): ...      # same span+replacement from LT and model → one candidate, model's explanation
def test_merge_non_overlapping_keeps_all(): ...  # (0,3) and (4,7) → both, sorted by (block_id, start)
```

- [ ] **Step 2: Run** `uv run pytest tests/proofread/test_ai_pass.py tests/proofread/test_merge.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Tolerant matching:** build a normalized copy of the block text plus an index map back to the original offsets. Normalization folds `“”„«»‹›` → `"`, `‘’‚` → `'`, `…` → `...`, and any whitespace run (including NBSP) → one space. Normalize `original` the same way, search, and map the match back.
  - **Prompts:** the instruction prompts are the builder's choice (spec §8). They must:
    - tell the model to propose only real errors and style issues;
    - say that `original` must be copied exactly from the block;
    - list the categories and severities;
    - say the context block is read-only;
    - name the variant and the quote style from the style sheet.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: AI proofreading pass and candidate merging"` (add new files).

### Task 14: Consistency pass

**Files:**
- Create: `jerome/proofread/consistency.py`
- Test: `tests/proofread/test_consistency.py`

**Interfaces:**
- Produces: `consistency_candidates(blocks: Sequence[BlockText], style: StyleSheet) -> list[Candidate]`. Every candidate has source `consistency` and category `consistency`, except quote fixes, which are `punctuation`. Severities: spelling variants, house spellings and quotes are `medium`; near-duplicate names are `low`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hyphen_variant_minority_flagged():
    blocks = [BlockText(i, "Send an e-mail.") for i in range(3)] + [BlockText(9, "Send an email.")]
    c = consistency_candidates(blocks, STYLE_EN)
    assert [(x.block_id, x.original, x.replacement, x.severity) for x in c] == [(9, "email", "e-mail", Severity.MEDIUM)]

def test_sentence_initial_capital_is_not_a_case_variant():
    assert consistency_candidates([BlockText(1, "Spring came. It was spring.")] * 3, STYLE_EN) == []

def test_house_spelling_flagged():
    style = replace(STYLE_EN, house_spellings={"OK": ["okay", "O.K."]})
    assert consistency_candidates([BlockText(1, "It was okay.")], style)[0].replacement == "OK"

def test_wrong_quotes_for_de_DE():
    c = consistency_candidates([BlockText(1, 'Er sagte "Hallo".')], STYLE_DE)
    assert {(x.original, x.replacement) for x in c} == {('"', "„"), ('"', "“")}

def test_near_duplicate_names():
    blocks = [BlockText(i, "Then Katharina laughed.") for i in range(5)] + [BlockText(9, "Then Katarina laughed.")]
    (x,) = consistency_candidates(blocks, STYLE_EN)
    assert (x.original, x.replacement, x.severity) == ("Katarina", "Katharina", Severity.LOW)
    assert "5" in x.explanation and "1" in x.explanation

def test_names_seen_fewer_than_3_times_ignored(): ...  # "Katharina" ×1 + "Katarina" ×1 → []
def test_ignore_words_exempt_names(): ...              # Katharina ×5, Katarina ×1, ignore_words=["Katarina"] → []
```

- [ ] **Step 2: Run** `uv run pytest tests/proofread/test_consistency.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Hyphen, space and case variants:**
    - The grouping key is the lowercase word with hyphens and spaces removed. Hyphenated tokens and two-word sequences that join into a known closed form count.
    - The dominant form is the most frequent.
    - Words at the start of a sentence (use `split_sentences`) don't count as case variants.
  - **Quotes:** a quote character that isn't in `style.quote_style` is flagged. It becomes the opening quote when it follows a space, a line start or an opening bracket; otherwise the closing quote.
  - **Names:** capitalised words not at the start of a sentence, with total count ≥ 3, Levenshtein distance 1, and not in `ignore_words` (`ignore_words` exempts a name from both spelling and name checks).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: deterministic consistency pass"` (add new files).

### Task 15: Job estimate, create, cancel, resume

**Files:**
- Create: `jerome/jobs/__init__.py`, `jerome/jobs/estimate.py`, `jerome/jobs/service.py`, `jerome/api/routes_jobs.py`
- Test: `tests/jobs/test_service.py`, `tests/api/test_jobs_api.py`

**Interfaces:**
- Consumes: `block_texts`, `get_style_sheet` (Task 7); `chunk_blocks`, `build_request` (Task 13); `Registry`, `cost` (Task 11).
- Produces:
  - `PassName = Literal["languagetool","ai","consistency"]` and `ALL_PASSES = ("languagetool","ai","consistency")`.
  - `@dataclass CostRange(low: Decimal, high: Decimal)` and `@dataclass Estimate(chunks: int, per_pass: dict[str, CostRange], total: CostRange)`.
  - `estimate_job(session, registry: Registry, book_id: int, *, passes: Sequence[PassName] = ALL_PASSES, chapter_ids: Sequence[int] | None = None) -> Estimate`. Only chapters with `proofread=True` count; `languagetool` and `consistency` cost 0. For `ai`, per chunk:
    - input tokens come from `provider.count_tokens(...)`, or `ceil(chars / 3.5)`;
    - output = 0.15 × input;
    - `high` prices all input at the full input rate;
    - `low` prices the system prefix at the cache-read rate after the first chunk.
  - `create_job(session, registry, book_id, *, passes, chapter_ids, max_cost: Decimal, callback_url: str | None, actor: str) -> Job`. It stores the estimate and creates one `chapter` task per included chapter, plus one `consistency` task when that pass is selected. A second active job raises `Conflict`.
  - `cancel_job(session, job_id) -> Job` and `resume_job(session, job_id, *, max_cost: Decimal | None = None) -> Job`. Resume re-queues `failed` and `queued` tasks, resets `attempts`, and sets status `queued`.
- Routes:
  - `POST /v1/books/{id}/estimate` `{passes?, chapters?}`
  - `POST /v1/books/{id}/jobs` `{type: "proofread", passes?, chapters?, max_cost, callback_url?}` (`max_cost` required, > 0)
  - `GET /v1/jobs/{id}` → status, per-chapter `chunks_done`/total, `cost_actual`, `stats`
  - `POST /v1/jobs/{id}/cancel`, `POST /v1/jobs/{id}/resume` `{max_cost?}`

- [ ] **Step 1: Write the failing tests**

```python
def test_estimate_counts_chunks_and_15pct_output(session, registry_fake_counts_1000, book_6000_words):
    e = estimate_job(session, registry_fake_counts_1000, book_6000_words.id)
    assert e.chunks == 3 and e.per_pass["languagetool"] == CostRange(Decimal(0), Decimal(0))
    assert e.per_pass["ai"].high == 3 * cost(PRICE, Usage(1000, 150))

def test_estimate_falls_back_to_chars_per_token(session, registry_no_count, book): ...  # provider.count_tokens → None; 7,000 chars of input → 2,000 tokens priced
def test_skipped_chapters_not_estimated_or_tasked(session, registry, book_with_front_matter): ...  # proofread=False chapter: no chunks counted, no task
def test_tasks_per_chapter_plus_consistency(session, registry, book_3_chapters):
    job = create_job(session, registry, book_3_chapters.id, passes=ALL_PASSES, chapter_ids=None, max_cost=Decimal(5), callback_url=None, actor="a")
    assert sorted(t.kind for t in job.tasks) == ["chapter"] * 3 + ["consistency"]

def test_one_active_job_per_book(session, registry, book):
    create_job(session, registry, book.id, passes=ALL_PASSES, chapter_ids=None, max_cost=Decimal(1), callback_url=None, actor="a")
    with pytest.raises(Conflict):
        create_job(session, registry, book.id, passes=ALL_PASSES, chapter_ids=None, max_cost=Decimal(1), callback_url=None, actor="a")

def test_cancel_then_resume(session, registry, book): ...  # cancel → "cancelled"; resume(max_cost=10) → "queued", max_cost == 10, failed tasks requeued with attempts 0
```

API tests: `test_create_job_requires_max_cost_422`, `test_create_job_409_when_active`, and `test_get_job_progress`.

- [ ] **Step 2: Run** `uv run pytest tests/jobs/test_service.py tests/api/test_jobs_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. The app builds its `Registry` from settings once at startup. Tests override it with `FakeProvider`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: job estimates, creation, cancel and resume"` (add new files).

### Task 16: Worker (claiming, per-chunk checkpoints, retries, cost cap, consistency task, webhooks)

**Files:**
- Create: `jerome/jobs/worker.py`, `jerome/jobs/webhooks.py`
- Modify: `jerome/cli.py` (add the `worker` command)
- Test: `tests/jobs/test_worker.py`

**Interfaces:**
- Consumes: `languagetool_candidates` (Task 12); `chunk_blocks`, `run_ai_pass`, `merge_candidates` (Task 13); `consistency_candidates` (Task 14); `add_candidates` (Task 9); `Registry`, `cost`, `ModelRefusal` (Task 11).
- Produces:
  - `@dataclass WorkerDeps(registry: Registry, lt: LanguageToolClient, webhook_secret: str | None, now: Callable[[], datetime] = utcnow, sleep_base: float = 10.0)`;
  - `claim_task(session) -> int | None`. It takes the oldest `queued` task with `not_before <= now` whose job is `queued` or `running`, using `FOR UPDATE SKIP LOCKED`. A `consistency` task is claimable only once all its sibling chapter tasks are `done`. Claiming sets the task to `running` and the job to `running`;
  - `run_task(session_factory, task_id: int, deps: WorkerDeps) -> None`;
  - `work_forever(session_factory, deps, poll_interval: float = 1.0, stop: threading.Event | None = None) -> None`;
  - `send_webhook(url: str, event: str, payload: dict, secret: str | None) -> None`. Header: `X-Jerome-Signature: sha256=<hex HMAC of body>`. It makes 3 attempts, logs failures, and never raises.
- **Chunk loop** (each chunk is its own transaction):
  1. Reload the job. If it is `cancelled`, stop. If `cost_actual >= max_cost`, set `paused_cost_cap`, send `job.paused` and stop.
  2. Run the LanguageTool candidates for the chunk's blocks (when that pass is enabled).
  3. Run the AI pass (when enabled and `registry.for_pass("ai", local_only=book.local_only)` is not None; otherwise add `stats.ai_skipped_local_only += 1`). A `ModelRefusal` adds `stats.refusals += 1`, and the chunk continues with LanguageTool results only.
  4. `merge_candidates`, then `add_candidates`.
  5. Insert a `ModelUsage` row, add to `job.cost_actual` and `job.stats.dropped`, set `task.chunks_done += 1`, and commit.
  - Resuming skips the first `chunks_done` chunks.
  - On an exception: roll back the chunk, `attempts += 1`. Below 3 attempts, set `queued` with `not_before = now + sleep_base * 2**attempts`. At 3, set the task to `failed`, the job to `failed`, and send `job.failed`.
  - When every task is `done`: the job is `completed`, `finished_at` is set, and `job.completed` is sent.

- [ ] **Step 1: Write the failing tests**

```python
def test_commits_each_chunk(session_factory, deps_fake, job_two_chunks):
    run_task(session_factory, job_two_chunks.task_id, deps_fake)
    with session_factory() as s:
        t = s.get(JobTask, job_two_chunks.task_id)
        assert t.chunks_done == 2 and t.status == "done" and s.query(ModelUsage).count() == 2

def test_resume_continues_from_next_chunk(session_factory, job_three_chunks):
    deps = deps_with(FakeProvider([reply_ok(), RuntimeError("boom"), reply_ok(), reply_ok()]))
    run_task(session_factory, job_three_chunks.task_id, deps)            # chunk 1 ok, chunk 2 fails
    assert chunks_done(session_factory, job_three_chunks.task_id) == 1
    run_task(session_factory, job_three_chunks.task_id, deps)            # retry
    assert chunks_done(session_factory, job_three_chunks.task_id) == 3 and len(deps.registry_provider.calls) == 4

def test_failed_chunk_commits_nothing(...): ...  # provider raises on chunk 1 → 0 suggestions, 0 ModelUsage rows, chunks_done 0, attempts 1, not_before in the future
def test_three_failures_fail_job_and_send_webhook(respx_mock, ...): ...  # always raises; run 3× → task and job "failed"; one POST with event "job.failed"
def test_cost_cap_pauses_then_resume_continues(...):
    # max_cost below the cost of 2 chunks → paused_cost_cap after chunk 2; resume_job(max_cost=100) then run → completed
def test_cancel_stops_between_chunks(...): ...  # FakeProvider side effect cancels the job during chunk 1 → chunk 1 committed, chunks_done 1, provider called once
def test_consistency_task_waits_for_chapters(session_factory, job_two_chapters): ...  # claim_task never returns the consistency task while a chapter task is queued
def test_local_only_book_skips_ai(...): ...      # stats.ai_skipped_local_only == chunks; LanguageTool suggestions still saved
def test_refusal_counted_and_chunk_continues(...): ...
def test_completed_job_sends_signed_webhook(respx_mock, ...):
    req = respx_mock.calls[-1].request
    assert req.headers["X-Jerome-Signature"] == "sha256=" + hmac.new(b"s3cret", req.content, "sha256").hexdigest()

def test_two_workers_claim_different_tasks(session_factory, job_two_chapters):
    with session_factory() as a, session_factory() as b:
        assert claim_task(a) != claim_task(b)
```

- [ ] **Step 2: Run** `uv run pytest tests/jobs/test_worker.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. The `jerome worker` CLI command builds `WorkerDeps` from settings and calls `work_forever`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: job worker with per-chunk checkpoints and cost cap"` (add new files).

### Task 17: Short-text endpoint

**Files:**
- Create: `jerome/proofread/text.py`, `jerome/api/routes_text.py`
- Test: `tests/api/test_text_api.py`

**Interfaces:**
- Produces:
  - `@dataclass TextSuggestion(start, end, original, replacement, category, severity, source, explanation)`;
  - `proofread_text(session, deps: WorkerDeps, *, text: str, variant: Variant, passes: Sequence[PassName] = ("languagetool","ai"), quote_style: str | None = None, ignore_words: list[str] | None = None, local_only: bool = False, actor: str) -> list[TextSuggestion]`. It treats the text as one block (`BlockText(0, text)`), runs the passes, merges, and writes `ModelUsage` with `api_key_name=actor` and `job_id=None`;
  - `class TextLimits`: an in-memory per-key token bucket at `text_rpm`, plus a daily cap check (the sum of today's `ModelUsage.cost` for that key, UTC day).
- Route: `POST /v1/proofread/text`.
  - More than 5,000 characters → 422.
  - Over the daily cap or the rate limit → 429.

- [ ] **Step 1: Write the failing tests**

```python
def test_rejects_over_5000_chars(client, auth):
    assert client.post("/v1/proofread/text", headers=auth, json={"text": "a" * 5001, "variant": "en-US"}).status_code == 422

def test_returns_merged_suggestions(client, auth, fake_ai_and_lt): ...  # one LT + one AI candidate on the same span → one result
def test_daily_cap_429(client, auth, session): ...  # insert ModelUsage today for 'tester' with cost >= cap → 429
def test_rate_limit_429(client, auth, monkeypatch): ...  # text_rpm=1 → second call 429
def test_nothing_stored_except_usage(client, auth, session, fake_ai_and_lt):
    client.post("/v1/proofread/text", headers=auth, json={"text": "teh", "variant": "en-US"})
    assert session.query(Suggestion).count() == 0 and session.query(ModelUsage).count() == 1
```

- [ ] **Step 2: Run** `uv run pytest tests/api/test_text_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: synchronous short-text proofreading"` (add new files).

---

## Milestone 4 — Word round trip

### Task 18: Review copy export

**Files:**
- Modify: `jerome/core/exports.py` (implement `docx-review`), `jerome/api/routes_exports.py` (add the `min_severity`, `categories` and `chapters` query params)
- Test: `tests/core/test_review_export.py`

**Interfaces:**
- Consumes: `write_docx` with `DocxWriteOptions("tracked", ..., bookmarks, custom_properties)` (Task 3); `list_suggestions` (Task 9).
- Produces: `export_book(..., "docx-review", filters=ReviewFilters(...), actor=...)`. It does the following:
  - Changes = `open` suggestions matching the filters, plus every `accepted`/`edited` suggestion in the exported chapters.
  - Each change carries `ref=str(id)` and `comment=f"[{category} · {severity}] {explanation}"`.
  - Every block in the exported chapters gets the bookmark `_jb{block.id}`.
  - It inserts a `ReviewExport` with `suggestion_statuses={id: status}` for every suggestion included, `block_ids`, `filters` and `created_by`.
  - It sets the custom property `jerome_review_export_id = str(review_export.id)`.
  - The filename is `<title>-review-<export id>.docx`.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_copy_has_open_and_recorded_as_revisions(session, tmp_storage, docx_book_with_decisions):
    doc = part_xml(export_book(session, tmp_storage, docx_book_with_decisions.id, "docx-review", actor="a").data, "word/document.xml")
    assert doc.count("<w:ins ") == 3  # 2 recorded + 1 open

def test_comment_format(session, tmp_storage, docx_book_with_open_typo):
    f = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    assert "[typo · high] Misspelling." in part_xml(f.data, "word/comments.xml")

def test_filters_exclude_low(session, tmp_storage, docx_book_with_low_style): ...  # min_severity="medium" → no <w:ins> for the low suggestion; it stays "open" and is absent from suggestion_statuses
def test_bookmark_on_every_exported_block(session, tmp_storage, docx_book): ...  # set of _jb names == {f"_jb{id}" for every block in proofread chapters}
def test_review_export_row_and_property(session, tmp_storage, docx_book_with_open_typo):
    f = export_book(session, tmp_storage, docx_book_with_open_typo.id, "docx-review", actor="a")
    rx = session.query(ReviewExport).one()
    assert custom_props(f.data)["jerome_review_export_id"] == str(rx.id) and rx.suggestion_statuses == {str(sid): "open"}

def test_review_copy_only_for_docx(client, auth, epub_book_id): ...  # 422
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_review_export.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: DOCX review copy export"`.

### Task 19: Reviewed-DOCX reader

**Files:**
- Create: `jerome/documents/docx_review.py`, `tests/fixtures/word_sim.py`
- Test: `tests/documents/test_docx_review.py`

**Interfaces:**
- Consumes: `docx_xml` helpers (Task 2).
- Produces:
  - `@dataclass PendingRevision(start: int, end: int, kind: Literal["ins","del"], author: str, text: str)`;
  - `@dataclass ReviewedBlock(bookmark: str | None, text: str, pending: list[PendingRevision])`;
  - `@dataclass ReviewedDocument(blocks: list[ReviewedBlock], properties: dict[str, str])`;
  - `read_reviewed_docx(data: bytes) -> ReviewedDocument`. Here:
    - `text` is the **base view**: pending insertions removed, pending deletions kept as normal text, using the same placeholder rules as `paragraph_runs`;
    - pending deletions are spans in the base view;
    - pending insertions are zero-width (`start == end`) and carry their text;
    - `bookmark` is the `_jb…` bookmark that starts in the paragraph, or None;
    - blocks come in document order over the same parts as `iter_paragraphs`.
- Produces, in `tests/fixtures/word_sim.py`: `simulate_word_review(data: bytes, *, accept: Iterable[str] = (), reject: Iterable[str] = (), untracked_edits: Mapping[str, str] = {}, tracked_edits: Mapping[str, str] = {}, delete_paragraphs_containing: Iterable[str] = (), renumber_ids: bool = True, editor: str = "Editor") -> bytes`.
  - Jerome changes are selected by the text they insert (or, for a pure deletion, the text they delete). A selection applies to the whole change: both its `w:del` and its `w:ins`.
  - An untracked edit replaces the text everywhere in visible runs.
  - A tracked edit adds `w:del`/`w:ins` revisions by `editor`.
  - `renumber_ids` rewrites every `w:id` on revisions, comments and bookmarks, the way Word does on save.

- [ ] **Step 1: Write the failing tests**

```python
def test_plain_paragraph_has_no_pending():
    (b,) = read_reviewed_docx(make_docx(["plain"])).blocks
    assert (b.text, b.pending) == ("plain", [])

def test_pending_jerome_revision_positions():
    out = write_docx(make_docx(["teh cat"]), {"word/document.xml#0": [Change(0, 3, "the")]}, DocxWriteOptions("tracked", NOW))
    (b,) = read_reviewed_docx(out).blocks
    assert b.text == "teh cat"
    assert b.pending == [PendingRevision(0, 3, "del", "Jerome", "teh"), PendingRevision(3, 3, "ins", "Jerome", "the")]

def test_accepted_revision_becomes_plain_text():
    reviewed = simulate_word_review(tracked_teh_cat(), accept=["the"])
    assert read_reviewed_docx(reviewed).blocks[0].text == "the cat"

def test_editor_tracked_revision_author():
    reviewed = simulate_word_review(tracked_teh_cat(), tracked_edits={"cat": "dog"})
    assert {p.author for p in read_reviewed_docx(reviewed).blocks[0].pending} == {"Jerome", "Editor"}

def test_bookmark_survives_renumbering():
    opts = DocxWriteOptions("tracked", NOW, bookmarks={"word/document.xml#0": "_jb7"})
    reviewed = simulate_word_review(write_docx(make_docx(["x"]), {}, opts), renumber_ids=True)
    assert read_reviewed_docx(reviewed).blocks[0].bookmark == "_jb7"

def test_custom_properties_read(): ...  # written {"jerome_review_export_id": "42"} → properties == {"jerome_review_export_id": "42"}
def test_paragraph_without_bookmark_has_none(): ...  # bookmark is None
```

- [ ] **Step 2: Run** `uv run pytest tests/documents/test_docx_review.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: reviewed DOCX reader and Word simulator"` (add new files).

### Task 20: Review import (match, classify, editor changes, conflicts, report)

**Files:**
- Create: `jerome/core/review_imports.py`
- Modify: `jerome/api/routes_exports.py` (add the review-import routes)
- Test: `tests/core/test_classify.py`, `tests/core/test_review_import.py`

**Interfaces:**
- Consumes: `read_reviewed_docx` (Task 19); `decide`, `record_editor_change` (Task 9); ORM `ReviewExport`, `ReviewImport`.
- Produces:
  - `@dataclass SuggestionView(id: int, start: int, end: int, original: str, replacement: str)`.
  - `@dataclass EditorChange(start: int, end: int, original: str, replacement: str, pending: bool)`.
  - `@dataclass BlockOutcome(results: dict[int, tuple[Literal["unchanged","accepted","rejected","edited"], str | None]], editor_changes: list[EditorChange])`.
  - `classify_block(original: str, reviewed: ReviewedBlock, suggestions: Sequence[SuggestionView]) -> BlockOutcome`.
  - `match_blocks(expected: Sequence[tuple[int, str]], reviewed: Sequence[ReviewedBlock], threshold: float = 0.8) -> dict[int, ReviewedBlock]`. `expected` is `(block_id, original_text)` in export order. Matching by bookmark comes first. For unmatched blocks, try the reviewed paragraphs between the nearest matched neighbours: best `difflib.SequenceMatcher.ratio()` at or above the threshold.
  - `@dataclass ReviewImportReport(review_import_id, counts: dict[str, int], unmatched_block_ids: list[int], conflicts: list[int], notes: list[str])`. The `counts` keys are `accepted`, `rejected`, `edited`, `unchanged`, `editor_changes`, `conflicts`.
  - `import_review(session, book_id: int, data: bytes, actor: str) -> ReviewImportReport`.
- **Classification algorithm** (`classify_block`):
  1. Get the opcodes from `difflib.SequenceMatcher(None, original, reviewed.text, autojunk=False)`.
  2. Map an original offset to the base view. Inside an `equal` opcode, map linearly. Inside any other opcode, a span start maps to `j1` and a span end to `j2`.
  3. For each suggestion `[s, e)`, map it to `[ms, me]`:
     - any pending revision (any author) intersecting `[ms, me]`, zero-width ones included when `ms <= start <= me` → `unchanged`;
     - else `final = text[ms:me]`, then: equal to `replacement` → `accepted`; equal to `original` → `rejected`; anything else → `edited` with `final`.
  4. Expand each non-`equal` opcode to whole-word boundaries in both texts, and merge regions that touch or overlap after expanding. Each resulting region whose original range doesn't intersect any suggestion span becomes `EditorChange(i1, i2, original[i1:i2], text[j1:j2], pending=False)`. For example, "sat" → "sits" is one change, not two character edits.
  5. Each pending revision by a non-`Jerome` author becomes `EditorChange(pending=True)`, mapped back to original offsets the same way in reverse.
- **`import_review` steps:**
  1. Read the file.
  2. A missing `jerome_review_export_id`, or a `ReviewExport` for another book → `UnsupportedDocumentError` (422).
  3. Match blocks, then classify each matched block.
  4. For each suggestion whose result is not `unchanged`:
     - if its current status differs from `suggestion_statuses[str(id)]` → **conflict**: skip it and add it to `conflicts`;
     - else if the new status differs → call `decide(..., channel="word", review_import_id=…)`.
  5. Record the editor changes.
  6. Blocks with no match go into `unmatched_block_ids`, and their suggestions are left untouched.
  7. Store the `ReviewImport` with its report.
- Routes:
  - `POST /v1/books/{id}/review-imports` (multipart `file`) → report
  - `GET /v1/books/{id}/review-imports/{import_id}`

- [ ] **Step 1: Write the failing tests**

```python
# test_classify.py — pure function
S = SuggestionView(1, 0, 3, "teh", "the")
def test_accepted():  assert classify_block("teh cat", RB("the cat"), [S]).results[1] == ("accepted", None)
def test_rejected():  assert classify_block("teh cat", RB("teh cat"), [S]).results[1] == ("rejected", None)
def test_edited():    assert classify_block("teh cat", RB("The cat"), [S]).results[1] == ("edited", "The")
def test_pending_means_unchanged():
    rb = RB("teh cat", pending=[PendingRevision(0, 3, "del", "Jerome", "teh"), PendingRevision(3, 3, "ins", "Jerome", "the")])
    assert classify_block("teh cat", rb, [S]).results[1] == ("unchanged", None)
def test_untracked_editor_change():
    out = classify_block("teh cat sat", RB("the cat sits"), [S])
    assert out.editor_changes == [EditorChange(8, 11, "sat", "sits", pending=False)]
def test_editor_pending_revision(): ...  # RB("teh cat", pending=[del(4,7,"Editor","cat"), ins(7,7,"Editor","dog")]) → editor_changes == [EditorChange(4, 7, "cat", "dog", pending=True)]
def test_two_suggestions_independent(): ...  # S1 teh→the and S2 sat→sits in "teh cat sat"; reviewed "the cat sat" → S1 accepted, S2 rejected

# test_review_import.py — with DB
def test_full_simulated_review_with_renumbered_ids(session, tmp_storage, book_with_three_open):
    copy = export_book(session, tmp_storage, book_with_three_open.id, "docx-review", actor="a").data
    reviewed = simulate_word_review(copy, accept=["the"], reject=["sits"], untracked_edits={"dog": "hound"}, renumber_ids=True)
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts == {"accepted": 1, "rejected": 1, "edited": 0, "unchanged": 1, "editor_changes": 1, "conflicts": 0}
    assert {d.channel for d in session.query(Decision)} == {"word"}

def test_reupload_same_file_changes_nothing(...): ...       # second import: all counts 0 except unchanged, no new Decision rows
def test_deleted_paragraph_unmatched_and_untouched(...): ... # delete_paragraphs_containing=["sat"] → block id in unmatched_block_ids; its suggestion stays open

def test_stale_copy_conflict_not_overwritten(session, tmp_storage, book_with_three_open):  # Review Focus 4
    copy = export_book(session, tmp_storage, book_with_three_open.id, "docx-review", actor="a").data
    decide(session, first_open_id(session), "accept", actor="api-user")        # decided after the export
    reviewed = simulate_word_review(copy, reject=["the"])                          # Word rejects the same suggestion
    r = import_review(session, book_with_three_open.id, reviewed, actor="ed")
    assert r.counts["conflicts"] == 1 and session.get(Suggestion, first_open_id(session)).status == "accepted"

def test_upload_without_property_422(client, auth, docx_book_id): ...
def test_upload_for_other_book_422(...): ...  # review copy of book A uploaded to book B → 422, no Decision rows
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_classify.py tests/core/test_review_import.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: Word round trip review import"` (add new files).

---

## Milestone 5 — Packaging and evaluation

### Task 21: Docker stack, remaining CLI commands, end-to-end test, README

**Files:**
- Create: `Dockerfile`, `.env.example`, `README.md` (replace), `tests/test_end_to_end.py`
- Modify: `docker-compose.yml`, `jerome/cli.py`

**Interfaces:**
- **Compose services:**
  - `db` (postgres:16);
  - `languagetool` (a LanguageTool server image pinned to an exact tag, port 8010);
  - `api` (`jerome db-upgrade && jerome serve`);
  - `worker` (`jerome worker`).
  - The app image is built from `Dockerfile` (python:3.12-slim + uv), and `JEROME_DATA_DIR` is mounted as a volume.
- **CLI:**
  - `jerome db-upgrade` runs `alembic upgrade head`;
  - `jerome import PATH --variant V [--local-only]` prints the book id;
  - `jerome proofread BOOK_ID --max-cost N [--passes …]` prints the estimate, asks y/N, then creates the job;
  - `jerome export BOOK_ID --format F -o PATH`.

- [ ] **Step 1: Write the failing test**

```python
def test_end_to_end_with_fakes(client, auth, session_factory, fake_deps):
    book = client.post("/v1/books", headers=auth, files={"file": ("m.docx", make_docx(["# One", "teh cat sat."]))}, data={"variant": "en-US"}).json()
    assert client.post(f"/v1/books/{book['id']}/estimate", headers=auth, json={}).status_code == 200
    job = client.post(f"/v1/books/{book['id']}/jobs", headers=auth, json={"type": "proofread", "max_cost": "1"}).json()
    while (tid := claim_in(session_factory)) is not None:  # test helper: claim_task in its own session, then commit
        run_task(session_factory, tid, fake_deps)
    assert client.get(f"/v1/jobs/{job['id']}", headers=auth).json()["status"] == "completed"
    copy = client.get(f"/v1/books/{book['id']}/export", headers=auth, params={"format": "docx-review"}).content
    reviewed = simulate_word_review(copy, accept=["the"])
    assert client.post(f"/v1/books/{book['id']}/review-imports", headers=auth, files={"file": ("r.docx", reviewed)}).json()["counts"]["accepted"] == 1
    clean = client.get(f"/v1/books/{book['id']}/export", headers=auth, params={"format": "docx-clean"}).content
    assert "the cat sat." in [b.text for b in all_blocks(clean)]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_end_to_end.py -v`. Expected: FAIL until the CLI and wiring are complete, then PASS.
- [ ] **Step 3: Implement** the Dockerfile, compose services, CLI commands and README. The README covers the quickstart, env vars, the data directory, the public-repo warning, and how to run the integration tests.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS, with integration tests skipped.
- [ ] **Step 5: Run the full stack.**
  - Run `docker compose up -d --build && uv run pytest -m integration -q`. Expected: the LanguageTool and LibreOffice integration tests PASS (LibreOffice is skipped if it isn't installed).
  - Then curl `/v1/books` with a key created through `docker compose exec api jerome keys create smoke`. Expected: 200.
- [ ] **Step 6: Manual check** (spec §7.10). Open one review copy in Word for Mac and one in LibreOffice. Accept one, reject one, save, upload. The report counts must match what you did.
- [ ] **Step 7: Commit** `git commit -m "feat: docker stack, CLI, end-to-end test, README"` (add new files).

### Task 22: Seeded-error evaluation

**Files:**
- Create: `eval/__init__.py`, `eval/sources.toml` (Gutenberg ids: EN 1342 and 932, DE 22367 and 2229), `eval/fetch_texts.py`, `eval/inject.py`, `eval/score.py`, `eval/run.py`
- Test: `tests/eval/test_inject_score.py`

**Interfaces:**
- Produces:
  - `@dataclass InjectedError(paragraph: int, start: int, end: int, wrong: str, correct: str, category: Category)`;
  - `inject_errors(paragraphs: list[str], language: Literal["en","de"], *, seed: int, per_category: int) -> tuple[list[str], list[InjectedError]]`. The per-language catalogs:

    | Category | Errors injected |
    |---|---|
    | typo | swap, drop or double a letter |
    | punctuation | EN: drop a comma before "which" or after an introductory clause. DE: drop the comma before "dass", "weil" or "obwohl" |
    | grammar | EN: subject–verb agreement. DE: article gender or case |
    | consistency | spelling variants such as "e-mail"/"email" or "colour"/"color" (en-GB) |

  - `score(found: Sequence[Candidate], truth: Sequence[InjectedError], block_ids: list[int]) -> dict[str, dict[str, float]]`. It reports precision and recall per `(source, category)` and overall. A true positive is a candidate overlapping the error span whose replacement makes the span equal to `correct`.
  - `python -m eval.run --language en|de --passes … --max-cost N --seed 1`: prints the estimate, asks y/N, runs the passes directly (no DB), and writes `eval/results/<utc timestamp>.json` and `.md`.

- [ ] **Step 1: Write the failing tests**

```python
def test_inject_reproducible():
    assert inject_errors(PARAS_EN, "en", seed=1, per_category=2) == inject_errors(PARAS_EN, "en", seed=1, per_category=2)

def test_each_category_injected():
    _, truth = inject_errors(PARAS_DE, "de", seed=1, per_category=2)
    assert {t.category for t in truth} == {Category.TYPO, Category.PUNCTUATION, Category.GRAMMAR, Category.CONSISTENCY}

def test_score_precision_recall():
    truth = [InjectedError(0, 0, 3, "teh", "the", Category.TYPO)]
    found = [cand(0, 0, 3, "teh", "the", Category.TYPO), cand(0, 4, 7, "cat", "dog", Category.STYLE)]
    s = score(found, truth, block_ids=[0])
    assert s["overall"] == {"precision": 0.5, "recall": 1.0}
```

- [ ] **Step 2: Run** `uv run pytest tests/eval -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `fetch_texts.py` downloads plain-text Gutenberg files into `eval/data/` (gitignored), strips the license header and footer, and keeps paragraphs of at least 20 words.
- [ ] **Step 4: Run** `uv run pytest tests/eval -v`. Expected: PASS.
- [ ] **Step 5: Baseline run** (spends money: confirm the estimate first). Run `uv run python -m eval.fetch_texts && uv run python -m eval.run --language en --max-cost 3 --seed 1`, then the same with `--language de`. Expected: two result files; record the overall precision and recall in the README's evaluation section.
- [ ] **Step 6: Commit** `git commit -m "feat: seeded-error evaluation harness"` (add new files and the README change).
