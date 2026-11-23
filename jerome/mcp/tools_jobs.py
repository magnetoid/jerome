"""Job tools: the handlers and JSON of the REST /v1 job routes.

Arguments are checked by building the route's own request model (``EstimateIn``,
``JobIn``, ``ResumeIn``) first, so an unknown pass, an empty list or a max_cost that is not
positive is refused exactly as REST refuses it.
"""

from decimal import Decimal
from typing import Any

from jerome.api import routes_jobs
from jerome.api.routes_jobs import EstimateIn, JobIn, ResumeIn
from jerome.core.agent_actions import record_action
from jerome.jobs.estimate import PassName
from jerome.mcp.context import ToolContext, call
from jerome.mcp.rest import READ_ONLY, rest_json
from jerome.mcp.server import mcp


def _given(**kwargs: Any) -> dict[str, Any]:
    """The arguments the caller set; a None means "use the route's default"."""
    return {k: v for k, v in kwargs.items() if v is not None}


@mcp.tool(annotations=READ_ONLY)
def estimate_job(
    book_id: int,
    passes: list[PassName] | None = None,
    chapters: list[int] | None = None,
) -> dict[str, Any]:
    """Estimate what a proofreading job would cost, without starting it or paying for
    anything. `book_id` from `list_books`; `passes` defaults to all three
    (languagetool, ai, consistency); `chapters` (ids from `list_chapters`) defaults to
    every chapter marked proofread. Returns the chunk count and low/high cost per pass and
    in total, in USD as strings: the same JSON as POST /v1/books/{id}/estimate. Call this
    before `start_job` and choose its `max_cost` from the high total."""

    def body(c: ToolContext) -> dict[str, Any]:
        request = EstimateIn(**_given(passes=passes, chapters=chapters))
        return rest_json(routes_jobs.estimate(book_id, request, c.session, c.registry))

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def list_jobs(book_id: int) -> list[dict]:
    """List a book's jobs, newest first (`book_id` from `list_books`), each with its status,
    cost so far and per-chapter tasks: the same JSON as GET /v1/books/{id}/jobs."""

    def body(c: ToolContext) -> list[dict]:
        return rest_json(routes_jobs.list_for_book(book_id, c.session))

    return call(body)


@mcp.tool(annotations=READ_ONLY)
def get_job(job_id: int) -> dict[str, Any]:
    """Get one job (`job_id` from `start_job` or `list_jobs`): its status (queued, running,
    paused_cost_cap, completed, failed or cancelled), cost so far, stats and per-chapter
    task progress. The same JSON as GET /v1/jobs/{id}."""

    def body(c: ToolContext) -> dict[str, Any]:
        return rest_json(routes_jobs.get(job_id, c.session))

    return call(body)


@mcp.tool()
def start_job(
    book_id: int,
    max_cost: Decimal,
    passes: list[PassName] | None = None,
    chapters: list[int] | None = None,
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Start a proofreading job on a book. This runs paid model work: call `estimate_job`
    first with the same `passes` and `chapters`, and set `max_cost` (USD, more than 0, up to
    4 decimal places) to at least its high total; the job pauses when it reaches the cap
    (`resume_job` can raise it). `book_id` from `list_books`; `passes` and `chapters` as in
    `estimate_job`; `callback_url` (http or https) gets a POST when the job completes,
    fails or pauses. A book has at most one active job. Returns the job, as `get_job` shows
    it; its suggestions then appear in `list_suggestions`. Logged in the Janus activity
    log."""

    def body(c: ToolContext) -> dict[str, Any]:
        request = JobIn(
            type="proofread",
            max_cost=max_cost,
            **_given(passes=passes, chapters=chapters, callback_url=callback_url),
        )
        job = rest_json(
            routes_jobs.create(book_id, request, c.session, c.registry, c.principal.actor)
        )
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="start_job",
            summary=f"Started job {job['id']}",
            book_id=job["book_id"],
            job_id=job["id"],
        )
        return job

    return call(body)


@mcp.tool()
def cancel_job(job_id: int) -> dict[str, Any]:
    """Cancel a queued, running or paused job (`job_id` from `start_job` or `list_jobs`).
    Suggestions it already made stay. Returns the job, as `get_job` shows it. Logged in the
    Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        job = rest_json(routes_jobs.cancel(job_id, c.session))
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="cancel_job",
            summary=f"Cancelled job {job['id']}",
            book_id=job["book_id"],
            job_id=job["id"],
        )
        return job

    return call(body)


@mcp.tool()
def resume_job(job_id: int, max_cost: Decimal | None = None) -> dict[str, Any]:
    """Resume a job that paused at its cost cap, failed or was cancelled (`job_id` from
    `list_jobs`); it continues where it stopped, and runs paid model work. A job paused at
    its cap needs a higher `max_cost` (USD, more than 0); leave it out to keep the current
    cap. Returns the job, as `get_job` shows it. Logged in the Janus activity log."""

    def body(c: ToolContext) -> dict[str, Any]:
        request = ResumeIn(max_cost=max_cost)
        job = rest_json(routes_jobs.resume(job_id, c.session, request))
        record_action(
            c.session,
            actor=c.principal.actor,
            tool="resume_job",
            summary=f"Resumed job {job['id']}",
            book_id=job["book_id"],
            job_id=job["id"],
        )
        return job

    return call(body)
