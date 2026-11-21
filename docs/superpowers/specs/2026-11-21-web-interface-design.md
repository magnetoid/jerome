# Jerome web interface: first version — design

Date: 2026-11-21. Status: draft for the owner's approval.

## 1. Intent

Today Jerome is used only through its REST API and the Word round trip. The owner's team needs a web interface so they can do the whole proofreading workflow in a browser: start a project, run proofreading, review, export.

The owner's words set the direction:

- users start projects and jobs;
- the work should have "a very streamlined and good flow";
- the AI should do most of the work, and show what it changed, with notes on why.

A Janus chat page is wanted too, but it comes in the next project (§3).

## 2. Who uses it

- **Only the owner's team.** There are no outside editors, authors or clients.
- **One shared workspace.** Everyone sees every project.
- Each person has their own login, so every decision records who made it. The learning loop depends on that.
- There are no roles or permissions. Every team member can do everything, including managing the team.

## 3. Goals, non-goals, anti-goals

**Goals**

1. A team member can go from a Word or EPUB file to a reviewed, exported book without touching the API or a terminal.
2. The AI applies clear-cut corrections itself. People only decide what needs judgment, and can always see and reverse what the AI did, with its reason.
3. Each screen has one obvious next step.

**Non-goals for this version.** Each comes later:

- the Janus chat page and Jerome's MCP server (the next project);
- roles, permissions, or outside users;
- password reset by email, email invitations, or Google sign-in;
- translation;
- a phone layout. The interface is for laptop and desktop screens; it only needs to stay usable on a tablet;
- two people editing the same chapter live. The API's existing conflict checks (409) are enough.

**Anti-goals.** Any of these would make the release a failure even if it works:

- an AI change that a person cannot see or undo;
- the "left to review" counter mixing AI-applied changes with ones that need a person;
- API keys stored in the browser;
- a second server, container or domain to deploy.

## 4. Constraints

- **The repo is public.** No book files, aligned pairs or real manuscripts may be committed. Tests and fixtures use synthetic text or the Gutenberg files in `tests/fixtures/public/`.
- **Same deployment.** The interface ships in the same Docker image and container as the API. It is served by Jerome on the production domain, behind the existing reverse proxy and Coolify setup (`docker-compose.coolify.yml`), and needs no proxy changes.
- **Same AI model.** The product's model stays `claude-opus-5-5`.
- **Tests run against Homebrew Postgres 16.** This Mac has no Docker daemon, and backend tests use `JEROME_TEST_DATABASE_URL`. Node 26 and npm 11 are installed locally.
- **Existing API endpoints keep their shape** for scripts and, later, for Janus. The one behaviour change: jobs on books with auto-apply switched on (§6.3) accept clear-cut suggestions themselves. Existing books start with it off.

## 5. Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| A | React app in `web/` inside the Jerome repo, built during the Docker image build and served by FastAPI. The browser signs in with a session cookie. | One container and one domain. Matches the Janus web app's stack. The Docker build gets a Node stage (about +1 minute). | **Chosen by the owner** |
| B | A separate Next.js app in its own container | Another container, another proxied port, and Node running in production. Its server-side rendering isn't needed behind a login. | Rejected |
| C | Pages rendered by Python, with shadcn-style CSS from a CDN | No Node build. But the review screen needs a lot of JavaScript anyway (keys, inline marks, live progress), so the code would be half templates and half hand-written scripts, and it wouldn't match Janus. | Rejected |

The owner's earlier "shadcn via CDN" idea is covered by A. shadcn/ui is a set of React components compiled into the app, not a CDN stylesheet.

## 6. The design

### 6.1 Stack and serving

- **Location and stack.** The app lives in `web/`: React, TypeScript, Vite, Tailwind CSS 4 and shadcn/ui. This is the same setup as Janus's web app, so components and styles can be shared later.
- **How FastAPI serves it.** The built static files are served at `/`. Any path that isn't `/v1/...`, `/docs`, `/openapi.json` or a static asset returns `index.html`, so links like `/projects/12/review` work on reload. Built assets get long cache headers; `index.html` gets none.
- **Docker.** The image becomes a two-stage build. A Node stage runs `npm ci && npm run build`, and the Python stage copies the output.
- **Local development.** `npm run dev` runs Vite, which forwards `/v1` to `localhost:8000`.
- **Look.** Light and dark themes follow the system setting (the shadcn default). Notifications use sonner toasts. Icons are lucide.

