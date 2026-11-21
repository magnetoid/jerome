# Jerome — Proofreading Slice Design

Date: 2026-11-21 · Status: draft, awaiting owner approval
Source: "Book Proofreading & Translation Engine — Architecture Proposal" (2026-11-21), plus decisions made in conversation.
Marked **(call)** = a decision Claude made that the owner has not confirmed; correct freely.

## 1. Intent

Jerome will proofread and translate whole books, English↔German first. Every change it proposes is a suggestion an editor accepts or rejects. Nothing is rewritten silently. It learns from every editor decision and, later, from books already translated.

This first slice builds **proofreading only, end to end through the REST API**:

- a DOCX or EPUB goes in;
- a job proposes fixes;
- an editor accepts, rejects or edits each one;
- the book comes back out with its formatting intact.

Why this slice first (chosen by the owner over "groundwork first" and "translation first"):

- It proves the hardest part, the file round trip with formatting intact, on the simplest AI job.
- Translation reuses all of it later: the document model, job runner, model layer, suggestion and decision store, and export.

Who uses it: **(call)** one in-house editorial team, one tenant, each person with their own API key. Not a product sold to many customers yet.

## 2. Goals

1. Upload a DOCX or EPUB manuscript in English or German.
2. Parse it into chapters → blocks (paragraph, heading, list item, quote, table cell, footnote) → sentence segments. Each gets an ID that stays fixed for that imported version, and inline formatting is kept.
3. Run a proofreading job asynchronously, one chapter at a time:
   - It shows a cost estimate before it starts and stops if a cost cap is hit.
   - It resumes after a crash without redoing finished chapters.
4. Produce suggestions. Each has a span in one block, a replacement, a category (`typo`, `grammar`, `punctuation`, `consistency`, `style`), a severity (`high`, `medium`, `low`), a source (`languagetool`, `model`, `consistency`) and a short explanation.
5. Let an editor list, filter, accept, reject or edit each suggestion, alone or in bulk. Store every decision permanently: original text, suggestion, final text, who, and when. This is the data the learning loop will use later.
6. Export:
   - DOCX input → a **review copy** (§7.3b); a **change record** with every accepted or edited change as a tracked change (author "Jerome"); or a clean DOCX with accepted changes applied.
   - EPUB input → a clean EPUB with accepted changes applied.
7. Keep a per-book style sheet:
   - language variant (`en-US`, `en-GB`, `de-DE`, `de-AT`, `de-CH`)
   - quote style (e.g. “…”, ‘…’, „…“, »…«, «…»)
   - house spellings (preferred form → variants to flag)
   - an ignore list (character and place names, invented words)
8. Offer a synchronous endpoint that proofreads short text (≤ 5,000 characters) for quick use by other tools.
9. **Word round trip (chosen by the owner).** Editors review in Word until the review screen exists:
   - Jerome exports a review copy in which every open suggestion is a tracked change with a comment explaining it.
   - Editors accept or reject in Word.
   - Uploading the reviewed file back records every decision, plus the editor's own changes, in the same decisions table the API uses.

## 3. Non-goals for this slice

Each of these comes later and has its own design:

- translation
- importing already-translated books, alignment, translation memory, vector search
- the MCP server
- the review web UI (shadcn, per the owner)
- Janus test skills
- fine-tuning
- InDesign, PDF and Markdown input
- re-importing a rewritten manuscript as a new version and matching it to the old one. This is not the same as uploading a reviewed review copy, which is in scope (§7.3b).
- a review copy for EPUB books. Until the review screen exists, EPUB books are reviewed through the API, or converted to DOCX first **(call)**.
- more than one tenant
- deployment to Tetra Host

## 4. Anti-goals: failure even if everything "works"

- **Silent rewrites.** The exported text may differ from the input only where an editor accepted or edited a suggestion.
- **Formatting loss.** An import followed by an export with no accepted changes must produce a document equivalent to the input: the same text, the same inline formatting (bold, italic, underline, small caps, links, footnote references) and the same structure.
- **Book text in the repo.** The GitHub repo is public. Manuscripts, uploads, exports and job data never go in it.
- **Noise.** Editors who must wade through hundreds of trivial style nits will stop using it. Every suggestion has a severity and category so the list can be filtered. Style suggestions are `low` by default.

