# Learned words for all books: design

The owner approved this on 2026-11-25. Asked where Jerome should remember a word, the owner
chose "Za sve knjige" (for all books). This builds on LanguageTool's options and the three
AI ratings (`2026-11-25-lt-options-ratings-design.md`), which keep LanguageTool's rule id
on each mark (`lt_rule`).

## Why

LanguageTool marks the same correct words over and over: names, invented words, deliberate
forms. When the AI says a mark is definitely not an error, the owner wants to save that
once, so that LanguageTool stops marking the word in this book and in every later book.

## Goals

- **The button.** A LanguageTool mark that the AI rated `not_an_error` gets a "Remember for
  all books" button on the review card. Clicking it:
  - saves a learned word;
  - rejects this mark;
  - rejects the book's other open LanguageTool marks that the learned word covers.
- **What a learned word holds:**
  - the language (`en` or `de`, from the book's style sheet);
  - the word (the marked text, exactly as written);
  - the rule;
  - the AI's reason;
  - who saved it and when, and the book it was saved from.
- **The rule.** For a misspelling (category `typo`), the rule is `*`: any spelling rule.
  Otherwise it is the mark's `lt_rule`, and only that rule is covered.
- **Later checks.** LanguageTool checks in every book of that language skip a match when:
  - its text equals a learned word;
  - and it is either a misspelling matched by a `*` word, or its rule id equals the word's
    rule.
  The skipped matches count as dropped, the same as the book's ignore words.
- **Settings.** A "Learned words" section on the Settings page lists every learned word
  (word, language, rule, reason, who, when) with a Delete button. Deleting a word does not
  reopen the marks it rejected.

## Non-goals

- An MCP tool. Remembering a word changes every book, so for now only people do it, on the
  web or through the API.
- Applying learned words to the plain-text check endpoint (`/v1/text`), which has no book.
- Learning from model marks or consistency marks.

## Decisions

- **A table, `learned_words`** (migration 0012):
  - `id`, `language`, `word`, `rule`, `reason`;
  - `book_id` (SET NULL when the book is deleted), `created_by`, `created_at`;
  - unique on (`language`, `word`, `rule`).
  - Saving a word that is already there keeps the existing row and still rejects the marks.
- **Matching is exact and case-sensitive.** "Katharina" does not cover "katharina".
- **When the button is offered.** The mark comes from LanguageTool, is open and rated
  `not_an_error`, and is either a misspelling or has an `lt_rule`. Marks made before
  `lt_rule` existed are covered only when they are misspellings. Otherwise the API refuses
  with a conflict.
- **The API:**
  - `POST /v1/suggestions/{id}/remember` returns the learned word and how many marks it
    rejected. The rejections are ordinary decisions by that person, so they can be undone
    one by one.
  - `GET /v1/learned-words` and `DELETE /v1/learned-words/{id}` are for the super admin,
    like the rest of Settings.
- **The worker** loads the learned words for the book's language once, when it starts a
  chapter task.

## Left to the builder

The wording on the card and the layout of the Settings table, within the existing design
system.