### 6.2 Logins and the team

- **Users table.** Fields: `email` (unique, compared case-insensitively), `name`, `password_hash` (a standard slow hash such as argon2), `active`, `created_at`, `last_login_at`.
- **Sessions.** Sessions are stored server-side: the cookie holds only a random token, and the database stores its hash. So deactivating a user or logging out ends their sessions immediately.
  - The cookie is `HttpOnly`, `Secure` and `SameSite=Lax`.
  - A session lasts 30 days and is extended while in use.
  - State-changing requests that authenticate by cookie must be protected against cross-site requests: require a custom header that the app always sends, or an equivalent method.
- **Login throttling.** Repeated failed logins for the same email are always slowed down.
  - Throttling by client IP happens only when the proxy is trusted, through a new setting `JEROME_TRUSTED_PROXIES` that is empty by default.
  - Without that, every request appears to come from the Docker gateway, and per-IP throttling would lock out the whole team at once.
  - In production the reverse proxy already sends `X-Forwarded-For`, so the deploy notes say to set the setting to the Docker gateway address. No proxy change is needed.
- **One actor for both kinds of auth.** `current_actor` accepts either an API key (as today) or a session cookie.
  - For a person, the recorded actor is their email.
  - Decisions made in the interface use a new channel, `web`. Existing channels are `api`, `word` and `system`.
- **First user.** The first login is created with a new CLI command, `jerome users create EMAIL --name NAME`. It prints a generated password once. This is the same pattern as `jerome keys create` and works through the Coolify one-off task.
- **Team page.** It lists members (name, email, last login, active). Any member can:
  - add a member, which shows a one-time temporary password to pass on;
  - deactivate or reactivate a member;
  - reset a member's password, which also shows a one-time temporary password.

  A temporary password must be changed at first login. Each person can change their own password from the account menu.
- **Last active member.** Deactivating the last active member is refused.

### 6.3 AI applies the clear-cut changes

This is the core of "AI does most of the work".

- **Default rule.** After a job finds suggestions, Jerome accepts a suggestion automatically when all three hold:
  - its category is `typo` or `punctuation`;
  - its severity is `high`;
  - the AI proposed it, either alone or with LanguageTool making the identical change.
- **The team's own house-style rules also apply themselves.** This was the owner's decision. Two kinds of fix from the consistency pass are applied automatically even though the AI did not propose them:
  - house spellings from the style sheet, such as "always *Foto*, never *Photo*";
  - quote-style fixes.

  These are the team's rules, not guesses.
  - Consistency findings based on the majority form in the book (spelling and name variants) still go to a person.
  - To tell the two kinds apart, consistency suggestions need a `rule` marker: `house_spelling`, `quote_style`, `variant` or `name`.
- **LanguageTool alone is never enough.** On the Gutenberg test book, LanguageTool proposed "cœur → cur" and "Sitôt → Pitot" as high-severity typos in a French epigraph. It would ruin text like that if trusted on its own.
- **When the checkers agree.** When the AI and LanguageTool propose the identical change, the merge step (`jerome/proofread/merge.py`) currently drops the duplicate and keeps no record of it.
  - It must now set an `agreed` flag on the kept suggestion.
  - That suggestion takes the AI's category, severity and explanation, and its source becomes `model`. The auto-apply rule therefore always uses the AI's own rating: a change the AI rated `medium` never auto-applies on LanguageTool's `high`.
  - The interface shows it as "both checkers agree".
- **When the checkers disagree.** When the merge drops a *different* fix for an overlapping span, which it does silently today, it must:
  - set a `disputed` flag on the kept suggestion;
  - store the dropped fix's text in an `alternative` field.

  Disputed suggestions never auto-apply. Their card shows "the checkers disagree" and the alternative fix.
- **Per-project setting.** Each book has an auto-apply setting. The default is as above. It controls:
  - on or off;
  - which categories (any of typo, punctuation, grammar, consistency, style);
  - a minimum severity;
  - "apply our house-style rules", on by default.
  - New books start with it on, however they are created (web, API or CLI). The upload endpoint accepts the setting.
  - Books that existed before this release start with it off.
  - A change to the setting applies to jobs started afterwards; suggestions already found are not re-evaluated.
