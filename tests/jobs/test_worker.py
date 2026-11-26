import hmac
import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event, func, select, update

from jerome.core.auto_apply import DEFAULT_POLICY, policy_to_json
from jerome.errors import Conflict
from jerome.jobs.service import cancel_job, resume_job
from jerome.jobs.webhooks import send_webhook
from jerome.jobs.worker import WorkerDeps, claim_task, run_task, work_forever
from jerome.models import FakeProvider, ModelOutputError, ModelRefusal, Registry, Usage
from jerome.models.base import CompletionResult
from jerome.orm import (
    Block,
    Book,
    Chapter,
    Decision,
    Job,
    JobTask,
    LearnedWord,
    ModelUsage,
    StyleSheetRow,
    Suggestion,
)
from jerome.proofread.languagetool import LanguageToolClient

LT = "http://lt.test"
HOOK = "http://hook.test/cb"
BIG = " ".join(["word"] * 1500)  # two of these do not fit one 2,500-word chunk
ALL_PASSES = ["languagetool", "ai", "consistency"]


def reply(*sugs):
    return {"suggestions": list(sugs)}


def make_deps(test_settings, provider, lt_http=None, *, secret=None, sleep_base=10.0):
    settings = test_settings.model_copy(
        update={"models": {"ai": {"provider": "fake", "model": "claude-opus-5-5"}}}
    )
    registry = Registry(settings, {"fake": provider})
    lt = LanguageToolClient(LT, lt_http or httpx.Client())
    return WorkerDeps(registry, lt, secret, sleep_base=sleep_base)


@pytest.fixture
def lt_route(respx_mock):
    return respx_mock.post(f"{LT}/v2/check").respond(json={"matches": []})


def build(
    sf,
    chapters,
    *,
    local_only=False,
    passes=ALL_PASSES,
    consistency=False,
    callback_url=None,
    max_cost=Decimal(5),
    house=None,
    chapter_ids=None,
    auto_apply=None,
    variant="en-US",
    quote_style="“”‘’",
):
    """Committed book + job. `chapters` is a list of lists of block texts. Without
    `auto_apply` the book gets the column default, auto-apply off."""
    with sf() as s:
        book = Book(title="B", format="docx", local_only=local_only)
        if auto_apply is not None:
            book.auto_apply = policy_to_json(auto_apply)
        book.style_sheet = StyleSheetRow(
            variant=variant,
            quote_style=quote_style,
            house_spellings=house or {},
            ignore_words=[],
        )
        s.add(book)
        s.flush()
        job = Job(
            book_id=book.id,
            type="proofread",
            status="queued",
            passes=list(passes),
            chapter_ids=chapter_ids,
            max_cost=max_cost,
            callback_url=callback_url,
        )
        block_ids = []
        for i, texts in enumerate(chapters):
            ch = Chapter(book_id=book.id, ordinal=i, title=f"C{i}", proofread=True)
            s.add(ch)
            s.flush()
            for j, body in enumerate(texts):
                b = Block(
                    chapter_id=ch.id,
                    book_id=book.id,
                    ordinal=j,
                    kind="paragraph",
                    text=body,
                    hash=f"h{ch.id}-{j}",
                )
                s.add(b)
                s.flush()
                block_ids.append(b.id)
            job.tasks.append(JobTask(kind="chapter", chapter_id=ch.id))
        if consistency:
            job.tasks.append(JobTask(kind="consistency"))
        s.add(job)
        s.commit()
        return SimpleNamespace(
            job_id=job.id,
            book_id=book.id,
            task_ids=[t.id for t in job.tasks],
            task_id=job.tasks[0].id,
            block_ids=block_ids,
        )


@pytest.fixture
def job_two_chunks(session_factory):
    return build(session_factory, [[BIG, BIG]])


@pytest.fixture
def job_three_chunks(session_factory):
    return build(session_factory, [[BIG, BIG, BIG]])


@pytest.fixture
def job_two_chapters(session_factory):
    return build(session_factory, [["One."], ["Two."]], consistency=True)


def task(sf, task_id):
    with sf() as s:
        t = s.get(JobTask, task_id)
        s.expunge(t)
        return t


