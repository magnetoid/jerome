# MCP server: design

Status: approved by the owner on 2026-11-22. The owner chose approach A, the two-Janus picture and the activity log. This builds on the proofreading slice (`2026-11-21-proofreading-slice-design.md`), the web interface (`2026-11-21-web-interface-design.md`) and team roles (`2026-11-22-roles-and-theme-design.md`).

## Intent

Janus is the owner's AI agent. It will work with Jerome in two places:

1. **Janus on the owner's desktop** connects straight to Jerome in production.
2. **A second Janus runs inside Jerome** as a separate part of the system. It acts as an assistant across all of Jerome, with a chat panel in the web interface. That is the next project, with its own design.

Both talk to Jerome over MCP and need the **complete picture of Jerome**. Janus is a capable agent, so it gets the same powers a person has, not a narrow slice.

This project builds that MCP server. Its first user is the desktop Janus. The embedded Janus will use the same server later.

### The bigger picture

The owner wants Jerome to carry a book all the way through:

- import;
- proofreading, and translation later;
- finished PDF and EPUB files;
- publishing to the shop that sells them.

The owner's order is:

1. **MCP server** (this project);
2. **translation** (EN↔DE);
3. **finished PDF and EPUB output**;
4. **publishing to the shop**, which comes last.

PDF is an **output only**. Jerome never pulls books from the shop. Each later step gets its MCP tools when it is built, in the same code.

## Goals

- An MCP endpoint at `/mcp` on Jerome's existing app. Every operation an editor can do through the REST API is available as a tool, except the exclusions in Non-goals.
- The tools call the same `core` services as the REST routes and the CLI, so an operation behaves the same however it arrives.
- Janus signs in with a Jerome API key, as other scripts do today.
- Every suggestion decision Janus makes is visible as Janus's in the review interface and can be undone, like any other decision.
- Everything else Janus changes is written to a **Janus activity log**: what, when, with which key, and for settings the value before and after. People can read it on a "Janus activity" page. The owner chose this.
- The README shows how to connect a Janus (or any MCP client) to Jerome.

## Non-goals

- The embedded Janus and its chat panel (the next project).
- Translation, PDF output and shop publishing (later projects; they get tools when they exist).
- Team management, sign-in, passwords and API key management over MCP. API keys can't manage the team (roles design), and an agent shouldn't hand out keys.
- Deleting a book over MCP. It is the one irreversible operation, and the anti-goal below rules it out for an agent. People delete books in the web interface. **(call)**
- OAuth or per-person sign-in for MCP. Today's API keys are enough for one owner's desktop Janus. The embedded Janus will need to act on behalf of the signed-in person; that design decides how.
- A local stdio transport (`jerome mcp`). The desktop Janus reaches production over the network, so stdio doesn't serve it.

## Anti-goals

Any of these would make the release a failure even if it works:

- An agent change that a person cannot see or undo.
- A tool that behaves differently from the REST route for the same operation (other checks, other limits, other results).
- A way into `/mcp` without an API key, including a browser's session cookie.

## Approaches

The owner was shown three and chose **A**.

| | Approach | Trade-off | |
|---|---|---|---|
| A | MCP inside Jerome's API process at `/mcp`. Tools call the `core` services directly and authenticate with the same API keys. | Least work. One container and one domain, so every future feature gets its tool in the same code. MCP and REST share one process. | **Chosen** |
| B | A separate MCP service that calls Jerome's REST API. | Cleaner separation, but every operation is written twice and goes through an extra hop. It also goes against the first design's rule that MCP wraps the same services. | Rejected |
| C | A local stdio MCP through the CLI (`jerome mcp`). | It only works on the machine that runs Jerome, so the desktop Janus can't reach production with it. | Rejected |

## Design

### Endpoint and transport

- `/mcp` is mounted on the FastAPI app created by `create_app`, outside `/v1`.
- The transport is MCP **Streamable HTTP** in **stateless** mode, answering with plain JSON, not a server-sent event stream. **(call)**
  - Each tool call is short. Long proofreading runs in the worker, and Janus follows it with `get_job`.
  - Plain JSON passes through the reverse proxy in front of production without any buffering or timeout tuning.
- It uses the official MCP Python SDK, pinned in `pyproject.toml`. **(call)**
- Production needs no new container, domain or proxy rule. The proxy already forwards the whole domain to the app.

### Authentication and identity