- **When it happens.** Auto-apply runs in the worker as each chunk's suggestions are stored. Editors can therefore start reviewing finished chapters while later ones still run.
  - It goes through the same decision path as a person (`suggestions.decide`), with actor `ai` and a new channel, `auto`.
  - The consistency pass is unchanged: it still counts and anchors on the original text. Any of its findings that overlap an applied change are dropped, as today.
- **Reversing an AI change.** A person can reject an AI-applied change in one step: the original text comes back, and the decision is recorded as that person's, on channel `web`.
  - Today an accepted suggestion can only be undone back to open. The builder decides whether to add a direct "accepted → rejected" path or to log an undo plus a reject, but the person must take a single action.
  - A person can also edit an AI-applied change.
- **Exports.** The tracked-changes Word export shows AI-applied changes as tracked changes, so nothing is hidden there either.
- **Learning loop.** Decisions on channel `auto` are not editor decisions. Anything that learns from decisions later must exclude them, but must count a person's reversal of an auto decision as a real decision. This design only has to record the data correctly.

### 6.4 Screens

**Login**

Email and password. A temporary password leads to a change-password step.

**Projects (home), a single list**

- The table shows: project title, language, stage, progress, a next-step button, and last activity (who and when). Newest activity comes first, and there is a search box.
- A strip above the table shows running jobs, with progress and cost against the limit.
- A **+ New project** button sits in the top bar. The top bar also links to Projects and Team, plus the account menu.
- Stages are derived from data, not stored. The first row that matches wins:

  | Stage | When | Next step |
  |---|---|---|
  | Proofreading | a job is queued or running | View progress |
  | Needs attention | the latest job failed or paused at its cost limit | Resume |
  | To review | open suggestions exist | Review (with "N left") |
  | Done | at least one job completed and nothing is open | Export |
  | Set up | anything else: no job yet, or only cancelled ones | Start proofreading |

**New project**

A single page with:

- a drop zone for a DOCX or EPUB file;
- a title, pre-filled from the file;
- the language variant (English US/GB, German DE/AT/CH);
- a collapsed "House style" section: quote style, house spellings, words to ignore;
- the auto-apply setting, showing its default.

Create uploads the book and opens the project page.

**Project page**

The page shows one main panel at a time, depending on the stage:

- **Set up.** The chapter list has checkboxes. Chapters already marked as not to be proofread (front matter, licence text) start unticked. Below it: the cost estimate (low–high), a cost limit (default: the high estimate rounded up to the next dollar), and **Start proofreading**. The panel can also start a free LanguageTool-only run.
- **Proofreading.** Progress per chapter, cost so far, and Cancel. Chapters that are done can already be reviewed.
- **Needs attention.** What happened, and **Resume**, optionally with a higher cost limit.
- **To review / Done.** A summary such as "AI applied 214 · 38 need you · 12 decided by the team", with per-chapter counts and **Review**.

Whenever no job is active, the page also offers **Run proofreading again**. It uses the same chapter, pass, estimate and limit controls as Set up, and covers:

- an AI run after a free LanguageTool-only run;
- a re-run after changing the house style;
- newly ticked chapters.

A **Jobs** list on the page shows the book's jobs, newest first, with their status and cost.

Always available on the project page:

- **Export:** Word clean, Word with tracked changes, Word review copy, EPUB clean (whichever the format allows).
- **Upload reviewed Word file:** the existing review import, with its result shown.
- A **Settings** tab for house style and auto-apply.
- **Delete project**, behind a confirmation.

**Review (inline, like Word)**

- **Top bar:** a chapter selector, then filters for category and severity, plus a view switch: *Needs you* (default), *Applied by AI*, *All*. On the right, the counter "N left", which counts only suggestions that need a person.
- **Body:** the chapter's paragraphs in one readable column, showing the current text with marks:
  - **Needs you:** underlined. The one in focus opens a card underneath with the change, the AI's note, category, severity, source, and "both checkers agree" when it applies. Actions: Accept, Reject, Edit.
  - **Applied by AI:** shown as a change (old text struck through, new text) in its own colour, with the note beside it like a Word margin comment. Actions: Reject (undo), Edit.
  - **Decided by the team:** shown subtly, with who decided it on hover. Undo is available.