def job(sf, job_id):
    with sf() as s:
        j = s.get(Job, job_id)
        s.expunge(j)
        return j


def count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


@pytest.fixture
def deps_fake(test_settings, lt_route):
    return make_deps(test_settings, FakeProvider([reply(), reply()]))


def test_commits_each_chunk(session_factory, deps_fake, job_two_chunks):
    run_task(session_factory, job_two_chunks.task_id, deps_fake)
    t = task(session_factory, job_two_chunks.task_id)
    assert t.chunks_done == 2 and t.status == "done"
    assert count(session_factory, ModelUsage) == 2
    j = job(session_factory, job_two_chunks.job_id)
    assert j.cost_actual == Decimal("0.014") and j.status == "completed" and j.finished_at


def test_resume_continues_from_next_chunk(
    session_factory, test_settings, lt_route, job_three_chunks
):
    provider = FakeProvider([reply(), RuntimeError("boom"), reply(), reply()])
    deps = make_deps(test_settings, provider)
    tid = job_three_chunks.task_id
    run_task(session_factory, tid, deps)  # chunk 1 ok, chunk 2 fails
    assert task(session_factory, tid).chunks_done == 1
    run_task(session_factory, tid, deps)  # retry
    assert task(session_factory, tid).chunks_done == 3
    assert len(provider.calls) == 4  # chunk 1 was never bought twice


def test_failed_chunk_commits_nothing(session_factory, test_settings, lt_route, job_two_chunks):
    deps = make_deps(test_settings, FakeProvider([RuntimeError("boom")]))
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deps.now = lambda: now
    run_task(session_factory, job_two_chunks.task_id, deps)
    t = task(session_factory, job_two_chunks.task_id)
    assert count(session_factory, Suggestion) == 0 and count(session_factory, ModelUsage) == 0
    assert (t.chunks_done, t.attempts, t.status) == (0, 1, "queued")
    assert t.not_before == now + timedelta(seconds=20)
    assert "boom" in t.error


def test_three_failures_fail_job_and_send_webhook(
    session_factory, test_settings, lt_route, respx_mock
):
    ids = build(session_factory, [[BIG]], callback_url=HOOK)
    hook = respx_mock.post(HOOK).respond(200)
    deps = make_deps(test_settings, FakeProvider([RuntimeError("x")] * 3), secret="s3cret")
    for _ in range(3):
        run_task(session_factory, ids.task_id, deps)
    assert task(session_factory, ids.task_id).status == "failed"
    j = job(session_factory, ids.job_id)
    assert j.status == "failed" and j.finished_at
    assert hook.call_count == 1
    assert json.loads(hook.calls[0].request.content)["event"] == "job.failed"


def test_cost_cap_pauses_then_resume_continues(
    session_factory, test_settings, lt_route, respx_mock
):
    ids = build(session_factory, [[BIG, BIG, BIG]], max_cost=Decimal("0.01"), callback_url=HOOK)
    hook = respx_mock.post(HOOK).respond(200)
    provider = FakeProvider([reply(), reply(), reply()])
    deps = make_deps(test_settings, provider)
    run_task(session_factory, ids.task_id, deps)
    j = job(session_factory, ids.job_id)
    assert j.status == "paused_cost_cap" and j.cost_actual == Decimal("0.014")
    assert task(session_factory, ids.task_id).chunks_done == 2
    assert task(session_factory, ids.task_id).status == "queued"
    assert len(provider.calls) == 2
    # the job.paused webhook
    assert [json.loads(c.request.content)["event"] for c in hook.calls] == ["job.paused"]
    body = json.loads(hook.calls[0].request.content)
    assert body["status"] == "paused_cost_cap" and body["cost_actual"] == "0.0140"

    with session_factory() as s:
        resume_job(s, ids.job_id, max_cost=Decimal(100))
        s.commit()
    run_task(session_factory, ids.task_id, deps)
    assert len(provider.calls) == 3
    assert job(session_factory, ids.job_id).status == "completed"
    assert [json.loads(c.request.content)["event"] for c in hook.calls][-1] == "job.completed"


