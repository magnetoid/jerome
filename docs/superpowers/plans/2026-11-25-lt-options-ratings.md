# LanguageTool options and three AI ratings: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep LanguageTool's options for each mark, show them on the review card, and have
the AI check choose among them with one of three ratings.

**Architecture:**
- The LanguageTool pass keeps the options and the rule id on the `Candidate`.
- `add_candidates` stores them on the `Suggestion`.
- The AI check request lists the options, and the reply schema has the three ratings.
- The web card shows the options and the rating.

**Tech Stack:** Python 3.12, SQLAlchemy, Alembic, FastAPI; React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-lt-options-ratings-design.md`

## Global Constraints

- `AI_VERDICTS = ("error", "maybe_error", "not_an_error")`.
- `MAX_OPTIONS = 5`.
- Migration `0011` revises `0010`:
  - `correct` becomes `error`, with `ai_replacement` set to `replacement`;
  - `better` becomes `error`.
- An option equal to the original, repeated, or failing `check_replacement_text` is dropped.
- An `error` or `maybe_error` reply with an empty `replacement` takes LanguageTool's first
  option.
- Never commit book files. No server names in docs.

## Review Focus

- **LanguageTool returns duplicate or empty options.** Expect the duplicates dropped and an
  empty option kept only if it is a deletion that differs from the original.
- **Old marks.** `options` is null: the card shows no options list and the AI gets the
  replacement as option 1.
- **The AI answers with an option, but with different case or extra spaces.** Expect it
  stored as given. The card then matches options exactly and shows Use AI fix.
- **An option fails `check_replacement_text`** (a control character). Expect it dropped,
  and not shown.
- **The migration downgrade.** Expect `error` and `maybe_error` to map back to `correct`,
  and the columns dropped.

---

### Task 1: Store LanguageTool's options and rule

**Files:**
- Modify:
  - `jerome/types.py` (Candidate `options: tuple[str, ...] = ()`, `lt_rule: str | None = None`);
  - `jerome/orm.py`;
  - `jerome/proofread/languagetool.py`;
  - `jerome/core/suggestions.py`;
  - `jerome/api/schemas.py` (`options: list[str] | None`, `lt_rule: str | None`);
  - `jerome/api/routes_suggestions.py` (`_PLAIN_FIELDS`).
- Create: `migrations/versions/0011_lt_options.py`.
- Test:
  - `tests/proofread/test_languagetool.py`: options kept in order, deduped, the original
    dropped, at most 5, the rule id kept;
  - `tests/core/test_suggestions.py`: options and rule stored;
  - `tests/api`: fields in the output;
  - a migration test, if the repo has a migration test pattern.

### Task 2: Three ratings in the AI check

**Files:**
- Modify:
  - `jerome/types.py` (`AI_VERDICTS`);
  - `jerome/proofread/ai_check.py` (`Mark.options`, the request lists them, the schema and
    parse rules);
  - `jerome/core/ai_check.py`;
  - `jerome/proofread/prompts/ai_check_{en,de}.md`;
  - `jerome/mcp/tools_review.py` (the description).
- Test:
  - the request lists "1. …", "2. …";
  - the three ratings parse;
  - an empty fix on `error` or `maybe_error` takes option 1;
  - a fix on `not_an_error` is dropped;
  - core and API tests use the new names.

### Task 3: The card

**Files:**
- Modify:
  - `web/src/lib/review.ts` (types, labels, `canUseAiFix`, `aiPick`);
  - `web/src/components/review/FocusCard.tsx`;
  - `web/src/pages/ReviewPage.tsx` (`onUseOption`);
  - fixtures.
- Test:
  - `FocusCard.test.tsx`:
    - options listed, and a click calls `onUseOption` with the text;
    - option 1 accepts;
    - which button is primary for each rating;
    - colour labels.
  - `ReviewPage.test.tsx`: an option click sends an edit with that text.

### Task 4: Docs

- Modify: `README.md`, the Ask AI paragraph.