- **Header:** every request to `/mcp` must carry `Authorization: Bearer <API key>`, checked by the same `verify_api_key` as REST.
- **Errors:** a missing, unknown or revoked key gets HTTP 401 before the MCP layer runs.
- **No cookies:** a session cookie is never accepted on `/mcp`, so a web page can't drive Janus's tools from a signed-in browser.
- **Identity:** the caller is `Principal(actor=<key name>, channel="mcp", user_id=None, must_change_password=False)`.
- **Keys:** the README recommends one key per Janus, named after it, for example `janus-desktop`, made with `jerome keys create`. Revoking that key cuts off that Janus alone.

### Decisions made by Janus

- **Migration `0006`** adds `mcp` to the allowed decision channels (`CHANNELS` and the `ck_decisions_channel` check). The same migration adds the upload-link table and the `agent_actions` table (below). The downgrade refuses to run while any decision has channel `mcp` or any `agent_actions` row exists, rather than losing them silently. **(call)**
- **Recording:** tool calls that accept, reject, undo or edit suggestions record `actor=<key name>` and `channel="mcp"`, through the same `decide`, `bulk_decide` and edit functions as REST.
- **In the web interface:**
  - wherever a decision's author is shown, an `mcp` decision reads "Janus (<key name>)";
  - the existing undo works on it like on any other decision;
  - in the "left to review" counter, a suggestion Janus decided counts as decided, the same as an editor's decision.
- **Learning loop:** decisions on channel `mcp` are agent decisions, not editor decisions. Anything that later learns from editor decisions must leave them out, as it already must leave out `auto`. A person's later reversal of a Janus decision counts as a real editor decision. This design only has to record the data correctly. **(call)**

### Janus activity log

The owner chose that a person must be able to see everything Janus changed, not only its suggestion decisions.

- **Data:** migration `0006` also adds a table `agent_actions` with these columns:
  - `id` and `at`;
  - `actor`, the key name, and `channel`, always `mcp` for now;
  - `tool`, the tool name;
  - `book_id`, `chapter_id` and `job_id`, each nullable, whichever apply;
  - `summary`, one human sentence such as "Turned auto-apply off" or "Accepted 14 suggestions in chapter 3";
  - `before` and `after`, as JSON, nullable.
- **What is logged:** one row per **successful** call of a tool that writes:
  - `import_book`, `import_review_copy`;
  - `set_style_sheet`, `set_auto_apply`, `set_chapter_proofread`, with the value before and after;
  - `start_job`, `cancel_job`, `resume_job`;
  - `decide_suggestion`, `edit_suggestion`, `bulk_decide`, with the suggestion IDs and counts. The decisions themselves stay in the decision table, as above.
- **What is not logged:** read-only tools and failed calls, which change nothing. **(call)**
- **Transactions:** the row is written in the same transaction as the change, so a change is never left out of the log and a rolled-back change never appears in it.
- **Auto-apply:** when a job Janus started auto-applies suggestions, those decisions stay `ai`/`auto`. The `start_job` row shows that Janus started that job. **(call)**
- **API:** `GET /v1/agent-actions?book_id=&limit=&offset=`, newest first, for any signed-in member or API key.
- **Web:**
  - a "Janus activity" page, linked in the navigation, for every member. Books are shared by the whole team, so the log is too. **(call)**
  - Each row shows the time, the key ("Janus (<key name>)"), the summary and links to the book, chapter or job. For a setting, it also shows the before and after values.
  - The book page links to its own filtered view.
- **Undo:**
  - Suggestion decisions are undone in the review screen, as today.
  - A setting is changed back with the book's normal settings, using the "before" value the log shows.
  - There is no one-click revert. **(call)**
- **Retention:** rows are kept. They are small, and the history is the point.

### Tools

Each tool wraps the core function its REST route calls.
- **Arguments:** the same as the REST parameters, with the same validation.
- **Results:** a structured result built from the same response schemas (`BookOut`, `SuggestionOut` and so on).
- **Annotations:** read-only tools say so. Tools that start paid model work say so in their description.

| Group | Tools | REST equivalent |
|---|---|---|
| Projects and books | `list_projects`, `list_books`, `get_book`, `list_chapters`, `set_chapter_proofread` | `GET /projects`, `GET /books`, `GET /books/{id}`, `GET /books/{id}/chapters`, `PATCH /chapters/{id}` |
| Import | `request_upload`, `import_book` | `POST /books` |
| Book settings | `get_style_sheet`, `set_style_sheet`, `get_auto_apply`, `set_auto_apply` | the style-sheet and auto-apply routes |
| Jobs | `estimate_job`, `start_job`, `list_jobs`, `get_job`, `cancel_job`, `resume_job` | the job routes |
| Review | `get_chapter_summary`, `get_chapter_review`, `list_suggestions`, `get_block`, `decide_suggestion` (accept, reject, undo), `edit_suggestion`, `bulk_decide` | the suggestion and review routes |
| Word round trip | `export_book`, `import_review_copy`, `get_review_import` | `GET /books/{id}/export`, the review-import routes |
| Quick text | `proofread_text` | `POST /proofread/text` |