def test_cancel_stops_between_chunks(session_factory, test_settings, lt_route, job_two_chunks):
    def cancel_mid_call(_kwargs):
        with session_factory() as s:
            cancel_job(s, job_two_chunks.job_id)
            s.commit()
        return reply()

    provider = FakeProvider([cancel_mid_call, reply()])
    deps = make_deps(test_settings, provider)
    run_task(session_factory, job_two_chunks.task_id, deps)
    t = task(session_factory, job_two_chunks.task_id)
    assert t.chunks_done == 1 and len(provider.calls) == 1  # paid chunk kept, none after
    assert job(session_factory, job_two_chunks.job_id).status == "cancelled"
    assert count(session_factory, ModelUsage) == 1


def test_stop_between_chunks_requeues_task_and_job(
    session_factory, test_settings, lt_route, job_three_chunks
):
    stop = threading.Event()

    def stop_during_first_call(_kwargs):
        stop.set()  # SIGTERM arrives while the first chunk is with the model
        return reply()

    provider = FakeProvider([stop_during_first_call, reply(), reply()])
    deps = make_deps(test_settings, provider)
    tid = job_three_chunks.task_id
    run_task(session_factory, tid, deps, stop)
    t = task(session_factory, tid)
    assert (t.status, t.chunks_done, t.attempts) == ("queued", 1, 0)
    assert job(session_factory, job_three_chunks.job_id).status == "queued"
    assert len(provider.calls) == 1  # the chunk in flight was kept, no second call
    assert count(session_factory, ModelUsage) == 1
    run_task(session_factory, tid, deps)  # a restarted worker picks it up where it was
    assert task(session_factory, tid).chunks_done == 3 and len(provider.calls) == 3


def test_work_forever_exits_after_a_stop_mid_task(
    session_factory, test_settings, lt_route, job_two_chunks
):
    stop = threading.Event()

    def stop_during_call(_kwargs):
        stop.set()
        return reply()

    deps = make_deps(test_settings, FakeProvider([stop_during_call, reply()]))
    work_forever(session_factory, deps, poll_interval=0.01, stop=stop)  # returns, not hangs
    assert task(session_factory, job_two_chunks.task_id).status == "queued"


def test_consistency_task_waits_for_chapters(session_factory, job_two_chapters):
    with session_factory() as s:
        claimed = claim_task(s)
    assert claimed == job_two_chapters.task_ids[0]
    with session_factory() as s:
        assert claim_task(s) == job_two_chapters.task_ids[1]
    with session_factory() as s:
        assert claim_task(s) is None  # chapters running, consistency not claimable
    with session_factory() as s:  # a finished chapter and a queued one: still not claimable
        s.execute(
            update(JobTask).where(JobTask.id == job_two_chapters.task_ids[0]).values(status="done")
        )
        s.execute(
            update(JobTask)
            .where(JobTask.id == job_two_chapters.task_ids[1])
            .values(status="queued")
        )
        s.commit()
    with session_factory() as s:
        assert claim_task(s) == job_two_chapters.task_ids[1]
    with session_factory() as s:
        s.execute(
            update(JobTask).where(JobTask.id == job_two_chapters.task_ids[1]).values(status="done")
        )
        s.commit()
    with session_factory() as s:
        assert claim_task(s) == job_two_chapters.task_ids[2]


def test_claim_respects_not_before_and_job_status(session_factory, job_two_chapters):
    soon = datetime.now(UTC) + timedelta(hours=1)
    with session_factory() as s:
        s.execute(update(JobTask).values(not_before=soon))
        s.commit()
    with session_factory() as s:
        assert claim_task(s) is None
        assert claim_task(s, now=soon + timedelta(seconds=1)) == job_two_chapters.task_ids[0]
        j = s.get(Job, job_two_chapters.job_id)
        assert j.status == "running"
        t = s.get(JobTask, job_two_chapters.task_ids[0])
        assert t.status == "running" and t.started_at
    with session_factory() as s:
        cancel_job(s, job_two_chapters.job_id)
        s.commit()
    with session_factory() as s:
        assert claim_task(s, now=soon + timedelta(days=1)) is None


