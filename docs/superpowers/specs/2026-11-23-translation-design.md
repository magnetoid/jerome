# Translation (EN↔DE) as the first step of a project: design

Status: draft, written 2026-11-23, awaiting owner approval. Marked **(call)** = a decision Claude made that the owner has not confirmed; correct freely.

It builds on:
- the proofreading slice (`2026-11-21-proofreading-slice-design.md`), whose document model, job runner, suggestions and export this reuses;
- the web interface, MCP server and admin settings designs.

## Intent

Jerome was always meant to proofread **and translate** whole books, English↔German first. Proofreading is live. Translation comes next.

The owner sees translation as **one step of the same process, not a separate tool**: a book is translated first, and the translation then goes to proofreading. Some books arrive already translated and need only proofreading, so **creating a project says what Jerome should do with the book**.

```
Import (EN) → Translate (EN→DE) → Proofread (DE) → Review → Export (DE, same formatting)
```

## Goals

1. **The New project page asks what the work is:**
   - **Proofread only.** This is exactly today's flow, and existing projects stay as they are.
   - **Translate + proofread.** The page asks for the source language and the target language. Each is one of the existing variants: `en-US`, `en-GB`, `de-DE`, `de-AT`, `de-CH`. The source and target must differ in language, not only in variant. Both directions are supported, EN→DE and DE→EN. The quote style defaults to the target language's.

   The REST import endpoint (`POST /v1/books`) and the MCP import tool take the same choice.
2. **Translation runs as a job.** It goes chapter by chapter in the worker, like proofreading. It has:
   - a cost estimate before it starts;
   - a cost cap, entered at Start as it is for proofreading today (`max_cost`), which pauses the job when hit;
   - resume after a crash without redoing finished chapters;
   - progress on the project page.