## 5. Constraints

- **Stack** (from the proposal): Python, FastAPI, Postgres. **(call)** Use uv, Python 3.12, SQLAlchemy 2 with Alembic, pytest and ruff.
- **Long books.** A 100k-word book cannot go to a model in one request. The work splits by chapter, then by chunks of blocks.
- **Hosted vs. local models.** Whether manuscript text may be sent to hosted AI providers is not decided.
  - **(call)** Hosted is allowed by default.
  - Each book has a `local_only` flag. When it is set, AI passes use only providers marked local (e.g. Ollama). With no local provider configured, the AI pass is skipped and only the LanguageTool and consistency passes run.
- **Manuscript sources.** The program that produces the owner's files is unknown.
  - **(call)** Assume DOCX made in Word or Google Docs.
  - The EPUB reader must cope with cluttered markup, as in InDesign exports: it merges adjacent inline elements with identical formatting and ignores empty spans.
- **Test files.** No books from the owner's machine are used for now. Test fixtures are generated in code or taken from public-domain texts (Project Gutenberg, in English and German) fetched for the purpose.
- **File storage.** Uploads and exports are stored outside the repo, in `JEROME_DATA_DIR` (default `~/.local/share/jerome`). `.gitignore` excludes `*.docx` and `*.epub` everywhere except `tests/fixtures/public/`.

## 6. Approaches considered

| Approach | What it is | Trade-off |
|---|---|---|
| A. Groundwork first | DOCX/EPUB → segments → back out unchanged, with tests and no AI (proposal order) | A solid base, but nothing usable for a while |
| **B. Proofreading slice (chosen)** | DOCX/EPUB → LanguageTool + AI suggestions → accept/reject over the API → export with tracked changes | Builds only as much groundwork as needed and proves the round trip on the simplest AI job. Translation reuses everything |
| C. Translation slice | Book → EN↔DE translation in passes → review → export | Closest to the headline goal, but about twice the work before anything runs (book analysis, chunking, resumable jobs, cost estimates) |

## 7. Design

### 7.1 Layout

There is one core, with thin surfaces on top of it, as the proposal describes.

```
jerome/
  core/          # services: books, style sheets, jobs, suggestions, export — pure Python, no HTTP
  documents/     # document model + DOCX and EPUB readers/writers
  proofread/     # LanguageTool pass, AI pass, consistency pass, merging
  models/        # provider-agnostic model layer
  jobs/          # queue table, worker loop, checkpoints, cost estimate
  api/           # FastAPI routes → core services
  cli.py         # Typer: serve, worker, keys, import, proofread, export (thin, for ops and testing)
```

The REST routes and the CLI both call `core` services. The future MCP server will wrap the same services, so a job behaves identically whichever way it arrives.

### 7.2 Document model (the part everything rests on)

- **Hierarchy:** Book → Chapter → Block → Segment.
  - A **block** is a leaf text container: a paragraph, heading, list item, quote, table cell paragraph or footnote paragraph.
  - **Segments** are sentence spans inside a block, found with a sentence splitter that handles EN and DE. Proofreading anchors to blocks; segments exist because translation will need them.
- **Block text and marks:**
  - Each block stores its plain `text` and a list of `marks` (start, end, kind: bold, italic, underline, smallcaps, link with href, superscript, …).
  - A `source_ref` locates the block in the original file: the XML path of the `w:p` in DOCX, or the XHTML file plus element path in EPUB.
  - A content hash is stored for future version matching.