def test_local_only_book_skips_hosted_languagetool(session_factory, test_settings, respx_mock):
    ids = build(session_factory, [["teh cat"]], local_only=True, passes=["languagetool"])
    hosted = respx_mock.post("https://api.languagetoolplus.com/v2/check").respond(
        json={"matches": []}
    )
    deps = make_deps(test_settings, FakeProvider([], local=False))
    deps = WorkerDeps(deps.registry, LanguageToolClient("https://api.languagetoolplus.com"), None)
    run_task(session_factory, ids.task_id, deps)
    assert not hosted.called
    assert job(session_factory, ids.job_id).stats["languagetool_skipped_local_only"] == 1
    assert task(session_factory, ids.task_id).status == "done"


def test_languagetool_hosted_by_host():
    assert LanguageToolClient("https://api.languagetoolplus.com").hosted
    assert LanguageToolClient("https://api.languagetool.org/").hosted
    assert not LanguageToolClient("http://languagetool:8010").hosted
    assert not LanguageToolClient("http://localhost:8010").hosted
    assert not LanguageToolClient("https://notlanguagetool.org").hosted


def test_local_only_book_skips_ai(session_factory, test_settings, respx_mock):
    ids = build(
        session_factory, [["teh cat", BIG, BIG]], local_only=True, passes=["languagetool", "ai"]
    )
    respx_mock.post(f"{LT}/v2/check").respond(
        json={
            "matches": [
                {
                    "message": "m",
                    "offset": 0,
                    "length": 3,
                    "replacements": [{"value": "the"}],
                    "rule": {"issueType": "misspelling"},
                }
            ]
        }
    )
    provider = FakeProvider([], local=False)
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert provider.calls == []
    j = job(session_factory, ids.job_id)
    assert j.stats["ai_skipped_local_only"] == 2  # two chunks
    assert count(session_factory, Suggestion) >= 1
    assert count(session_factory, ModelUsage) == 0
    assert task(session_factory, ids.task_id).status == "done"


def test_refusal_counted_and_chunk_continues(session_factory, test_settings, lt_route):
    ids = build(session_factory, [[BIG, BIG]])
    deps = make_deps(test_settings, FakeProvider([ModelRefusal("no"), reply()]))
    run_task(session_factory, ids.task_id, deps)
    t = task(session_factory, ids.task_id)
    assert t.chunks_done == 2 and t.status == "done" and t.attempts == 0
    assert job(session_factory, ids.job_id).stats["refusals"] == 1
    assert count(session_factory, ModelUsage) == 1  # the refused call recorded no usage


def test_completed_job_sends_signed_webhook(session_factory, test_settings, lt_route, respx_mock):
    ids = build(session_factory, [[BIG]], callback_url=HOOK)
    respx_mock.post(HOOK).respond(200)
    deps = make_deps(test_settings, FakeProvider([reply()]), secret="s3cret")
    run_task(session_factory, ids.task_id, deps)
    req = respx_mock.calls[-1].request
    assert req.headers["X-Jerome-Signature"] == (
        "sha256=" + hmac.new(b"s3cret", req.content, "sha256").hexdigest()
    )
    assert req.headers["Content-Type"] == "application/json"
    body = json.loads(req.content)
    assert body["event"] == "job.completed" and body["job_id"] == ids.job_id
    assert body["status"] == "completed" and body["cost_actual"] == "0.0070"
    assert body["stats"]["dropped"] == 0


def test_no_webhook_without_callback_url(session_factory, test_settings, lt_route, respx_mock):
    ids = build(session_factory, [[BIG]])
    run_task(session_factory, ids.task_id, make_deps(test_settings, FakeProvider([reply()])))
    assert job(session_factory, ids.job_id).status == "completed"
    assert [c.request.url.host for c in respx_mock.calls] == ["lt.test"]  # LT only


def test_two_workers_claim_different_tasks(session_factory, job_two_chapters):
    with session_factory() as a, session_factory() as b:
        first, second = claim_task(a), claim_task(b)
        assert first is not None and second is not None and first != second


