# Learned words for all books: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Remember for all books" button on the AI's `not_an_error` rating saves the
word, and LanguageTool checks in every book of that language skip it.

**Architecture:**
- A `learned_words` table and `jerome/core/learned_words.py` hold the logic: `remember`,
  `rules_for`, `list_words`, `delete_word`, `covers`.
- `languagetool_candidates` takes the learned rules.
- The worker loads them into its plan.
- The API has a remember route and the Settings routes.
- On the web: the card button and a Settings section.

**Tech Stack:** Python 3.12, SQLAlchemy, Alembic, FastAPI; React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-learned-words-design.md`

## Global Constraints

- The rule `*` means any misspelling rule. It is stored for marks with category `typo`.
- Matching is exact and case-sensitive.
- The language comes from the book's style sheet: `StyleSheet.language`.
- Migration `0012` revises `0011`. It has a unique constraint on (`language`, `word`,
  `rule`).
- The Settings routes need the super admin (`require_super_admin`). The remember route needs
  any principal.
- Never commit book files. No server names in docs.

## Review Focus

- **Remember twice.** Expect one row, and the marks are still rejected.
- **A mark already decided, or not rated `not_an_error`.** Expect a conflict, and nothing
  saved.
- **An old non-spelling mark with no `lt_rule`.** Expect a conflict.
- **Another book in the other language.** Expect its marks untouched and its checks not
  filtered.
- **An open mark of the same word under a different non-spelling rule.** Expect it left
  open.

---

### Task 1: The table and the core

**Files:**
- Modify: `jerome/orm.py` (`LearnedWord`).
- Create:
  - `migrations/versions/0012_learned_words.py`;
  - `jerome/core/learned_words.py`;
  - `tests/core/test_learned_words.py`.

**Interfaces (produces):**
- `LearnedRules = frozenset[tuple[str, str]]`, holding (word, rule) pairs.
- `covers(rules, word, rule_id, misspelling) -> bool`.
- `rules_for(session, language) -> LearnedRules`.
- `remember(session, suggestion_id, *, actor, channel) -> tuple[LearnedWord, list[int]]`,
  returning the row and the rejected ids.
- `list_words(session) -> list[LearnedWord]`, newest first.
- `delete_word(session, word_id) -> None`. It raises `NotFound`.

**Tests:**
- Remembering saves the row and rejects this mark, plus the same word's open LanguageTool
  marks in the book.
- It leaves alone:
  - a different rule;
  - another book;
  - a decided mark;
  - model marks.
- A misspelling is stored as `*`.
- Remembering twice keeps one row.
- The conflicts in the Review Focus are raised.
- `rules_for` filters by language.
- `delete_word` removes the row and leaves the rejections.

### Task 2: LanguageTool and the worker

**Files:**
- Modify:
  - `jerome/proofread/languagetool.py` (a `learned: LearnedRules = frozenset()` parameter);
  - `jerome/jobs/worker.py` (`_Plan.learned`, set from `rules_for(s, plan.style.language)`).
- Test:
  - `tests/proofread/test_languagetool.py`: a `*` word drops a misspelling but not a grammar
    match of the same text; a rule word drops only its rule; dropped matches are counted.
  - A worker test: a learned word stops a mark in a new job.

### Task 3: The API

**Files:**
- Modify:
  - `jerome/api/schemas.py` (`LearnedWordOut`, `RememberOut`);
  - `jerome/api/routes_suggestions.py` (`POST /suggestions/{id}/remember`, placed before the
    `{action}` route);
  - `jerome/api/routes_settings.py` (`GET /learned-words`, `DELETE /learned-words/{id}`).
- Test: `tests/api/test_learned_words_api.py`:
  - remember returns the word and the rejected count;
  - list and delete work for the super admin;
  - list and delete return 403 for an editor;
  - remember returns 409 on the conflict cases.

### Task 4: The web

**Files:**
- Modify:
  - `web/src/components/review/FocusCard.tsx` (an `onRemember` prop and the button);
  - `web/src/pages/ReviewPage.tsx` (the call, a toast, a refresh);
  - `web/src/lib/review.ts` (`canRemember`).
- Create: `web/src/components/settings/LearnedWordsSection.tsx`, used in `SettingsPage.tsx`.
- Test:
  - `FocusCard.test.tsx`: the button shows only when `canRemember`, and a click calls
    `onRemember`.
  - `SettingsPage.test.tsx` or a section test: the list renders, and Delete calls the API
    after the confirm dialog.

### Task 5: Docs

- Modify: `README.md`, the Ask AI paragraph, to mention Remember for all books.