- **Non-text inline objects** become a single U+FFFC placeholder character in `text`. These are footnote and endnote references, images, fields, and tabs or breaks that carry layout. Suggestions may never cover a placeholder.
- **IDs** are assigned at import and stored. They are stable for that imported version. Re-importing a changed manuscript is out of scope.
- **Chapter detection (call):**
  - **DOCX:** a new chapter starts at each paragraph with outline level 0 (Heading 1, or a custom style with that outline level). With no such paragraphs, the book is split into parts of about 5,000 words at paragraph boundaries.
  - **EPUB:** chapters follow the top-level entries of the table of contents (EPUB 3 `nav`, or EPUB 2 NCX), mapped to spine files and anchors. That handles chapters split across files and files holding several chapters. With no usable TOC, fall back to one chapter per spine document.
  - **Front and back matter** (cover, title page, copyright, TOC, `linear="no"` items, and `epub:type` front/back matter) is imported but marked `proofread: false`. Any chapter's flag can be changed through the API.
  - **A bad split** is fixed by deleting the book and re-importing it, which is cheap before any job has run. There is no re-split operation in this slice.
- **Export patches the original file in place** rather than regenerating it. This is what guarantees formatting survives.
  - Only blocks with accepted or edited suggestions are touched.
  - Inside a block, runs (DOCX) or text nodes (EPUB) are split at change offsets.
  - Inserted text takes the formatting of the character immediately before it.
- **DOCX reading:**
  - Body paragraphs, tables and footnotes/endnotes are read.
  - Headers, footers, text boxes and comments are kept as-is but not proofread **(call)**.
  - A DOCX that already contains unresolved tracked changes is refused at import with a clear message asking for them to be accepted or rejected first **(call)**.
  - Use lxml on the package XML. python-docx can help read but does not handle revisions.
- **DOCX tracked-changes export:**
  - Each change becomes `w:del` (runs converted to `w:delText`) and/or `w:ins` with a copy of the source run properties, `w:author="Jerome"`, a date and a unique `w:id`.
  - The output must open cleanly in Word and LibreOffice.
- **EPUB:**
  - Chapters follow the spine order of XHTML documents.
  - Blocks are leaf block elements (`p`, `h1`–`h6`, `li`, `blockquote` without block children, `td`).
  - Inline elements (`em`, `strong`, `i`, `b`, `span`, `a`, `sup`, `sub`) become marks.
  - On export, the other files in the archive (`mimetype` first and uncompressed, OPF, images, CSS) are copied unchanged.

### 7.3 Suggestions and decisions

- A suggestion anchors to `(block_id, start, end)` **in the block's original imported text**. Block text is never mutated.
  - The "current text" is computed as the original plus accepted changes.
  - Open suggestions in the same block may not overlap, so every accepted change applies cleanly against the original.
- **Overlap:**
  - When suggestions are created, overlapping candidates from different passes are merged or deduplicated.
  - If one is still left overlapping, accepting a suggestion marks overlapping open ones `superseded`. This is not an editor rejection and is excluded from learning data.
- **Statuses:** `open`, `accepted`, `rejected`, `edited` (the editor wrote their own replacement for the span), `superseded`.
- The `decisions` table is append-only. It stores every accept, reject, edit and undo, with the actor (API key name) and timestamp. Undo returns a suggestion to `open` and adds a decision row; history is never rewritten.
- **Re-runs and already-decided suggestions (call):**
  - Each suggestion carries the `job_id` that produced it. Only one proofreading job per book can be active at a time.
  - A new suggestion is dropped if it:
    - overlaps a span that is already `accepted` or `edited` (it would not apply cleanly to "original + accepted");
    - has the same span and replacement as a suggestion that was `rejected` (this stops the same nit coming back);
    - overlaps a suggestion that is still `open`. The existing one stays, and new ones never replace it.
  - The consistency pass follows the same rules. It can therefore run after editors have started deciding on early chapters.
  - Editors can review while a job is still running, because each chunk's results are committed as soon as that chunk finishes (§7.6).

### 7.3b Word round trip

**Review copy** (`format=docx-review`):

- It is built by patching the original DOCX:
  - every `open` suggestion becomes a tracked change (author "Jerome");
  - already accepted or edited changes are applied as tracked changes too, so the editor sees the whole picture.
