"""Book, chapter and settings tools: the same handlers and JSON as the REST routes.

Each tool calls the route function from ``jerome.api.routes_books`` or
``jerome.api.routes_projects`` with the same arguments REST passes, so the same inputs get
the same answers and the same refusals. Writing tools also log one ``agent_actions`` row
in the same transaction.
"""

from typing import Any

from mcp.types import ToolAnnotations

from jerome.api import routes_books, routes_projects
from jerome.api.schemas import (
    AutoApplyBody,
    BookOut,
    ChapterOut,
    ChapterPatch,
    StyleSheetOut,
    StyleSheetUpdate,
)
from jerome.core import books
from jerome.core.agent_actions import record_action
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp
from jerome.orm import Chapter
from jerome.types import Category, Severity, Variant

# Settings changes: they overwrite a value, and the same call twice leaves the same state.
SETTING = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


@mcp.tool(annotations=READ_ONLY)
def list_projects() -> list[dict]:
    """List every book as a project: its stage (set_up, translating, translated,
    proofreading, needs_attention, to_review or done), its active job, suggestion counts and
    latest activity; a translated book also has its `source_variant` and `translation`
    (paragraph counts per status). The same JSON as GET /v1/projects. Each item's `id` is
    the `book_id` the other tools take."""

    def body(c: ToolContext) -> list[dict]:
        return rest_json(routes_projects.list_projects(c.session))

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def list_books() -> list[dict]:
    """List every book, in import order: the same JSON as GET /v1/books. Each book's `id`
    is the `book_id` the other tools take."""

    def body(c: ToolContext) -> list[dict]:
        return [
            BookOut.model_validate(b).model_dump(mode="json") for b in books.list_books(c.session)
        ]

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_book(book_id: int) -> dict[str, Any]:
    """Get one book (`book_id` from `list_books` or `list_projects`): its title, format,
    local-only flag, original filename and import time. Same JSON as GET /v1/books/{id}."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_books.get_book(book_id, c.session), BookOut)

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def list_chapters(book_id: int) -> list[dict]:
    """List a book's chapters in order (`book_id` from `list_books`). Each chapter has its
    `id` (the `chapter_id` other tools take), ordinal, title and `proofread`: whether jobs
    proofread it. Same JSON as GET /v1/books/{id}/chapters."""

    def body(c: ToolContext) -> list[dict]:
        return rest_json(routes_books.list_chapters(book_id, c.session), list[ChapterOut])

    return call(body)


@mcp.tool(annotations=SETTING)
def set_chapter_proofread(chapter_id: int, proofread: bool) -> dict[str, Any]:
    """Choose whether proofreading jobs cover a chapter (`chapter_id` from
    `list_chapters`): false skips it, for front matter and the like. Returns the chapter,
    as `list_chapters` shows it. Logged in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        patch = ChapterPatch(proofread=proofread)
        existing = c.session.get(Chapter, chapter_id)
        before = None if existing is None else {"proofread": existing.proofread}
        chapter = routes_books.patch_chapter(chapter_id, patch, c.session)
        out = rest_json(chapter, ChapterOut)
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="set_chapter_proofread",
            summary=(
                "Marked a chapter as proofread"
                if patch.proofread
                else "Marked a chapter as not proofread"
            ),
            book_id=chapter.book_id,
            chapter_id=chapter.id,
            before=before,
            after={"proofread": out["proofread"]},
        )
        return out

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_style_sheet(book_id: int) -> dict[str, Any]:
    """Get a book's style sheet (`book_id` from `list_books`): its language variant, quote
    style (four characters: primary open/close, then secondary open/close), house
    spellings and ignored words. Same JSON as GET /v1/books/{id}/style-sheet."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_books.get_style_sheet(book_id, c.session), StyleSheetOut)

    return call(body)


@mcp.tool(annotations=SETTING)
def set_style_sheet(
    book_id: int,
    variant: Variant | None = None,
    quote_style: str | None = None,
    house_spellings: dict[str, list[str]] | None = None,
    ignore_words: list[str] | None = None,
) -> dict[str, Any]:
    """Change a book's style sheet (`book_id` from `list_books`). Only the fields given
    change; leave a field out to keep it. `quote_style` is four characters (primary
    open/close, then secondary open/close, e.g. "“”‘’"); `house_spellings` maps the
    preferred spelling to the variants it replaces; `ignore_words` replaces the whole list.
    Returns the full style sheet, as `get_style_sheet` shows it. Logged in the Janus
    activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        update = StyleSheetUpdate(
            variant=variant,
            quote_style=quote_style,
            house_spellings=house_spellings,
            ignore_words=ignore_words,
        )
        before = rest_json(routes_books.get_style_sheet(book_id, c.session), StyleSheetOut)
        after = rest_json(routes_books.put_style_sheet(book_id, update, c.session), StyleSheetOut)
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="set_style_sheet",
            summary="Changed the style sheet",
            book_id=book_id,
            before=before,
            after=after,
        )
        return after

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_auto_apply(book_id: int) -> dict[str, Any]:
    """Get a book's auto-apply policy (`book_id` from `list_books`): whether a job accepts
    clear-cut AI suggestions itself (a person can still reverse them), for which categories,
    from which severity up, and whether house-rule fixes (house spellings, straight quotes)
    count as clear-cut. Same JSON as GET /v1/books/{id}/auto-apply."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_books.get_auto_apply(book_id, c.session), AutoApplyBody)

    return call(body)


@mcp.tool(annotations=SETTING)
def set_auto_apply(
    book_id: int,
    enabled: bool,
    categories: list[Category],
    min_severity: Severity,
    house_rules: bool,
) -> dict[str, Any]:
    """Replace a book's auto-apply policy (`book_id` from `list_books`); every field is
    required, so read it with `get_auto_apply` first and change what you need. Applies to
    suggestions from later jobs. Returns the policy, as `get_auto_apply` shows it. Logged
    in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        policy = AutoApplyBody(
            enabled=enabled,
            categories=categories,
            min_severity=min_severity,
            house_rules=house_rules,
        )
        before = rest_json(routes_books.get_auto_apply(book_id, c.session), AutoApplyBody)
        after = rest_json(routes_books.put_auto_apply(book_id, policy, c.session), AutoApplyBody)
        if before["enabled"] == after["enabled"]:
            summary = "Changed auto-apply settings"
        else:
            summary = "Turned auto-apply on" if after["enabled"] else "Turned auto-apply off"
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="set_auto_apply",
            summary=summary,
            book_id=book_id,
            before=before,
            after=after,
        )
        return after

    return call(body)
