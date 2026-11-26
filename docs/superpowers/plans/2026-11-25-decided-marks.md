# Decided Marks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** In review, a decided mark can be clicked in every view, and its card shows who decided it and when, with Revert.

**Architecture:**
- The API adds `decided_at`, taken from the mark's latest decision.
- `useReviewFocus` learns to pin a mark the person clicked that is outside the view, while `j`/`k` still walk the view.
- The card shows the time and labels its undo button Revert for a decided mark.

**Tech Stack:** FastAPI and SQLAlchemy; React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-decided-marks-design.md`

## Global Constraints

- Exact copy:
  - the state line is "Accepted by X · <date and time>";
  - the button reads "Revert (u)" on a decided mark and "Undo (u)" otherwise.
- `decided_at` is null when the mark has no decision other than a system one, which is the same rule as `decided_by`.

## Review Focus

1. A decided mark is clicked, then the person presses `j`. Focus goes to the next open mark after it in the text, not to the first one in the chapter.
2. A decided mark is clicked, then the person presses `u`. That mark is reverted, not the last decision in the history.
3. A decided mark is clicked, then the person decides an open mark elsewhere. The card still moves on as usual.
4. A mark that is reverted after a click becomes an ordinary open mark in Needs you.
5. On an open mark the button reads "Undo", and it still undoes the last decision.

---

### Task 1: `decided_at` in the API

**Files:**
- Modify: `jerome/api/routes_suggestions.py` (`suggestion_out`) and `jerome/api/schemas.py` (`SuggestionOut.decided_at: datetime | None`)
- Test: `tests/api/test_suggestions_api.py`

Steps:
- [ ] Write the failing test. After an accept, `decided_at` equals the decision's `created_at`. On an open mark it is null.
- [ ] Implement it: add `Decision.created_at` to the `deciders` query.
- [ ] Run the API tests, then commit.

### Task 2: Clicking decided marks, and the card

**Files:**
- Modify: `web/src/hooks/useReviewFocus.ts`, `web/src/pages/ReviewPage.tsx`, `web/src/components/review/FocusCard.tsx`, `web/src/lib/review.ts` (`decided_at` on `Suggestion`)
- Test: `web/src/pages/ReviewPage.test.tsx`, `web/src/components/review/FocusCard.test.tsx`, `web/src/hooks/useReviewFocus.test.ts` (if present)

Interfaces:
- `useReviewFocus(items, pickable = [])` returns `{ current, focus, pick, next, prev }`:
  - `pick(item)` pins an item from `pickable`;
  - `next` and `prev` work from document order when `current` is not in `items`.

Steps:
- [ ] Write the failing tests, one for each Review Focus line, plus a card test: "Accepted by ana · <formatted>" and "Revert".
- [ ] Implement it:
  - `ReviewPage` passes `isActive = inView || isDecided`;
  - `select` picks a decided mark that is outside the view;
  - the card shows `formatDateTime(decided_at)` and the Revert label.
- [ ] Run vitest and tsc, then commit.
