"""Review tools: chapter progress, the review page's blocks, suggestions and one block.

The read tools call the handlers of ``jerome.api.routes_projects`` and
``jerome.api.routes_suggestions``. Paging bounds are the routes' own (``Query(ge=...)``),
declared on the tool arguments so a bad value is refused as ``invalid:`` before the body.
"""

from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import Response

from jerome.api import routes_activity, routes_projects, routes_suggestions
from jerome.api.schemas import (
    ActivityItemOut,
    BlockOut,
    BulkBody,
    EditBody,
    SuggestionFilterIn,
    SuggestionOut,
)
from jerome.core.agent_actions import record_action
from jerome.core.suggestions import bulk_decide as bulk_decide_core
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp
from jerome.mcp.tools_translation import CALLS_MODEL
from jerome.types import Category, Severity, Source, Status


@mcp.tool(annotations=READ_ONLY)
def get_chapter_summary(book_id: int) -> list[dict]:
    """Review progress per chapter of a book (`book_id` from `list_books`), in order: each
    chapter's `chapter_id`, ordinal, title, whether it is proofread, and how many of its
    suggestions are open, auto-applied and decided. The same JSON as
    GET /v1/books/{id}/chapter-summary. Use it to pick a chapter for `get_chapter_review`."""

    def body(c: ToolContext) -> list[dict]:
        return rest_json(routes_projects.chapter_summary(book_id, c.session))

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_chapter_review(
    chapter_id: int,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """One page of a chapter's blocks for review (`chapter_id` from `list_chapters` or
    `get_chapter_summary`): each block's `id`, its original and current text (with
    accepted changes), and its suggestions with their `id`, span, replacement, category,
    severity and status. `total_blocks` says how many blocks the chapter has; page with
    `offset` and `limit` (at most 200). The same JSON as GET /v1/chapters/{id}/review."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_projects.chapter_review(chapter_id, c.session, offset, limit))

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def list_suggestions(
    book_id: int,
    chapter_id: int | None = None,
    category: Category | None = None,
    severity: Severity | None = None,
    min_severity: Severity | None = None,
    source: Source | None = None,
    status: Status | None = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """List a book's suggestions (`book_id` from `list_books`), in book order, optionally
    filtered: `chapter_id` from `list_chapters`, an exact `severity` or a `min_severity`
    and up, a `category`, a `source` and a `status` (open is what still needs a decision).
    Returns `{"suggestions": [...], "total": n}`: one page (`limit` at most 500, from
    `offset`) of the same JSON as GET /v1/books/{id}/suggestions, and `total`, the number
    of matches before paging. Each suggestion's `id` is what the decision tools take."""

    def body(c: ToolContext) -> dict[str, Any]:
        response = Response()
        rows = routes_suggestions.list_suggestions(
            book_id,
            c.session,
            response,
            chapter_id=chapter_id,
            category=category,
            severity=severity,
            min_severity=min_severity,
            source=source,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {
            "suggestions": rest_json(rows, list[SuggestionOut]),
            "total": int(response.headers["X-Total-Count"]),
        }

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_block(book_id: int, block_id: int) -> dict[str, Any]:
    """One block of a book (`block_id` from `get_chapter_review` or a suggestion's
    `block_id`): its original text, its current text with accepted changes, and all its
    suggestions. The same JSON as GET /v1/books/{book_id}/blocks/{block_id}."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_suggestions.get_block(book_id, block_id, c.session), BlockOut)

    return call(body)


# Decisions are reversible (`decide_suggestion` with `undo`), so none is destructive; the same
# call twice is not a no-op (a second accept is refused), so none is idempotent.
DECISION = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


@mcp.tool(annotations=DECISION)
def decide_suggestion(
    suggestion_id: int, action: Literal["accept", "reject", "undo"]
) -> dict[str, Any]:
    """Accept, reject or undo the decision on one suggestion (`suggestion_id` from
    `list_suggestions` or `get_chapter_review`). Recorded as your decision, so an editor
    sees it as Janus's and can undo it in the app. Refused with `conflict:` when the
    suggestion cannot take that action now (already decided, or it overlaps an accepted
    one). Returns the suggestion: the same JSON as POST /v1/suggestions/{id}/{action}."""

    def body(c: ToolContext) -> dict[str, Any]:
        out = rest_json(
            routes_suggestions.decide(suggestion_id, action, c.session, c.principal),
            SuggestionOut,
        )
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="decide_suggestion",
            summary={
                "accept": "Accepted a suggestion",
                "reject": "Rejected a suggestion",
                "undo": "Undid a decision",
            }[action],
            book_id=out["book_id"],
            chapter_id=out["chapter_id"],
            after={"suggestion_ids": [out["id"]]},
        )
        return out

    return call(body)


@mcp.tool(annotations=DECISION)
def edit_suggestion(suggestion_id: int, replacement: str) -> dict[str, Any]:
    """Accept a suggestion with your own wording: `replacement` is the text to put in its
    place (`suggestion_id` from `list_suggestions`). Recorded as your decision and undoable
    in the app. The same JSON as POST /v1/suggestions/{id}/edit."""

    def body(c: ToolContext) -> dict[str, Any]:
        edit = EditBody(replacement=replacement)
        out = rest_json(
            routes_suggestions.edit(suggestion_id, edit, c.session, c.principal), SuggestionOut
        )
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="edit_suggestion",
            summary="Edited a suggestion",
            book_id=out["book_id"],
            chapter_id=out["chapter_id"],
            after={"suggestion_ids": [out["id"]]},
        )
        return out

    return call(body)


@mcp.tool(annotations=CALLS_MODEL)
def ai_check_suggestion(suggestion_id: int) -> dict[str, Any]:
    """Ask the AI whether a mark LanguageTool made is right (`suggestion_id` from
    `list_suggestions`: an open suggestion with `source` "languagetool", not `agreed`, and no
    `ai_verdict` yet). The AI weighs all of LanguageTool's `options`. The rating is kept on
    the suggestion: `ai_verdict` is `error` (definitely an error), `maybe_error` or
    `not_an_error` (definitely fine: reject it), with `ai_reason`. With either error rating,
    `ai_replacement` is the AI's fix: when it differs from `replacement`, use it with
    `edit_suggestion`. It decides nothing. A paid model call on the ai pass model, billed to
    the proofreading job, under the key's per-minute rate limit. The same JSON as POST
    /v1/suggestions/{id}/ai-check. Logged in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        out = rest_json(
            routes_suggestions.ai_check_suggestion(
                suggestion_id, c.session, c.registry, c.principal.actor, c.app.state.text_limits
            ),
            SuggestionOut,
        )
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="ai_check_suggestion",
            summary="Asked the AI about a LanguageTool mark",
            book_id=out["book_id"],
            chapter_id=out["chapter_id"],
            after={"suggestion_ids": [out["id"]], "ai_verdict": out["ai_verdict"]},
        )
        return out

    return call(body)


@mcp.tool(annotations=DECISION)
def bulk_decide(
    book_id: int,
    action: Literal["accept", "reject"],
    ids: list[int] | None = None,
    filter: SuggestionFilterIn | None = None,
) -> dict[str, Any]:
    """Accept or reject many suggestions of a book (`book_id` from `list_books`) in one
    call: either the listed `ids`, or every open suggestion matching `filter` (`chapter_id`,
    `category`, `severity`, `min_severity`, `source`, `status`). Recorded as your decisions
    and undoable one by one in the app. Returns `{"decided": n}`, the same JSON as
    POST /v1/suggestions/bulk."""

    def body(c: ToolContext) -> dict[str, Any]:
        request = BulkBody(book_id=book_id, ids=ids, filter=filter, action=action)
        decided = bulk_decide_core(
            c.session,
            book_id,
            ids=request.ids,
            f=routes_suggestions._to_filter(request.filter) if request.filter else None,
            action=request.action,
            actor=c.principal.actor,
            channel=c.principal.channel,
        )
        out = {"decided": len(decided)}
        n = len(decided)
        noun = "suggestion" if n == 1 else "suggestions"
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="bulk_decide",
            summary=f"{'Accepted' if action == 'accept' else 'Rejected'} {n} {noun}",
            book_id=book_id,
            after={"decided": n, "suggestion_ids": decided},
        )
        return out

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def list_activity(
    book_id: int | None = None,
    chapter_id: int | None = None,
    who: Literal["people", "agent", "automatic", "api"] | None = None,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Everything done, newest first: each correction decision by anyone (people, you,
    automatic apply, Word import, API keys) and your other logged changes. Optionally for
    one book (`book_id`) or chapter (`chapter_id`), or one kind of author (`who`). A
    correction row (`kind` "decision") with `revertable` true can be undone with
    `revert_decision` and its `id`. Returns `{"items": [...], "total": n}`; the items are
    the same JSON as GET /v1/activity."""

    def body(c: ToolContext) -> dict[str, Any]:
        response = Response()
        items = routes_activity.list_activity(
            c.session, response, book_id, chapter_id, who, limit, offset
        )
        return {
            "items": rest_json(items, list[ActivityItemOut]),
            "total": int(response.headers["X-Total-Count"]),
        }

    return call(body)


@mcp.tool(annotations=DECISION)
def revert_decision(decision_id: int) -> dict[str, Any]:
    """Revert one correction decision (`id` of a `list_activity` row whose `kind` is
    "decision"): its suggestion goes back to open and what it displaced comes back.
    Refused with `conflict:` when the suggestion was decided again later or the row is
    itself an undo. Returns the suggestion: the same JSON as
    POST /v1/activity/decisions/{id}/revert."""

    def body(c: ToolContext) -> dict[str, Any]:
        out = rest_json(
            routes_activity.revert_decision(decision_id, c.session, c.principal), SuggestionOut
        )
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="revert_decision",
            summary="Reverted a decision",
            book_id=out["book_id"],
            chapter_id=out["chapter_id"],
            after={"decision_id": decision_id, "suggestion_ids": [out["id"]]},
        )
        return out

    return call(body)