- Each Jerome change gets a Word comment in the form `[category · severity] explanation`, author "Jerome".
- Filters (`min_severity`, `categories`, `chapters`) keep the file from being flooded. Suggestions left out stay `open` in Jerome.
- Every exported block is wrapped in a hidden bookmark named `_jb<block_id>`. Word keeps bookmarks whose names start with `_` and hides them from the user. These bookmarks find the blocks again later, because Word renumbers revision and comment IDs when it saves, so those IDs can't be relied on.
- Each review copy is stored as a `review_exports` row recording which suggestions it contained.

**Reading a reviewed copy back** (`POST /books/{id}/review-imports`, multipart):

1. Match the upload to its `review_exports` row using a custom document property written at export.
2. Find each block by its bookmark. If a bookmark is missing, fall back to position plus a fuzzy text match.
3. For each block, read two things:
   - the **accepted view**: pending insertions dropped, pending deletions kept, i.e. the text as it was before the revisions still open;
   - which spans still carry pending revisions.
4. Align the block's original imported text with the accepted view (character-level diff), then classify each suggestion that was in the copy:
   - a pending Jerome revision still covers it → stays `open`;
   - the final text at its span equals the replacement → `accepted`;
   - it equals the original → `rejected`;
   - it is anything else → `edited`, with the final text taken from the document.
5. Differences that no suggestion covers are the editor's own changes. They are recorded as suggestions with source `editor`:
   - status `accepted` if already applied in the document;
   - status `open` if still pending as the editor's own tracked change.
   These are learning data: things Jerome missed.
6. Write decisions with `channel = "word"`. Only status changes are written, so uploading the same file again does nothing new, and a partly reviewed file can be uploaded more than once.
7. Return a report:
   - counts per outcome;
   - blocks that could not be matched (deleted, split or merged paragraphs), whose suggestions are left untouched;
   - structural edits Jerome cannot carry over.

**Limit (call):** Jerome's clean export patches the *original* file with recorded changes. Structural edits made in Word are not carried over: added, deleted, split or merged paragraphs, and formatting changes. They are listed in the import report. If editors restructure in Word, their Word file is the master and Jerome is only the record.

### 7.4 Proofreading passes

Each chapter runs these passes in order. A final book-level pass runs after all chapters.

1. **LanguageTool pass** (self-hosted LanguageTool server in a container; text stays local):
   - Each block is sent with the language variant from the style sheet. Ignore-list words are passed as disabled-spelling words.
   - LanguageTool rule categories map to Jerome categories and severities. Typos and grammar default to `high`, punctuation to `medium`.
   - Placeholder positions are masked.
2. **AI pass** (model layer, §7.5):
   - Chunks are about 2,500 words of consecutive blocks, plus the previous block as read-only context.
   - The prompt order is: fixed instructions → style sheet → chunk. This keeps the fixed prefix cacheable across every chunk of the book.
   - Each block is shown as `[block_id] text`.
   - Output comes back through structured outputs as `{suggestions: [{block_id, original, replacement, category, severity, explanation, occurrence}]}`.
   - The engine validates each suggestion:
     - the block exists;
     - `original` occurs in the block (`occurrence` picks which match);
     - it does not touch a placeholder;
     - it changes something.
   - Invalid suggestions are dropped and counted in job stats.
   - The AI pass focuses on things LanguageTool misses: style, awkward phrasing, wrong-word errors, agreement across clauses (German cases and genders), and tense consistency.
3. **Consistency pass** (deterministic, book-level, after all chapters):
   - Spelling variants that differ only by hyphen, space or case ("e-mail" / "email") and house-spelling variants from the style sheet. The minority form is flagged and the dominant form suggested.
   - Quote characters that don't match the style sheet.
   - Near-duplicate proper names: capitalised words seen at least 3 times that are one edit apart ("Katharina" / "Katarina"). Each minority spelling is flagged `low`, with the majority spelling as the replacement. The explanation gives both counts, so the editor can reject the suggestion or edit it to the other spelling.

