# LanguageTool's options and the AI's three ratings: design

Approved by the owner on 2026-11-25 ("Approve"). This builds on the AI check of LanguageTool
marks (`2026-11-24-lt-ai-check-design.md`).

## Why

LanguageTool often offers several replacements for one word, and Jerome keeps only the
first. The owner reviews with the AI's help and wants to see all of LanguageTool's options,
with the AI choosing among them instead of judging the first one alone. The owner also wants
the AI to say how sure it is: definitely an error, maybe an error, or definitely not an
error.

## Goals

- Keep up to 5 of LanguageTool's replacements for each new mark, in LanguageTool's order.
  The first one stays the mark's `replacement`.
- Keep LanguageTool's rule id on each new mark. The next project, learning for all books,
  needs it.
- The review card lists the options when there are two or more. Clicking an option accepts
  the mark with that text.
- The AI check sends every option and asks for one of three ratings:
  - `error`: definitely an error. The AI names its fix: one of the options word for word,
    or its own text.
  - `maybe_error`: it could be an error. The AI also names the fix it would pick.
  - `not_an_error`: definitely fine as it stands. The AI uses it only when it is certain;
    when in doubt it must answer `maybe_error`.
- The card shows the rating in colour (red, amber, green), the AI's fix and the reason.
  - `error`: the AI's fix is the main button. That is Accept when the fix is LanguageTool's
    first option, and otherwise Use AI fix.
  - `maybe_error`: no main button.
  - `not_an_error`: Reject is the main button, as today.

## Non-goals

- Learning for all books ("Remember for all books", a list of learned words). That is the
  next PR.
- Keeping options for marks made before this change. Old marks have only their one
  replacement, and the card treats it as the only option.
- Options for model or consistency marks.

## Decisions

- **Migration 0011.**
  - Adds `options` (JSONB list of strings, nullable) and `lt_rule` (String, nullable) to
    `suggestions`.
  - Existing ratings change: `correct` becomes `error`, with `ai_replacement` set to the
    mark's replacement, and `better` becomes `error`, keeping its `ai_replacement`.
  - The check constraint changes to the three new values.
  - Production has no ratings yet, because no AI key was set.
- **Which options are kept.** An option is dropped when it equals the original text, repeats
  an earlier option, or fails `check_replacement_text`. At most 5 are kept.
- **A reply without a fix.** When `error` or `maybe_error` comes back with an empty fix, the
  fix is LanguageTool's first option.
- **Only one fix per mark is the AI's.** Choosing another option on the card is the
  reviewer's choice and is recorded as an edit, the same as Use AI fix.
- **The API.** It returns `options` (null for old marks) and `lt_rule`. MCP shows the same
  fields, and the `ai_check_suggestion` description lists the three ratings.

## Left to the builder

The wording and layout of the options list on the card, within the design system that is
already there.
