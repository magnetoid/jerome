# Decided marks stay reachable in review

**Status:** approved by the owner (design A), 2026-11-25.

## Intent

After a person accepts, edits or rejects a mark in **Needs you**, the mark looks gone. It stays in the text, but it cannot be clicked, and the card has already moved on. The owner wants a decided mark to stay reachable, on a card that shows:

- what changed (before → after);
- who changed it, and when;
- a **Revert** button.

## Goals

- In every view, a click on a decided mark (accepted, edited or rejected, including AI-applied changes) opens its card. Superseded marks are still never marked.
- The card of a decided mark shows the change, then "Accepted by X · 25 Nov 2026, 10:42", then **Revert (u)**. Revert puts the mark back to open: this is the existing undo.
- `j`/`k` still move only through the view's own marks. From a decided mark that was clicked, `j` goes to the next view mark after it in the text, and `k` to the one before it.
- After a decision, the card still moves on by itself, as it does today.
- The API sends `decided_at`, the time of the mark's latest decision (null when there is none). It is read the same way as `decided_by`.

## Non-goals

- A full history of each mark. That is on the Activity page, which already has Revert for every decision.
- New permissions. Revert is the existing undo, so anyone who can decide can revert.

## Approaches

- **A (chosen):** decided marks can be clicked in every view, and `j`/`k` stay on the view's marks. It is a small change and keeps review fast.
- **B:** decided marks stay in the `j`/`k` list until the page is reloaded. It is easier to step back, but slower to move through a chapter after many decisions.

## Builder's calls

- The Undo button reads **Revert** when the focused mark is decided and undoes that mark. Otherwise it reads **Undo** and undoes the person's last decision in the chapter, as today.
- A decided mark that is clicked stays focused across refetches while it stays decided. Once reverted, it is open again and an ordinary view mark.