def test_chunk_index_guard_skips_suggestions_but_records_usage(
    session_factory, test_settings, lt_route
):
    ids = build(session_factory, [["teh " + BIG, BIG]])

    def other_worker_commits_chunk(_kwargs):
        with session_factory() as s:  # a second worker finished chunk 0 first
            s.execute(update(JobTask).where(JobTask.id == ids.task_id).values(chunks_done=1))
            s.commit()
        return reply(
            {
                "block_id": ids.block_ids[0],
                "original": "teh",
                "replacement": "the",
                "category": "typo",
                "severity": "high",
                "explanation": "typo",
                "occurrence": 1,
            }
        )

    provider = FakeProvider([other_worker_commits_chunk])
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert count(session_factory, Suggestion) == 0
    assert count(session_factory, ModelUsage) == 1  # paid for, so accounted for
    assert job(session_factory, ids.job_id).cost_actual == Decimal("0.007")
    assert task(session_factory, ids.task_id).chunks_done == 1  # not double-counted
    assert len(provider.calls) == 1


class OtherModelProvider(FakeProvider):
    def complete(self, **kwargs):
        result = super().complete(**kwargs)
        return CompletionResult(result.data, result.usage, result.provider, "server-fallback-1")


def test_unknown_model_price_falls_back_to_configured(
    session_factory, test_settings, lt_route, caplog
):
    ids = build(session_factory, [[BIG]])
    provider = OtherModelProvider([reply()])
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert task(session_factory, ids.task_id).status == "done"  # the paid chunk is not failed
    with session_factory() as s:
        usage = s.scalars(select(ModelUsage)).one()
        assert usage.model == "server-fallback-1" and usage.cost == Decimal("0.007")
    assert "server-fallback-1" in caplog.text


def test_failure_after_model_call_still_records_usage(
    session_factory, test_settings, lt_route, monkeypatch
):
    ids = build(session_factory, [[BIG]])

    def boom(*args, **kwargs):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr("jerome.jobs.worker.add_candidates", boom)
    run_task(session_factory, ids.task_id, make_deps(test_settings, FakeProvider([reply()])))
    t = task(session_factory, ids.task_id)
    assert (t.chunks_done, t.attempts, t.status) == (0, 1, "queued")
    assert count(session_factory, ModelUsage) == 1  # spend is never lost from accounting
    assert job(session_factory, ids.job_id).cost_actual == Decimal("0.007")


def test_consistency_task_produces_suggestions(session_factory, test_settings, lt_route):
    ids = build(
        session_factory,
        [["The colour red."], ["Another colour."]],
        consistency=True,
        house={"color": ["colour"]},
    )
    deps = make_deps(test_settings, FakeProvider([reply(), reply()]))
    for tid in ids.task_ids[:2]:
        run_task(session_factory, tid, deps)
    assert job(session_factory, ids.job_id).status == "running"
    run_task(session_factory, ids.task_ids[2], deps)
    with session_factory() as s:
        rows = s.scalars(select(Suggestion)).all()
        assert {r.source for r in rows} == {"consistency"} and len(rows) == 2
        assert all(r.job_id == ids.job_id and r.replacement == "color" for r in rows)
    assert task(session_factory, ids.task_ids[2]).status == "done"
    assert job(session_factory, ids.job_id).status == "completed"


def ai(block_id, original, replacement, category, severity):
    return {
        "block_id": block_id,
        "original": original,
        "replacement": replacement,
        "category": category,
        "severity": severity,
        "explanation": f"A {category} fix.",
        "occurrence": 1,
    }


def decisions(sf):
    """(original, status, actor, channel) of every non-system decision, in order."""
    with sf() as s:
        return s.execute(
            select(Suggestion.original, Suggestion.status, Decision.actor, Decision.channel)
            .join(Decision, Decision.suggestion_id == Suggestion.id)
            .where(Decision.channel != "system")
            .order_by(Decision.id)
        ).all()