**Files.** Files never travel inside tool calls. Janus's MCP client hands every tool argument and result to the model, and a book is far too large to pass that way: a 50 MB file is about 67 MB of base64. Files move through **short-lived, single-use links**, which Janus uses with its own file or terminal tools (for example `curl`). **(call)**
- **Uploads:**
  - `request_upload` returns an `upload_id` and an `upload_url`. The link takes one `PUT` of the raw file bytes, accepts at most `max_upload_mb`, and expires after 15 minutes.
  - `import_book` then takes `upload_id` and `filename`, plus the same options as the REST upload.
  - `import_review_copy` takes `book_id` and `upload_id`.
  - An upload can be used once, and only by the key that requested it.
- **Exports:** `export_book` returns a `download_url`. The link takes one `GET`, expires after 15 minutes, and serves the same file, name and media type as the REST export.
- **Link security:**
  - The link carries its own unguessable token, so Janus never has to put its API key into a shell command, where the model would see it.
  - Only the token's hash is stored, with its key name, kind, expiry and whether it has been used.
  - An expired, used or unknown link gets 404.
  - The links live under `/mcp/files/…`.
- **Storage:** an uploaded file waits in Jerome's data directory until `import_book` or `import_review_copy` uses it. An unused upload is deleted once it expires. **(call)**

**Review copies.** The changes in an uploaded Word review copy were made by a person in Word. Their decisions therefore stay on channel `word` with the key name as actor, as REST records them today. The review import record shows that Janus uploaded it. **(call)**

**Errors.**
- **Mapping:** Jerome's errors (`NotFound`, `Conflict`, `InvalidChangeError` and the rest in `jerome/errors.py`) become MCP tool errors (`isError: true`). The message is the same text REST puts in `detail`, starting with a short code: `not_found:`, `conflict:`, `invalid:`.
- **Unexpected exceptions:** they become a generic tool error and are logged on the server, as REST does with a 500.

**Transactions.** One database transaction per tool call, committed on success and rolled back on any error, the same as one REST request.

**Descriptions.** Each tool's description is written for an agent:
- what the tool does;
- which IDs it takes and where they come from (for example "`chapter_id` from `list_chapters`");
- what it returns.

`start_job` says it runs paid model work and points to `estimate_job` first.

### Docs

The README gains a "Connecting Janus (MCP)" section. It covers:
- making a key with `jerome keys create janus-desktop`;
- the endpoint `https://<your Jerome domain>/mcp`;
- the header `Authorization: Bearer <key>`;
- a Janus `mcp_servers` example entry;
- the excluded operations.

### Testing

- **Client:** tests drive `/mcp` with an MCP client running in-process against the ASGI app. There is no network and no model calls; the existing test doubles stand in for the model.
- **Coverage:**
  - auth: no key, a bad key, a revoked key, and a cookie only are all refused with 401;
  - tool listing: every tool in the table is present, and excluded ones are absent;
  - each tool family works end to end on the public test fixtures;
  - error mapping, for each error type;
  - a decision made over MCP has `channel="mcp"` and `actor=<key name>`, and can be undone over REST;
  - upload and download links: one use only, expiry, the size limit, only the requesting key can use an upload, and 404 for an unknown token;
  - REST parity: for a representative tool per group, the tool's result equals the REST response for the same object;
  - migration `0006` goes up and down;
  - the activity log:
    - each writing tool leaves one row with the right summary and before and after values;
    - read-only tools and failed calls leave none;
    - a rolled-back change leaves no row;
    - the API filters by book.
- **Web:**
  - an `mcp` decision shows "Janus (<key name>)";
  - the Janus activity page lists rows, shows before and after for a setting, and filters by book.

## Left to the builder

- **Wiring:** mounting the SDK's Streamable HTTP app in FastAPI and its lifespan, the module layout (for example `jerome/mcp/`), and how a tool gets its database session and principal.
- **Naming:** argument names beyond those above, and the exact wording of the tool descriptions.
- **Where "Janus (<key name>)" appears** in the review screens, as long as every place that shows a decision's author shows it.
- **Conventions:** follow the existing patterns (`SessionDep` semantics, the `core` services, the errors in `jerome/errors.py`, `test-utils.tsx` on the web side).
