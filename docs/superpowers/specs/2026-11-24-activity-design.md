# Activity: everything done on a project, with revert

Short design, approved by the owner on 2026-11-24.

## Why

The owner wants one place that shows everything that was done on a project, by people and by the agent. A single proofreading correction must be revertable from there.

## What

- The "Janus activity" page becomes **Activity**. It lists, newest first:
  - every correction decision (accept, reject, edit, undo) by anyone: people in the web app, the agent over MCP, automatic apply, Word review import, API keys;
  - the agent's other logged changes (`agent_actions`): settings, jobs, files, translation.
- **Not listed:**
  - `system` decisions (supersede and reopen), which are consequences of another decision;
  - the agent's decision rows (`decide_suggestion`, `edit_suggestion`, `bulk_decide`), whose decisions are already listed one by one.
- **Who:**
  - people by their login (the decision's actor);
  - the agent as "Janus (key)";
  - automatic apply as "Automatic";
  - Word import as "<actor> (Word)";
  - API keys as "<key> (API)".
- **Filters:**
  - who: people (web, word), agent (mcp), automatic (auto), API (api);
  - chapter, when one book is shown.
  - The sidebar's Activity shows all projects; a project's link shows that project.
- **Revert:**
  - Each correction row has **Revert**. It undoes that one decision through the existing `decide(..., "undo")`: the suggestion goes back to open, and what it displaced comes back.
  - Revert is allowed only when that decision is still the suggestion's latest decision and left it accepted, rejected or edited. Otherwise the button is disabled with the reason.
  - An undo row is not revertable.
  - The revert is itself a new decision, so it shows as a new row.
- Only corrections are revertable for now. The other rows are shown without a button.

## How

- `jerome/core/activity.py`:
  - `list_activity(session, book_id, chapter_id, who, limit, offset)` pages a UNION of decision ids and agent-action ids ordered by time, then loads the page's details;
  - `revert_decision(session, decision_id, actor, channel)`.
- REST:
  - `GET /v1/activity` (X-Total-Count);
  - `POST /v1/activity/decisions/{id}/revert`.
- MCP:
  - `list_activity`;
  - `revert_decision`, which is logged as an agent action like the other decision tools, so its decision row is the one listed.
- Web: `ActivityPage` uses the new endpoint. The old `/v1/agent-actions` stays for existing clients.
- No migration.