### 7.5 Model layer

- **Interface:** `complete(pass_name, system, messages, json_schema) → {data, usage, model, provider}` and `count_tokens(...)`.
- Configuration maps each pass to a provider and model, so each pass can use a different model, as the proposal requires.
- **Claude adapter** (official `anthropic` Python SDK):
  - **(call)** The default model for the AI pass is `claude-opus-5-5`, set explicitly in config. The owner may choose a cheaper model per pass.
  - Uses adaptive thinking with an explicit effort setting (the default effort on this model is `medium`).
  - Uses structured outputs (`output_config.format` / `messages.parse`) for the suggestions schema.
  - Uses prompt caching on the fixed instructions + style sheet prefix.
  - Uses `messages.count_tokens` for estimates.
  - Handles `stop_reason == "refusal"` and enables server-side fallbacks.
- **OpenAI-compatible adapter** (covers Ollama and other providers). It sets the `local` flag when its base URL is local. Its code can be adapted from the adapters in the owner's Quorum repo (magnetoid/Quorum).
- **Prices** live in a config table: provider, model, input/output/cache-read price per million tokens. Every call records token usage and computed cost on the job.

### 7.6 Jobs

- **Tables:** `jobs` (book, type, status, estimate, cost cap, actual cost, optional webhook URL) and `job_tasks` (one per chapter, plus one `consistency` task; status, attempts, error, timings).
- **Worker** (`jerome worker`):
  - Claims tasks with `SELECT … FOR UPDATE SKIP LOCKED`.
  - Runs a chapter's passes chunk by chunk. **The checkpoint is per chunk (call):** each chunk's suggestions, its model usage and the task's `chunks_done` counter are committed in one transaction.
  - On resume or retry, the chapter continues from the next unfinished chunk. Paid-for chunks are never thrown away or bought twice.
  - A chunk that fails mid-call commits nothing, so retries are idempotent.
  - Each chunk runs the LanguageTool pass and the AI pass on the same blocks, merges their candidates (§7.4), and commits them together.
  - Retries up to 3 times with backoff, then marks the task `failed`. The job can be resumed later.
  - **(call)** Uses its own small queue table rather than a queue library, because checkpointing is tied to domain data.
- **Estimate:**
  - `POST /books/{id}/estimate` counts input tokens per chunk (exactly via the provider where available, otherwise about 3.5 characters per token).
  - Output is assumed to be 15% of input **(call; recalibrate from real jobs)**.
  - Returns low/high cost per pass.
- **Cost cap:**
  - Creating a job requires `max_cost`.
  - The worker stops starting new chunks once the actual cost reaches the cap, and the job ends `paused_cost_cap`. Raising the cap and resuming continues it.
- **Cancel and resume:**
  - The worker checks job status between chunks, so cancel takes effect then.
  - Resume re-queues tasks that are not `done`.
- **Webhook:** an optional `callback_url`. It receives `job.completed`, `job.failed` and `job.paused` with an HMAC signature header.

### 7.7 REST API (`/v1`, Bearer API key)

```
POST   /books                         multipart upload + language variant → book, chapters
GET    /books, /books/{id}, /books/{id}/chapters
GET|PUT /books/{id}/style-sheet
POST   /books/{id}/estimate           {passes?, chapters?} → cost estimate
POST   /books/{id}/jobs               {type:"proofread", chapters?, passes?, max_cost, callback_url?} → job
GET    /jobs/{id}                     status, per-chapter progress, cost so far
POST   /jobs/{id}/cancel | /resume
GET    /books/{id}/suggestions        filters: chapter, category, severity, source, status; paginated
GET    /books/{id}/blocks/{block_id}  original text, current text, its suggestions
POST   /suggestions/{id}/accept | /reject | /edit {replacement} | /undo
POST   /suggestions/bulk              {ids | filter, action}
GET    /books/{id}/export?format=docx-review|docx-tracked|docx-clean|epub-clean
                                      docx-review also takes min_severity, categories, chapters
POST   /books/{id}/review-imports     upload a reviewed review copy → import report
GET    /books/{id}/review-imports/{id}
POST   /proofread/text                synchronous, ≤ 5,000 chars, {text, variant} → suggestions (nothing stored)
```