3. **The translation is a new book** in the target language, linked to the source book. It has the same chapters and blocks, in the same order, and each translated block records the source block it came from.
4. **Proofreading is confirmed before it starts (the owner's choice).**
   - When the translation job finishes, the project page shows the proofreading estimate, computed on the translated text, and waits for Start with its own cost cap, as today.
   - Proofreading uses the translated book's style sheet: the target language variant and the quote style.
   - From there everything works as today: review in the web app or the Word round trip, auto-apply, decisions and export.
5. **Formatting survives translation.**
   - Italic, bold, links, superscript and the other marks move to the matching words of the translation.
   - Every placeholder (footnote references, images, fields) appears in the translation exactly once and in a sensible place.
   - A block whose placeholders don't come back intact is retried. If it still fails, the job is flagged and the block is marked for the editor. A block is never silently dropped.
6. **The original is visible during review.** On a translated book, the review page shows each block's source paragraph next to it.
7. **The AI proofreading pass on a translated book also sees the source paragraph.** **(call)** It may flag a meaning error, where the target text is fluent but says something different from the source, under a new category `translation`. This covers the case in which nobody on the team reads both languages.
8. **Export gives the book in the target language with the original's formatting.**
   - Export writes the translated text, with proofreading changes applied, into the structure of the **original file**. Styles, images, layout and front matter stay as they were.
   - It sets the document's language: `w:lang` in DOCX; `dc:language` and `xml:lang`/`lang` in EPUB.
   - **DOCX:** the clean DOCX, the change record and the review copy all work for a translated book. The review copy shows proofreading suggestions as tracked changes on the translated text.
   - **EPUB:** the clean EPUB.
9. **The project list and page show the process.**
   - A translate + proofread project appears **once** in the list, as the translated book with its language pair (e.g. `en-GB → de-DE`). The source book is reachable from it and is not a separate project. **(call)**
   - Two new stages come before `proofreading`:
     - **translating**, while the translation job runs;
     - **translated**, when it has finished and proofreading waits for Start. Flagged blocks are listed here.

     A translation job that fails or pauses at its cost cap shows `needs_attention`, as a proofreading job does.
10. **Models.**
    - Translation uses its own model pass, `translate`, editable on the Settings page like `ai`.
    - Its code default is `claude-opus-5-5`.
    - It respects the book's `local_only` switch the same way the AI pass does.

## Non-goals

- **Already-translated books with their original.** Aligning an existing translation to its original, checking it against the original, and translation memory are all out of scope. A book that arrives translated is imported on its own as **Proofread only**.
- **Learning from the owner's translated books.** The owner said translated books must not be used on this development machine. Tests use only the public Gutenberg fixtures (`tests/fixtures/public/pg932.epub`, English; `pg22367.epub`, German) and the test builders.
- A per-book translation glossary (source term → target term). Later. Names on the style sheet's ignore list are kept untranslated.
- Translating headers, footers, text boxes and comments. They are kept as they are, the same parts proofreading skips. **(call)**
- Free editing of a paragraph that has no suggestion, in the web review page. For DOCX books the Word round trip already covers it. For EPUB this is a gap, noted for later.
- Languages other than English and German.
- PDF output and shop publishing (the next projects).
- Changing a project's work after it is created. A wrong choice is fixed by deleting the project and creating it again, as with a bad chapter split today.

## Anti-goals

- **A translation must never lose text.** Every source block gets a translated block, or a visible flag saying it has none. A chapter whose output is shorter than expected, or which skips blocks, fails loudly. It never exports quietly with gaps.
- **The model is never asked to return structure it can break.** The model gets and returns text with inline mark tags and numbered placeholder tokens. Jerome validates them and maps them back. Jerome rebuilds the file and never trusts model-written XML.
- **The source book is never modified.**
- **No silent cost.** Translating a whole book on the default model is the most expensive thing Jerome does. The estimate is shown before it starts, and the cost cap applies.

## Approaches

The owner left the how to Claude, which chose **A**.

- **A. Translation creates a new, linked book in the target language (chosen).**
  - Proofreading, review, the Word round trip, auto-apply, decisions and export already work on a book, so they work on the translation unchanged.
  - The new pieces are: the translate job; the block link to the source; showing the source in review; and an export that writes the translated text into the original file's structure.
- **B. Translation as one whole-block suggestion per paragraph, in the same book.** Proofreading would then run over a mix of two languages, and each block would carry two layers of suggestions on top of each other. Rejected.
- **C. A separate translation layer on the same book.** Every service that reads block text would have to know which layer it reads: suggestions, review, export, review imports and MCP. About twice the work for the same result. Rejected.

## Design

### Storage (migration 0008)

- **`books`:**
  - `work` (`proofread` or `translate`);
  - `source_book_id`, nullable, pointing to the source book on a translated book;
  - the language pair, as the source book's language and the translated book's style sheet variant.
- **`blocks`:** `source_block_id`, nullable, pointing to the source block on a translated book's blocks.
- **`jobs` / `job_tasks`:** a job kind `translate`, and a task kind for a translated chapter.
- **The new book's chapters and blocks are created when the translation job is created**, with empty text, so they can be counted. A block is filled when its chapter is translated. **(call)**
- **`suggestions.category`:** gains `translation`.

### Translating a chapter

- Blocks go to the model in chunks, in order. Each chunk carries:
  - the previous source and translated paragraphs, for context;
  - the book's ignore list, for names kept as-is;
  - the target language variant and quote style.
- **Marks and placeholders are written as inline tags:**
  - a tag for each mark, e.g. `<i>…</i>`, `<b>…</b>`, `<a1>…</a1>` for a link whose href Jerome keeps;
  - a numbered token for each placeholder, e.g. `⟦1⟧`.

  Jerome checks that the reply has each block exactly once, by key, that the tags are balanced, and that every token appears exactly once. A block that fails is retried once on its own. If it still fails, it is stored with its source text and a flag.
- A chapter task is done when all its blocks are stored. Resume picks up at the first unfinished chapter.

### What gets translated (call)

- **Every chapter is translated, whatever its `proofread` flag.** That includes front and back matter: title page, copyright and the TOC page. A German book must not keep an English title page. The `proofread` flag still decides only what proofreading covers, and is copied to the new book.
- **TOC labels are rebuilt from the translated headings.**
  - EPUB: the nav document and the NCX labels are rewritten from the translated chapter headings at export.
  - DOCX: the cached text of the TOC field is not rewritten. The exported file asks Word to update its fields on open (`w:updateFields`).
- **Book title.**
  - The New project form has an optional **Translated title** field.
  - When it is filled, export writes it to the metadata (`dc:title`, or the DOCX core properties).
  - When it is empty, the original title is kept.

  The title of a translated edition is a publishing choice, so Jerome does not invent one.

### Blocks that could not be translated (call)

- **A flagged block keeps its source text, and proofreading skips it.** Target-language rules must not run over source-language text.
- **The project page lists flagged blocks.** Each has a **Translate again** action, which re-runs that one block with the same context. Its cost is counted on the translation job.
- **Export is refused while blocks are flagged.** The refusal lists the flagged blocks. The editor can retry them. For a DOCX book, the editor can instead mark a block as accepted as-is and fix it in the Word round trip. This keeps the "never lose text" anti-goal: no book leaves Jerome with an untranslated paragraph that nobody saw.

### The source book (call)

- **The source book is hidden and read-only.**
  - It does not appear in the projects list, in `GET /v1/books` or in the MCP book list.
  - It can be read by ID, and the translated book links to it.
  - Jobs, suggestions, decisions, review copies and exports on it are refused with a 409 that names the translated book.
- `POST /v1/books` with `work: translate` returns the **translated** book, so a client always works with the project.
- **Deleting the translated book deletes the source book too.**

### Export of a translated book

- Each translated block replaces the text of its source block in the original file.
  - **DOCX:** a run is rebuilt per mark span, copying the run properties of the source run that carried the same mark (or the paragraph's first run for plain text).
  - **EPUB:** the element's text nodes and inline elements are rebuilt from the marks the same way.
- Proofreading changes are applied to the translated text before it is written, as clean export does today.
- The review copy and change record are made on top of the translated base text.

### Web

- **New project:** the work choice and the two language selects.
- **Projects list:** the language pair, and the `translating` stage label.
- **Project page:** translation progress by chapter, the estimate and Start, and a link to the source book.
- **Review page:** the source paragraph beside each block on a translated book, collapsible on narrow screens.

### MCP and REST

- The import endpoint and tool take `work`, `source_language` and `target_language`.
- Starting the translation job uses the existing job endpoints and tools, with the new kind.

## Constraints

- The repo is public: no book text, owner hosts or deployment IDs in code, docs or tests.
- Tests use only the public fixtures and the test builders. Model calls in tests use the fake provider.
- The product default model stays `claude-opus-5-5`.
- Live model evaluation runs on demand, never in CI, because it costs money.

## Left to the builder

- Chunk size, the context window of earlier paragraphs, and the prompt wording.
- The exact inline tag syntax and its parser.
- How a mark is placed when the model drops or merges a tag. Keep the text; drop the mark rather than fail, and count it in the job summary.
- The cost-estimate formula for translation output length (German runs longer than English).
- Whether segments are used inside a chunk.
