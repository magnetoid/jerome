# Jerome

Jerome is a book proofreading engine. It imports a manuscript (DOCX or EPUB), finds
typos, grammar, punctuation and consistency problems with LanguageTool and an AI model, and
returns the result as a Word file with tracked changes and comments, a clean copy, or a
clean EPUB. English and German (en-US, en-GB, de-DE, de-AT, de-CH) are supported.
Everything is reachable through a REST API and a small CLI.

## Quickstart (Docker)

```bash
cp .env.example .env          # then set ANTHROPIC_API_KEY in .env
docker compose up -d --build
docker compose exec api jerome keys create me   # prints a key, shown once

curl -H "Authorization: Bearer jk_..." http://127.0.0.1:8000/v1/books
```

The stack runs Postgres, LanguageTool, the API (port 8000, migrations run on start) and a
background worker. The API and LanguageTool are bound to 127.0.0.1 only. The interactive API
docs are at http://127.0.0.1:8000/docs.

`docker/postgres-init` (which creates the `jerome_test` database) runs only when the
`jerome-db` volume is first created. To re-run it, remove the volume with
`docker compose down -v`, which also deletes the database.

## Deploying on a server

The compose stack is also how Jerome runs on a server. Before the first `up`, set these in `.env`:

- `ANTHROPIC_API_KEY`.
- `POSTGRES_PASSWORD`: a long random password of letters and digits
  (`openssl rand -hex 24`). Postgres applies it only when the `jerome-db` volume is first
  created, so changing it later means changing it inside Postgres too.
- `JEROME_WEBHOOK_SECRET`, if you use job webhooks.

Then:

```bash
docker compose up -d --build
docker compose exec api jerome keys create NAME     # one key per person or client
```

- **HTTPS.** Every port is bound to 127.0.0.1. Put a TLS reverse proxy (Caddy, nginx,
  Traefik, Coolify, …) in front of `127.0.0.1:8000` instead of opening the port: API keys
  travel as bearer tokens.
- **Memory.** LanguageTool wants about 2 GB of RAM.
- **Backups.** The `jerome-db` volume holds books, suggestions and decisions; `jerome-data`
  holds the uploaded originals and exports. Back up both.
- **Updating.** `git pull && docker compose up -d --build`. Migrations run when the API
  starts, and the worker finishes its current chunk before it stops.

## Web interface

Jerome serves a web app from the same container as the API, at `/`. Members sign in with an
email and password, create a project by uploading a DOCX or EPUB, start a proofreading job,
watch its progress and review the result in the browser. Clear-cut changes (typos, punctuation
and house-style rules) can be applied automatically by the AI while proofreading runs; every
such change is marked as the AI's and can be reverted in one step. Exports (Word with
comments, Word with tracked changes, clean Word, clean EPUB) are on the project page. The API
and its bearer keys work as before, except team management (`/v1/users`), which needs a
super admin signed in to the web interface.