- **`/proofread/text` (call):**
  - It runs LanguageTool and the AI pass on the configured default model by default. The request can narrow the passes (`passes`) and set `variant`, `quote_style`, `ignore_words` and `local_only`.
  - Cost is limited by a daily cap per API key (`JEROME_TEXT_DAILY_COST_CAP`) and a requests-per-minute limit per key. Once over the cap, the endpoint answers 429.
  - Nothing is stored except the model usage rows.
- API keys are created with `jerome keys create <name>` and stored hashed. The key name is the actor recorded on decisions.
- The OpenAPI schema is generated by FastAPI.

### 7.8 Data model (Postgres)

`books`, `chapters`, `blocks` (text, marks jsonb, source_ref jsonb, hash), `segments`, `style_sheets`, `jobs`, `job_tasks`, `suggestions`, `decisions` (with `channel`: `api` or `word`), `review_exports` (the suggestions each review copy contained, and its filters), `review_imports` (the report from each upload), `api_keys`, `model_usage` (one row per model call: job, pass, provider, model, tokens, cost). Migrations use Alembic.

### 7.9 Running it

`docker compose up` brings up Postgres, LanguageTool, the API and the worker. Configuration comes from environment variables (`DATABASE_URL`, `JEROME_DATA_DIR`, `LANGUAGETOOL_URL`, provider keys, and `JEROME_MODELS` mapping pass → provider/model).

### 7.10 Testing and evaluation

- **Round-trip tests (the gate for everything else):**
  - Fixtures are generated in code (python-docx plus raw XML for footnotes, tables, links and mixed formatting; small hand-built EPUBs), plus one short public-domain EN and one DE text from Project Gutenberg.
  - Import → export with no changes → the text, marks and structure must be equal.
  - Import → accept known changes → export → the expected text, with untouched blocks byte-identical in XML.
- **Tracked-changes validity:**
  - The exported DOCX re-parses, and LibreOffice headless converts it without errors (integration test).
  - It also gets a one-time manual check in Word.
- **Word round-trip tests:**
  - Build a review copy, then simulate an editor in code: accept some Jerome revisions, reject some, leave some pending, add an untracked edit and a tracked edit of their own, delete one paragraph, and renumber every `w:id` the way Word does on save.
  - Upload the result and check every outcome and the report.
  - Before this slice counts as done, do one manual pass in real Word (Mac) and one in LibreOffice.
- **Unit tests** cover the offset ↔ run mapping, overlap rules, validation of AI suggestions, the consistency checks, and cost arithmetic. The model layer is tested with a fake provider. LanguageTool tests run against the container under an `integration` marker.
- **Seeded-error evaluation set:**
  - Clean public-domain EN and DE paragraphs get known errors injected programmatically, per category, with the answers recorded.
  - Measures precision and recall per pass and category.
  - It runs on demand, not in CI, because it costs money.
  - It becomes the baseline that every later prompt or model change must beat, which is the "test set" rule from the proposal applied to proofreading.

## 8. Left to the builder

- the sentence-splitter library, chunk size tuning and prompt wording
- the exact mapping from LanguageTool rules to Jerome categories and severities
- the dedup rule between passes when they flag the same span (start with: prefer higher severity, tie goes to the AI pass; tune on the seeded evaluation set)
- logging, retry backoff details and pagination style
- the diff algorithm and fuzzy-match threshold for review imports, tuned on the round-trip tests

## 9. Open with the owner (not blocking this slice)

- Can manuscript text go to hosted AI providers, or must some books stay on your servers? The design assumes hosted by default, with a per-book `local_only` switch.
- Which program produces the manuscripts? The design assumes Word or Google Docs DOCX.
- Which way do the English–German books go? This doesn't affect this slice. It matters for translation.