def test_chunk_auto_applies_and_counts_in_stats(session_factory, test_settings, lt_route):
    ids = build(
        session_factory,
        [["teh cat sat. " + BIG, "A dog ran ,away. " + BIG]],  # one block per chunk
        passes=["languagetool", "ai"],
        auto_apply=DEFAULT_POLICY,
    )
    first, second = ids.block_ids
    provider = FakeProvider(
        [
            reply(
                ai(first, "teh", "the", "typo", "high"), ai(first, "sat", "sits", "grammar", "high")
            ),
            reply(
                ai(second, "ran ,away", "ran, away", "punctuation", "high"),
                ai(second, "dog", "dogs", "typo", "medium"),
            ),
        ]
    )
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert task(session_factory, ids.task_id).status == "done"
    assert job(session_factory, ids.job_id).stats["auto_applied"] == 2  # summed over chunks
    assert decisions(session_factory) == [
        ("teh", "accepted", "ai", "auto"),
        ("ran ,away", "accepted", "ai", "auto"),
    ]
    with session_factory() as s:
        still_open = s.scalars(select(Suggestion.original).where(Suggestion.status == "open"))
        assert sorted(still_open) == ["dog", "sat"]


def test_languagetool_only_job_applies_nothing(session_factory, test_settings, respx_mock):
    ids = build(
        session_factory, [["Mon cœur bat."]], passes=["languagetool"], auto_apply=DEFAULT_POLICY
    )
    respx_mock.post(f"{LT}/v2/check").respond(
        json={
            "matches": [
                {
                    "message": "Possible spelling mistake.",
                    "offset": 4,
                    "length": 4,
                    "replacements": [{"value": "cur"}],
                    "rule": {"issueType": "misspelling"},
                }
            ]
        }
    )
    provider = FakeProvider([])
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert provider.calls == [] and job(session_factory, ids.job_id).status == "completed"
    with session_factory() as s:
        row = s.scalars(select(Suggestion)).one()
        assert (row.original, row.replacement, row.source) == ("cœur", "cur", "languagetool")
        assert (row.category, row.severity, row.status) == ("typo", "high", "open")
    assert decisions(session_factory) == []
    assert job(session_factory, ids.job_id).stats["auto_applied"] == 0


def test_consistency_pass_applies_house_spellings(session_factory, test_settings, lt_route):
    ids = build(
        session_factory,
        [["The colour red."], ["Another colour."]],
        consistency=True,
        house={"color": ["colour"]},
        auto_apply=DEFAULT_POLICY,
    )
    deps = make_deps(test_settings, FakeProvider([reply(), reply()]))
    for tid in ids.task_ids:
        run_task(session_factory, tid, deps)
    assert job(session_factory, ids.job_id).status == "completed"
    assert decisions(session_factory) == [
        ("colour", "accepted", "ai", "auto"),
        ("colour", "accepted", "ai", "auto"),
    ]
    assert job(session_factory, ids.job_id).stats["auto_applied"] == 2


def test_consistency_pass_applies_straight_quotes_but_leaves_curly_ones_open(
    session_factory, test_settings, lt_route
):
    """In de-DE, “Hallo” would become “Hallo“: a curly quote of another style waits for a
    person, while straight quotes still get the house style."""
    ids = build(
        session_factory,
        [['Er sagte “Hallo”. Sie sagte "Tschüss".']],
        consistency=True,
        variant="de-DE",
        quote_style="„“‚‘",
        auto_apply=DEFAULT_POLICY,
    )
    deps = make_deps(test_settings, FakeProvider([reply()]))
    for tid in ids.task_ids:
        run_task(session_factory, tid, deps)
    assert job(session_factory, ids.job_id).status == "completed"
    assert decisions(session_factory) == [('"', "accepted", "ai", "auto")] * 2
    with session_factory() as s:
        rows = s.execute(
            select(Suggestion.original, Suggestion.replacement, Suggestion.status)
            .where(Suggestion.rule == "quote_style")
            .order_by(Suggestion.start)
        ).all()
    assert rows == [("”", "“", "open"), ('"', "„", "accepted"), ('"', "“", "accepted")]
    assert job(session_factory, ids.job_id).stats["auto_applied"] == 2


def test_auto_apply_off_by_default_for_books_without_a_policy(
    session_factory, test_settings, lt_route
):
    ids = build(session_factory, [["teh cat."]], passes=["ai"])  # the column default: off
    provider = FakeProvider([reply(ai(ids.block_ids[0], "teh", "the", "typo", "high"))])
    run_task(session_factory, ids.task_id, make_deps(test_settings, provider))
    assert count(session_factory, Suggestion) == 1 and decisions(session_factory) == []
    assert job(session_factory, ids.job_id).stats["auto_applied"] == 0