**Asking the AI about a LanguageTool mark.** LanguageTool often marks text that is fine
(names, dialogue, the author's style). On the review card of a mark LanguageTool made alone,
**Ask AI** asks the proofreading AI model (`models.ai`) whether the mark is right. The AI
reads the mark's paragraph with at least three sentences before and after it, taken from the
neighbouring paragraphs of the same chapter when the paragraph is short, and it weighs every
option LanguageTool offered (up to five, listed on the card; clicking one accepts the mark
with it). It answers with one of three ratings, shown in colour with its reason: "definitely
an error" with its fix (one of LanguageTool's options or its own text), which becomes the
main button; "maybe an error" with the fix it would pick, and no main button; or "definitely
not an error", which makes Reject the main button. Nothing is accepted or rejected by the
check itself, and the rating is kept with the suggestion. **Check LT marks in this
chapter**, in the review toolbar, asks about up to 40 unchecked marks per click, one call
per paragraph; it stops starting new paragraphs after about 40 seconds and says how many are
left. Each check is a paid model call, billed to the proofreading job and counted against
the per-minute text rate limit. The check is refused while a job runs on the book.
Over MCP the same check is the `ai_check_suggestion` tool.

**Remember for all books.** When the AI says a mark is definitely not an error, the card
offers **Remember for all books**. Jerome saves the word with the AI's reason, for the book's
language, and LanguageTool checks in every book of that language stop marking it: a
misspelling under any spelling rule, any other mark only under its own LanguageTool rule.
The mark and the book's other open marks of the word are rejected as your decisions. The
words are listed under **Learned words** on the Settings page, where the super admin can
delete one; its rejected marks stay rejected. The API is `POST
/v1/suggestions/{id}/remember`, `GET /v1/learned-words` and `DELETE /v1/learned-words/{id}`.

**First login.** There is no sign-up. Create each member once, inside the API container:

```bash
docker compose exec api jerome users create editor@example.com --name "Editor"
```

It prints a temporary password. The member must change it at first login, and cannot use
anything else in the app until they do. On Coolify, add a scheduled task on the `api`
container with that command, run it once, read the temporary password from the task's
execution log, then delete the task.

The first account becomes the super admin. Only super admins manage the team, in the web
Team page or with `--role` (`super_admin` or `member`). If no one can reach the Team page,
`jerome users set-role EMAIL super_admin` restores access. If the only super admin lost
their password, create a new account with `jerome users create … --role super_admin`.

**Behind a reverse proxy.** Put a TLS reverse proxy in front of the container and set
`JEROME_TRUSTED_PROXIES` to the address the proxy connects from (for example the Docker
network gateway), as a JSON list: `JEROME_TRUSTED_PROXIES='["172.17.0.1"]'`. Only then is
`X-Forwarded-For` believed, so that login throttling sees real client addresses.

**Login throttling.** Five failed logins within 15 minutes block that key for 15 minutes; the
response is 429 with a `Retry-After` header. Failures are counted per email address and, behind
a trusted proxy, per client IP. Trade-offs: someone who knows a member's email can lock that
account out for the window; people behind one shared NAT share one per-IP budget; and without
`JEROME_TRUSTED_PROXIES` every request appears to come from the proxy, so only the per-email
limit applies. The counters live in memory, in one process.

**Serve at the root of the domain.** Do not mount Jerome under a path prefix (an ASGI
`root_path`): the password-change gate matches on `request.url.path`, so under a prefix it
fails closed and members would be locked out of the app.

**Developing the web app.** Run the API with `uv run jerome serve` and, in `web/`,
`npm run dev`. Open the Vite URL it prints (http://localhost:5173); it proxies `/v1` to the
API on port 8000. `npm test` runs the unit tests and `npm run build` makes the production
bundle the API serves. The session cookie is Secure: for plain-http local use set
`JEROME_COOKIE_SECURE=false`.

## Local development

```bash
uv sync
docker compose up -d db languagetool                   # Postgres on 5433, LanguageTool on 8010
uv run jerome db-upgrade
uv run jerome serve                                    # API
uv run jerome worker                                   # in a second terminal
export JEROME_TEST_DATABASE_URL=postgresql+psycopg://jerome:jerome@localhost:5433/jerome_test
uv run pytest -q
```

CLI: `jerome keys create NAME`, `jerome db-upgrade`, `jerome import PATH --variant en-US
[--local-only]`, `jerome proofread BOOK_ID --max-cost 5 [--passes ai]` (prints the estimate and
asks before creating the job), `jerome export BOOK_ID --format docx-review -o review.docx`, `jerome publish BOOK_ID
--format pdf-book -o book.pdf` (makes the finished file here, without a job, and prints its
warnings).

## Configuration

Settings are read from `JEROME_*` environment variables and from `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `JEROME_DATABASE_URL` | `postgresql+psycopg://jerome:jerome@localhost:5433/jerome` | SQLAlchemy URL of the Postgres database |
| `JEROME_DATA_DIR` | `~/.local/share/jerome` | Where uploaded originals and exports are stored |
| `JEROME_LANGUAGETOOL_URL` | `http://localhost:8010` | LanguageTool server |
| `JEROME_MODELS` | `{"ai": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}}` | JSON: model per pass |
| `JEROME_PRICES` | `{}` | JSON: per-model price overrides |
| `JEROME_WEBHOOK_SECRET` | unset | Signs job webhooks when set |
| `JEROME_TEXT_DAILY_COST_CAP` | `5` | Daily cost cap per API key for the short-text endpoint |
| `JEROME_TEXT_RPM` | `30` | Requests per minute per key for the short-text endpoint |
| `JEROME_MAX_UPLOAD_MB` | `50` | Maximum upload size |
| `JEROME_SECRET_KEY` | unset | Fernet key that encrypts the secrets saved on the Settings page (see below). Lives only in the environment, never in the database |
| `JEROME_PUBLIC_BASE_URL` | unset | Public address of Jerome (e.g. `https://jerome.example.com`) for the file links MCP tools return; unset, the request's own address |
| `ANTHROPIC_API_KEY` | unset | Anthropic API key (no `JEROME_` prefix). Read from the environment or `.env`, so local runs find it too; never logged |

## Settings page

Super admins can open Settings in the web app to change the model, prices, the daily text
cost cap, the text rate limit, the upload limit, the session length, the LanguageTool
address and the public address without a restart, and to save the Anthropic key, the
webhook secret and the keys for the other model providers, DeepL and LanguageTool Premium (see "Choosing services"). The same page creates and revokes API keys
and lists the latest changes. A value saved there wins over the environment, which wins over
the default; "Reset" goes back to the environment value. Changes reach the API and the
worker within about 10 seconds.

Secrets are stored encrypted, so a database backup alone does not reveal them. This needs
`JEROME_SECRET_KEY` in the environment. Make one with:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The API and the worker both need the same `JEROME_SECRET_KEY`.

Without it you can still clear a secret, but not save one. If the key is lost or changed,
the saved secrets cannot be read: Jerome falls back to the environment values and the page
marks them, so enter them again.

## Data directory

Originals and exports live under `JEROME_DATA_DIR` (a Docker volume mounted at `/data` in the
stack). They are manuscripts: back them up, and never put them in this repository.

## Models and cost

The default AI model is `claude-opus-5-5`. Before a job starts, Jerome estimates its cost
(`POST /v1/books/{id}/estimate`, or the CLI prompt). Every job needs a `max_cost`; when the
spend reaches it, the job pauses as `paused_cost_cap` and can be resumed with a higher cap.
A book imported with `local_only` is never sent to a remote model.

The estimate's output figure does not yet include adaptive-thinking tokens. It will be
calibrated from the first evaluation run; the cost cap still bounds the actual spend.

### Choosing services

On the Settings page, the proofreading model and the translation model each pick a
provider. Each provider's key goes under Secrets. Saving a key fetches that provider's model
list to check it ("Key works: 42 models"), and the Model field then offers the live list (an
own server's list comes from its Base URL, with Load models). The key stays on the server.

| Provider | Used for | Key |
| --- | --- | --- |
| Anthropic (Claude) | proofreading, translation | `anthropic_api_key` |
| OpenAI | proofreading, translation | `openai_api_key` |
| Google Gemini | proofreading, translation | `gemini_api_key` |
| Mistral | proofreading, translation | `mistral_api_key` |
| OpenRouter (open-weight models: Llama, Qwen, DeepSeek, Mistral, Gemma) | proofreading, translation | `openrouter_api_key` |
| xAI (Grok) | proofreading, translation | `xai_api_key` |
| Own server / other (Ollama, vLLM, any OpenAI-compatible URL) | proofreading, translation | `openai_compat_api_key`, optional |
| DeepL | translation only | `deepl_api_key` (a key ending `:fx` uses DeepL Free) |

- Model names are typed. The page suggests well-known ones.
- A model with no built-in price needs one under Prices before a job can be estimated.
- For DeepL, the `deepl` price row holds the price per million characters in `input`, with 0 in the other fields.
- The prompts are tuned for Claude. Other models may break formatting tags more often, and those paragraphs are retried and then flagged.
- DeepL keeps formatting and the ignore list, but not the house style (quote marks). Run proofreading after it.
- Only an own server on a local address may process a `local_only` book.
- A key can also come from the environment: put `api_key` beside its provider in `JEROME_MODELS`. A key saved on the Settings page wins.

**LanguageTool Premium.**
1. Set the LanguageTool URL to `https://api.languagetoolplus.com`.
2. Fill in the LanguageTool Premium username.
3. Save the LanguageTool Premium API key under Secrets.

Checks then use your Premium account. Jerome waits and retries when Premium answers "too many requests".
A `local_only` book is not sent to LanguageTool's hosted service; its LanguageTool check is skipped and the job says so.

## Stopping and crashes

`jerome worker` handles SIGTERM and SIGINT: it finishes the chunk it is on, commits it, puts
the task and job back in the queue and exits (the compose file gives it 180 s for that). A
restarted worker carries on from the next chunk.

If a worker crashes or is killed, its job stays `running` and cannot be resumed while it says
so, because a live worker might still hold it. Cancel the job
(`POST /v1/jobs/{id}/cancel`), then resume it (`POST /v1/jobs/{id}/resume`); committed chunks
are kept and not paid for twice.

## PDF import

A PDF becomes a normal book: upload it on the New project page, with `POST /v1/books`, or with
`jerome import book.pdf --variant en-GB`. The book appears at once with the status
"importing" while a background job reads the pages and builds the manuscript; when it is done
you proofread or translate it like any DOCX. PDFs are limited to 2000 pages and
`max_pdf_upload_mb` (200 MB by default); DOCX and EPUB keep `max_upload_mb`.

**Engines.** Pages with a text layer are read directly, at no cost. Scanned pages are read by
one of two engines:

- *AI model*: the model set for the `ocr` pass (Settings → Models, "PDF page reading
  model"); until one is set, the `ai` pass's model ("Same as AI check"). It is the default
  whenever such a model is set up and the book is not local-only.
- *Tesseract*: a local reader, free and private. It is the default when no model is set up,
  and the only engine for a local-only book (the API refuses `ocr_engine=ai` for one with
  422). Choose it with the `ocr_engine` field, or `--ocr tesseract` on the command line.

**Estimate and confirm.** Nothing is spent on AI reading before you confirm. When the scanned
pages are known, the import pauses with an estimate of the AI cost; confirm it with a maximum
cost (`POST /v1/books/{id}/import/confirm` with `max_cost`), or use Tesseract instead
(`POST /v1/books/{id}/import/use-tesseract`). When the AI reading reaches the maximum,
Tesseract reads the remaining scanned pages and the import finishes; the import report
says how many.

**Re-OCR scanned pages** (`reocr`, on by default): a scanned page that already has a text
layer is read again by the engine, because the layer of a scan is often poor. Turn it off to
trust such a layer.

**Import report.** When the import is done, the book carries a report (`import_report`): how
many pages came from the text layer, from the AI model (`engine_pages.ai`) and from Tesseract
(`engine_pages.tesseract`), which pages could not be read (`failed_pages`, 1-based), which were
blank (`blank_pages`), and why the AI handed pages to Tesseract (`ai_fallbacks`). A failed
import shows its reason in `import_error`.

Through Janus (MCP) a PDF cannot be imported yet; upload it on the New project page.

## Word round trip

1. Import the manuscript and run a proofreading job.
2. Export `docx-review`: a copy with Jerome's suggestions as tracked changes and comments.
3. In Word (or LibreOffice) accept or reject changes, and edit freely. Save as DOCX.
4. Upload the file to `POST /v1/books/{id}/review-imports`. Jerome records your decisions.
5. Export `docx-clean` (or `docx-tracked`) for the final text.

## Translation

Creating a project says what Jerome should do with the book: **Proofread only**, or
**Translate and proofread** between English and German (any of en-US, en-GB, de-DE, de-AT,
de-CH; the two must be different languages). Over REST, `POST /v1/books` takes
`work=translate`, `variant` (the file's language), `target_variant` and an optional
`translated_title`; the MCP `import_book` tool takes the same arguments.

1. **Import.** The file becomes a hidden, read-only source book. The answer is the
   translated book: same chapters and paragraphs, still empty. Work on that one; the source
   is not listed, and jobs, settings, exports and deletes on it get a 409 that names the
   translated book. Deleting the translated book deletes the source too.
2. **Translate.** Estimate and start a job with `type: translate`
   (`POST /v1/books/{id}/estimate` and `/jobs`, or the MCP `estimate_job` and `start_job`).
   It translates every chapter, front matter included, and keeps formatting, links, notes
   and images in place.
3. **Proofread.** When the translation is done, estimate and start a proofreading job as
   usual. It uses the translated book's style sheet, and the AI pass sees each paragraph's
   source, so it can flag a meaning error under the category `translation`.
4. **Review and export** as for any book. The export writes the translation, with the
   accepted changes, into the original file's structure and sets the target language. The
   review copy and the Word round trip work on the translation. Word asks to update the
   document's fields when it opens an exported translated DOCX: that rebuilds the table of
   contents from the translated headings.

**Stages.** A translated book shows `set_up` until its translation starts, `translating`
while the job runs, then `translated` until proofreading starts; from there the stages are
the usual ones. A translation job that fails or pauses at its cost cap shows
`needs_attention`.

**Flagged paragraphs.** A paragraph whose translation cannot be used (its marks, links or
placeholders did not come back intact, even on a retry) keeps its source text and is
flagged; proofreading skips it. `GET /v1/books/{id}/translation` (MCP `get_translation`)
lists them. Translate one again with `POST /v1/blocks/{id}/translate` (MCP
`retranslate_block`, a paid call billed to the translation job), or, on a DOCX book only,
accept it untranslated with `POST /v1/blocks/{id}/keep-source` (MCP `keep_source_block`) and
fix it in Word. Export is refused while any paragraph is pending or flagged.

**Chapter titles.** An EPUB chapter's title is its table-of-contents label. When that label
is its first heading's text, it follows the heading's translation; otherwise (a label that
differs from the heading, or front matter with no heading) the job translates it on its
own. A title that cannot be translated keeps its source text, in the exported table of
contents too, and `GET /v1/books/{id}/translation` lists its chapter in
`untranslated_titles`.

**Model.** Translation has its own model pass, `translate`, set in `JEROME_MODELS` like `ai`
(for example `{"ai": {...}, "translate": {"provider": "anthropic", "model":
"claude-opus-5-5", "effort": "high"}}`). Its default is `claude-opus-5-5`, and a
`local_only` book uses only a local model for it.

## Finished files

Besides the working exports, Jerome makes the files a book is sold as:

- **`epub-book`**, an EPUB 3 ebook. A Word-born book gets an EPUB written by Jerome, with
  its own cover, title, copyright and contents pages and the chapters split at Heading 1.
  An EPUB-born book keeps its publisher's design: its clean export with the publication
  details and cover written in.
- **`pdf-book`**, a PDF for digital sale, in EB Garamond at the book's trim size (6×9 in,
  5.5×8.5 in, 5×8 in or A5), with running heads, page numbers, footnotes at the foot of the
  page, hyphenation for English and German, bookmarks and metadata. It is not a print-ready
  PDF: there is no PDF/X, bleed, CMYK or crop marks.

**Publication details.** Under Publication on the project page (`GET`/`PUT
/v1/books/{id}/publication`, MCP `get_publication`/`update_publication`): authors (required,
in display order), translator, publisher, an ISBN-13 for each of the EPUB and the PDF, year,
rights line (by default `© year authors`), description and trim size. A translated book
publishes with its translated title and its target language. **The cover** (`PUT
/v1/books/{id}/cover` with a file, MCP `set_cover`) must be a JPEG or PNG at least 1400 px on
its long side; a refused image keeps the earlier cover.

**Making one.** Make EPUB / Make PDF on the project page, `POST /v1/books/{id}/publish` with
`{"format": "epub-book"}` or `"pdf-book"`, or MCP `publish_book`. It runs as a job of type
`publish` that costs no model money; a book has at most one active job. When it completes,
`GET /v1/jobs/{id}/file` (MCP `finished_file`, a download link) serves the file, named with
the job's id, so every run keeps its own. It is refused for a hidden source book, for a
translation while any paragraph is pending or flagged, and without an author. Rendering
stops after 15 minutes, and a book with more than 200 MB of images is refused.

**Left out.** Jerome makes its own front pages, so the manuscript's are left out: a Word
table of contents, and an EPUB's cover, title page, copyright page and table of contents
(found by landmarks, `epub:type` or the EPUB 2 guide), when Jerome writes the file.

**The text check.** Before a file is made, its text is compared with the reviewed text
(the clean export), ignoring only spacing and soft hyphens. Any other difference fails the job at once and
names the place; it cannot be overridden. Such a difference is a fault in Jerome: report it.

**Warnings** go on the job (`stats.warnings`) and on the project page: no cover, the parts
left out, suggestions still open, chapters not proofread, a dropped image (a linked image or
one of an unknown type, never fetched), and EPUBCheck's errors where EPUBCheck is installed.
They never stop the job: you decide when a book is ready.

## Connecting Janus (MCP)

Jerome is also an MCP server, so an AI agent such as Janus (or any MCP client) can do what
an editor does: import books, set up and run proofreading jobs, review and decide
suggestions, and do the Word round trip. The endpoint is `/mcp` (Streamable HTTP,
stateless, JSON responses), outside `/v1`.

1. Make one key per agent, named after it, so revoking it cuts off that agent alone:

   ```bash
   docker compose exec api jerome keys create janus-desktop   # prints the key, shown once
   ```

2. Point the agent at `https://<your Jerome domain>/mcp` and send the key in the header
   `Authorization: Bearer <key>` on every request. A missing, unknown or revoked key gets
   401; a browser session cookie is never accepted on `/mcp`. For Janus, an entry in
   `mcp_servers` looks like this:

   ```json
   {
     "mcp_servers": {
       "jerome": {
         "type": "http",
         "url": "https://jerome.example.com/mcp",
         "headers": { "Authorization": "Bearer jk_..." }
       }
     }
   }
   ```

3. Set `JEROME_PUBLIC_BASE_URL` (for example `https://jerome.example.com`) when Jerome
   runs behind a reverse proxy, so the file links below point at the public address.
   Unset, links use the address the MCP request came in on.

**Files.** Books never travel inside a tool call. `request_upload` returns an `upload_url`
that takes one `PUT` of the raw file (`curl --fail --upload-file book.docx "<upload_url>"`),
then `import_book` or `import_review_copy` takes its `upload_id`. `export_book` returns a
`download_url` that takes one `GET`. The links carry their own token, so the API key never
appears in a shell command. They work once, expire after 15 minutes, and an upload can only
be used by the key that asked for it; an upload is at most `JEROME_MAX_UPLOAD_MB`.

**What Janus does is visible.** A suggestion decision made through MCP shows as
"Janus (<key name>)" in the review screens and can be undone there. Every other change
(imports, settings, jobs) is listed on the "Activity" page, with the before and after
values for a setting. Decisions in an imported Word review copy are the editor's, so they are
recorded as Word decisions.

**Activity.** The Activity page (`GET /v1/activity`, MCP `list_activity`) lists everything
done, newest first: every correction decision by people, Janus, automatic apply, Word import
and API keys, and Janus's other changes. It filters by book, chapter and who. A correction
that is still its suggestion's latest decision can be reverted from there
(`POST /v1/activity/decisions/{id}/revert`, MCP `revert_decision`); what it displaced comes
back, and the revert is listed as an undo.

**Not available over MCP:** deleting a book, team management, signing in, passwords and API
keys. Those stay with people, in the web interface and the CLI.

## Public repository: no manuscripts

This repository is public. Never commit manuscripts, book files, exports, `.env` or keys.
`.gitignore` excludes `*.docx`, `*.epub`, `.env`, `eval/data` and `eval/results`. The only
book files allowed are the public-domain fixtures under `tests/fixtures/public/`.

## Evaluation

A seeded-error harness measures the passes on public-domain text. It injects known typo,
punctuation, grammar and consistency errors into Project Gutenberg books (listed in
`eval/sources.toml`) and scores precision and recall per source and category.

```bash
uv run python -m eval.fetch_texts                    # free; writes eval/data/ (gitignored)
docker compose up -d languagetool
uv run python -m eval.run --language en --max-cost 3 --seed 1
uv run python -m eval.run --language de --max-cost 3 --seed 1
```

`eval.run` prints the cost estimate and asks `Run? [y/N]` before any paid call, refuses to
start if the estimate is above `--max-cost`, and stops sending chunks once the actual cost
reaches it. It uses the configured AI model. Results are written to `eval/results/`
(gitignored) as JSON and Markdown.

**No baseline numbers have been recorded yet.** The paid baseline has not been run; run it
with your own API key and record the overall precision and recall here.

## Tests

`uv run pytest -q` needs only Postgres (`JEROME_TEST_DATABASE_URL`); DB-backed tests are
skipped if it is unreachable. Integration tests are opt-in:

```bash
docker compose up -d languagetool
uv run pytest -m integration
```

They need LanguageTool on port 8010; the LibreOffice ones need `soffice` installed and are
skipped without it.