- **Keys:**
  - A accept;
  - R reject (also undoes an AI change);
  - E edit inline;
  - J and K next and previous suggestion in the current view;
  - U undo the last decision;
  - Esc closes the edit field.

  Every action is also a visible button.
- **Bulk action:** "Accept all N remaining [category] in this chapter", with confirmation. This uses the existing `/v1/suggestions/bulk`.
- **Finishing a chapter.** When its last "needs you" item is decided, a prompt offers the next chapter that has open items.
- **Errors.** API conflicts (409) and validation errors (422) appear as toasts with the API's message. The view then refreshes that paragraph.

**Team**

As described in §6.2.

### 6.5 API additions

The interface uses `/v1` like any client. New or changed endpoints:

- **Auth:**
  - `POST /v1/auth/login`;
  - `POST /v1/auth/logout`;
  - `GET /v1/auth/me`;
  - `POST /v1/auth/password` (change your own password).
- **Team:** `GET` and `POST /v1/users`, and `PATCH /v1/users/{id}` (name, active, reset password).
- **Project list with stage and progress:**
  - either `GET /v1/books` grows summary fields, or a new `GET /v1/projects` returns them;
  - the fields are stage, open count, auto-applied count, last activity (actor and time) and active job;
  - the builder chooses, but it must be one request for the whole list, not one per book.
- **Jobs:** `GET /v1/books/{id}/jobs` lists a book's jobs, newest first.
- **Counts:** a per-chapter summary for a book (open, auto-applied, decided by the team), used by the project page and the review chapter selector.
- **Chapter text for the inline review:**
  - one endpoint returns a chapter's paragraphs (original and current text) together with their suggestions;
  - it is paginated by paragraph if needed, so long chapters stay fast.
- **Suggestions:** `SuggestionOut` gains:
  - `chapter_id`;
  - `agreed`;
  - `disputed`;
  - `alternative`;
  - `auto_applied`;
  - `decided_by`, the actor of the latest decision.

  The list endpoint returns totals for paging.
- **Auto-apply settings:** `GET` and `PUT` per book. These may be part of the style-sheet endpoints.
- **Docs page.** The OpenAPI schema declares the Bearer scheme, so the `/docs` page gets a working Authorize button.

### 6.6 Live progress

While a job is queued or running, the project page and the running-jobs strip poll the job every 3 seconds, and stop when it finishes. Push updates (server-sent events or WebSockets) are not needed for this version.

## 7. Testing

- **Backend (pytest, as today):**
  - logins, sessions, throttling and cross-site protection;
  - users and team rules, including the last active member;
  - the auto-apply rule:
    - the default categories, severity and AI-involvement requirement;
    - the LanguageTool-alone exclusion;
    - house-spelling and quote-style fixes applying, and variant and name findings not applying;
    - disputed suggestions never auto-applying;
    - the AI's rating winning when the checkers agree;
    - per-project settings, the default for new books, and existing books staying off;
    - the `agreed`, `disputed` and `alternative` fields through the merge;
  - reversing an AI change in one action;
  - the new endpoints, and static file serving with the `index.html` fallback.
- **Frontend:** Vitest and Testing Library for the review screen: key handling, the needs-you / AI-applied split, and the counter. The full test run includes `tsc` and `vite build`, so a broken build fails.
- **End to end:** a short check against a local stack using the Gutenberg fixture: log in, create a project, run a LanguageTool-only job, review a few items, export.

## 8. Rollout

- Deploy with Coolify as today. The Alembic migrations add the users, sessions, auto-apply and `agreed` columns.
- After deploying, create the first login with `jerome users create` through the Coolify one-off task.
- The API keys in use keep working.

## 9. Left to the builder

- folder structure and state management inside `web/` (for example a data-fetching library);
- exact colours and spacing within shadcn defaults;
- the password hashing library and the exact cross-site protection method, within §6.2;
- whether the project summary extends `GET /v1/books` or is a new endpoint (§6.5);
- how the "reject an AI change" path is implemented (§6.3);
- the exact layout of the margin notes and cards, matching the inline mockup the owner chose.