def test_job_completes_only_when_every_task_done(session_factory, job_two_chapters, test_settings):
    provider = FakeProvider([reply(), reply()])
    lt = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"matches": []}))
    )
    deps = make_deps(test_settings, provider, lt)
    first, second, cons = job_two_chapters.task_ids
    run_task(session_factory, first, deps)
    assert job(session_factory, job_two_chapters.job_id).status == "running"
    run_task(session_factory, second, deps)
    assert job(session_factory, job_two_chapters.job_id).status == "running"
    run_task(session_factory, cons, deps)
    j = job(session_factory, job_two_chapters.job_id)
    assert j.status == "completed" and j.finished_at


def test_consistency_task_failure_retries(session_factory, test_settings, lt_route, monkeypatch):
    ids = build(session_factory, [["x"]], consistency=True)
    monkeypatch.setattr("jerome.jobs.worker.consistency_candidates", lambda *a, **k: 1 / 0)
    run_task(session_factory, ids.task_ids[1], make_deps(test_settings, FakeProvider([])))
    t = task(session_factory, ids.task_ids[1])
    assert (t.status, t.attempts) == ("queued", 1) and "ZeroDivision" in t.error


def test_send_webhook_retries_then_gives_up(respx_mock, caplog):
    route = respx_mock.post(HOOK).respond(500)
    sleeps = []
    send_webhook(HOOK, "job.failed", {"job_id": 1}, None, sleep=sleeps.append)
    assert route.call_count == 3 and sleeps == [1, 2]
    assert "X-Jerome-Signature" not in route.calls[0].request.headers
    assert "job.failed" in caplog.text


def test_send_webhook_never_raises_on_network_error(respx_mock):
    respx_mock.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
    send_webhook(HOOK, "job.completed", {}, "k", sleep=lambda _s: None)


def test_send_webhook_succeeds_on_retry(respx_mock):
    route = respx_mock.post(HOOK).mock(side_effect=[httpx.Response(503), httpx.Response(204)])
    sleeps = []
    send_webhook(HOOK, "job.completed", {}, "k", sleep=sleeps.append)
    assert route.call_count == 2 and sleeps == [1]


def test_work_forever_runs_claimed_task_then_stops(
    session_factory, test_settings, lt_route, job_two_chunks
):
    deps = make_deps(test_settings, FakeProvider([reply(), reply()]))
    stop = threading.Event()
    orig = deps.now

    def now_then_stop():  # stop once the job is complete
        with session_factory() as s:
            if s.get(Job, job_two_chunks.job_id).status == "completed":
                stop.set()
        return orig()

    deps.now = now_then_stop
    t = threading.Thread(target=work_forever, args=(session_factory, deps, 0.01, stop))
    t.start()
    t.join(timeout=20)
    stop.set()
    assert not t.is_alive()
    assert job(session_factory, job_two_chunks.job_id).status == "completed"


class BilledErrorProvider(FakeProvider):
    """Raises a billed refusal / output error, as the adapters do."""

    def __init__(self, error):
        super().__init__([])
        self.error = error

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


def billed(cls):
    return cls("x", usage=Usage(1000, 150), model="claude-opus-5-5", provider="fake")


def test_parse_failure_after_billed_call_records_usage(session_factory, test_settings, lt_route):
    ids = build(session_factory, [[BIG]])
    deps = make_deps(test_settings, FakeProvider([{"not": "suggestions"}]))
    run_task(session_factory, ids.task_id, deps)
    t = task(session_factory, ids.task_id)
    assert (t.chunks_done, t.attempts, t.status) == (0, 1, "queued")
    assert count(session_factory, ModelUsage) == 1
    assert job(session_factory, ids.job_id).cost_actual == Decimal("0.0070")


def test_adapter_output_error_records_usage(session_factory, test_settings, lt_route):
    ids = build(session_factory, [[BIG]])
    deps = make_deps(test_settings, BilledErrorProvider(billed(ModelOutputError)))
    run_task(session_factory, ids.task_id, deps)
    assert task(session_factory, ids.task_id).attempts == 1
    assert job(session_factory, ids.job_id).cost_actual == Decimal("0.0070")


