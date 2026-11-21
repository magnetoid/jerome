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
asks before creating the job), `jerome export BOOK_ID --format docx-review -o review.docx`.

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
| `ANTHROPIC_API_KEY` | unset | Anthropic API key (no `JEROME_` prefix). Read from the environment or `.env`, so local runs find it too; never logged |

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

## Stopping and crashes

`jerome worker` handles SIGTERM and SIGINT: it finishes the chunk it is on, commits it, puts
the task and job back in the queue and exits (the compose file gives it 180 s for that). A
restarted worker carries on from the next chunk.

If a worker crashes or is killed, its job stays `running` and cannot be resumed while it says
so, because a live worker might still hold it. Cancel the job
(`POST /v1/jobs/{id}/cancel`), then resume it (`POST /v1/jobs/{id}/resume`); committed chunks
are kept and not paid for twice.

## Word round trip

1. Import the manuscript and run a proofreading job.
2. Export `docx-review`: a copy with Jerome's suggestions as tracked changes and comments.
3. In Word (or LibreOffice) accept or reject changes, and edit freely. Save as DOCX.
4. Upload the file to `POST /v1/books/{id}/review-imports`. Jerome records your decisions.
5. Export `docx-clean` (or `docx-tracked`) for the final text.

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
