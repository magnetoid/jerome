# AI check of LanguageTool marks — design

Approved by the owner on 2026-11-24 ("Na klik (A)", then "Odobravam oba"). A small project.

## Intent

In review, LanguageTool marks things that are not errors (names, style, deliberate forms),
and a mark found by LanguageTool alone carries no AI opinion. The owner wants to ask the AI
about one mark, on that spot, and act on its answer in one click.

## Goals

- On the focus card of an open suggestion found by LanguageTool alone (`source ==
  "languagetool"`, not `agreed`), an **Ask AI** button.
- The AI sees the paragraph, the previous paragraph, the mark (its span, its fix, its
  message) and the book's house style; on a translated book also the source paragraph.
- It answers one of three verdicts, with a one-sentence reason:
  - `correct`: LanguageTool is right; its fix stands.
  - `not_an_error`: Reject becomes the card's main button. Nothing is rejected by itself.
  - `better`: a better text for the same span; **Use AI fix** accepts the suggestion with that
    text (the existing edit action).
- The verdict is saved with the suggestion, so the same question is not paid twice, and it
  shows again when the card is opened later.
- In the review toolbar, **Check LT marks in this chapter** asks about the chapter's open
  LanguageTool-only marks that have no verdict, grouped by paragraph, at most 40 per click.
  It reports "Checked 40 of 63; click again for the rest".
- An MCP tool `ai_check_suggestion`, so an agent can ask too.

## Non-goals

- No automatic check during the job (the owner chose on-click).
- The AI does not widen or move the span; a fix for elsewhere is not offered.
- Marks from the AI or the consistency pass get no button: they already have the AI's view.

## Approaches

- **On click, saved verdict (chosen by the owner).** Pays only for what is looked at.
- Automatic in the job: a verdict on every LanguageTool mark; costs on every mark in the book.

## Design

- Model: the `ai` pass (`models.ai`), through `Registry.for_pass("ai", local_only=...)`; a
  local-only book uses a local model or gets a clear 409.
- `POST /v1/suggestions/{id}/ai-check` checks one; `POST /v1/books/{book_id}/chapters/{chapter_id}/ai-check`
  checks the chapter's batch. Both return the suggestions with their verdicts.
- Saved on the suggestion: `ai_verdict` (`correct` | `not_an_error` | `better`),
  `ai_replacement` (only for `better`), `ai_reason`, `ai_model`. A migration adds them.
- A suggestion that is not open, not from LanguageTool alone, or that already has a verdict
  is refused with 409 (single) or skipped (chapter).
- Billing, like translating one paragraph again: the call is billed to the suggestion's job
  (else the book's latest proofreading job) in its own transaction, so the spend is kept even
  when the write is refused. The per-key per-minute rate limit applies (one token per click).
  The daily cap of `/v1/proofread/text` does not, since that cap counts only spend outside
  jobs. (My call; this corrects the "daily limit" in the proposal.)
- A reply that cannot be used (bad JSON, unknown verdict, an empty `better`, a `better` equal
  to the original) saves nothing and returns 502 with a plain message; the spend stays billed.
- Web: the card shows the verdict and reason under "Why". `better` shows the AI text and
  **Use AI fix**. `not_an_error` makes Reject the primary button. The chapter button sits in
  the review toolbar.

## Left to the builder

The prompt wording (English and German, beside the proofreading prompts), the reply schema's
exact shape, and the card's copy.