def test_billed_refusal_records_usage_and_cost(session_factory, test_settings, lt_route):
    ids = build(session_factory, [[BIG]])
    deps = make_deps(test_settings, BilledErrorProvider(billed(ModelRefusal)))
    run_task(session_factory, ids.task_id, deps)
    t = task(session_factory, ids.task_id)
    assert t.status == "done" and t.attempts == 0 and t.chunks_done == 1
    j = job(session_factory, ids.job_id)
    assert j.cost_actual == Decimal("0.0070") and j.stats["refusals"] == 1
    assert count(session_factory, ModelUsage) == 1


class Interleave:
    """Runs `action` once, just before the first statement matching `match` executes."""

    def __init__(self, engine, match, action):
        self.engine, self.match, self.action, self.fired = engine, match, action, False

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hook)

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)

    def _hook(self, conn, cursor, statement, *rest):
        if not self.fired and self.match in statement:
            self.fired = True
            self.action()


def test_claim_does_not_resurrect_cancelled_job(session_factory, engine, job_two_chapters):
    def cancel():
        with session_factory() as s:
            cancel_job(s, job_two_chapters.job_id)
            s.commit()

    hook = Interleave(engine, "UPDATE jobs SET status", cancel)
    with hook, session_factory() as s:
        assert claim_task(s) == job_two_chapters.task_ids[0]
    assert hook.fired
    assert job(session_factory, job_two_chapters.job_id).status == "cancelled"


def test_start_on_cancelled_job_requeues_task(session_factory, test_settings, job_two_chunks):
    with session_factory() as s:
        cancel_job(s, job_two_chunks.job_id)
        s.execute(update(JobTask).values(status="running"))  # claimed just before the cancel
        s.commit()
    deps = make_deps(test_settings, FakeProvider([]))
    run_task(session_factory, job_two_chunks.task_id, deps)
    t = task(session_factory, job_two_chunks.task_id)
    assert t.status == "queued" and t.not_before is None
    assert job(session_factory, job_two_chunks.job_id).status == "cancelled"


def test_start_does_not_reset_task_finished_by_another_worker(
    session_factory, engine, test_settings, job_two_chunks
):
    def other_worker_finishes():
        with session_factory() as s:
            s.execute(update(JobTask).values(status="done", chunks_done=2))
            s.commit()

    provider = FakeProvider([])
    with Interleave(engine, "FROM jobs", other_worker_finishes):
        run_task(session_factory, job_two_chunks.task_id, make_deps(test_settings, provider))
    t = task(session_factory, job_two_chunks.task_id)
    assert t.status == "done" and provider.calls == []


def test_cancel_job_waits_for_a_locked_job(session_factory, job_two_chunks):
    # cancel_job re-reads the row under lock: a status committed meanwhile is respected
    with session_factory() as s:
        s.get(Job, job_two_chunks.job_id)  # cached as queued
        with session_factory() as other:
            other.execute(update(Job).values(status="completed"))
            other.commit()
        with pytest.raises(Conflict):
            cancel_job(s, job_two_chunks.job_id)


def test_a_learned_word_is_not_marked_in_a_new_job(session_factory, test_settings, respx_mock):
    sf = session_factory
    ids = build(sf, [["Katharina ran."]], passes=["languagetool"])
    with sf() as s:
        s.add(LearnedWord(language="en", word="Katharina", rule="*", created_by="a"))
        s.add(LearnedWord(language="de", word="ran", rule="*", created_by="a"))
        s.commit()
    spelling = {"issueType": "misspelling", "id": "SP"}
    respx_mock.post(f"{LT}/v2/check").respond(
        json={
            "matches": [
                {"message": "m", "offset": 0, "length": 9, "replacements": [{"value": "K"}],
                 "rule": spelling},
                {"message": "m", "offset": 10, "length": 3, "replacements": [{"value": "run"}],
                 "rule": spelling},
            ]
        }
    )  # fmt: skip
    run_task(sf, ids.task_id, make_deps(test_settings, FakeProvider([])))
    with sf() as s:
        assert [r.original for r in s.scalars(select(Suggestion))] == ["ran"]
