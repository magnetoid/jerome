import hashlib
import re
import threading
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from jerome.core.translation import (
    import_for_translation,
    keep_source_block,
    retranslate_block,
    translate_items,
)
from jerome.errors import Conflict
from jerome.jobs.estimate import estimate_translation
from jerome.jobs.service import create_job, create_translation_job
from jerome.jobs.worker import WorkerDeps, claim_task, run_task
from jerome.models import FakeProvider, ModelOutputError, ModelRefusal, Registry, Usage, cost
from jerome.orm import Block, Chapter, Job, JobTask, ModelUsage, Segment
from jerome.proofread.languagetool import LanguageToolClient
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.epub_builders import make_epub
from tests.translate.fakes import echo_translator, scripted

PUBLIC = Path(__file__).resolve().parent.parent / "fixtures" / "public"
LONG = " ".join(["word"] * 700)  # two of these do not fit one 1,200-word chunk
CALL_COST = Decimal("0.007")  # one FakeProvider call, Usage(1000, 150), at the default price
_BLOCK_ID = re.compile(r'<block id="(\d+)">')


def registry_for(test_settings, provider, *, ai=False):
    models = {"translate": {"provider": "fake", "model": "claude-opus-5-5"}}
    if ai:
        models["ai"] = {"provider": "fake", "model": "claude-opus-5-5"}
    return Registry(test_settings.model_copy(update={"models": models}), {"fake": provider})


def deps_for(registry):
    return WorkerDeps(registry, LanguageToolClient("http://lt.test", httpx.Client()), None)


def echo(n=200, prefix="DE "):
    return [echo_translator(prefix)] * n


def imported(sf, storage, *, data=None, filename="b.docx", src="en-GB", tgt="de-DE", **kw):
    with sf() as s:
        t = import_for_translation(
            s,
            storage,
            filename=filename,
            data=data if data is not None else make_docx(["First para.", "Second para."]),
            source_variant=src,
            target_variant=tgt,
            **kw,
        )
        s.commit()
        return t.id


def start(sf, registry, book_id, max_cost=Decimal("10")):
    with sf() as s:
        job = create_translation_job(
            s, registry, book_id, max_cost=max_cost, callback_url=None, actor="a"
        )
        s.commit()
        return job.id


def drain(sf, deps, stop=None):
    while True:
        with sf() as s:
            task_id = claim_task(s)
        if task_id is None:
            return
        run_task(sf, task_id, deps, stop)
        if stop is not None and stop.is_set():  # the worker stops, like work_forever
            return


def blocks(sf, book_id):
    with sf() as s:
        rows = list(
            s.scalars(
                select(Block)
                .join(Chapter, Block.chapter_id == Chapter.id)
                .where(Block.book_id == book_id)
                .order_by(Chapter.ordinal, Block.ordinal)
            )
        )
        s.expunge_all()
        return rows


def job_row(sf, job_id):
    with sf() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def usage_rows(sf, job_id):
    with sf() as s:
        return list(s.scalars(select(ModelUsage).where(ModelUsage.job_id == job_id)))


def called_ids(provider):
    return [[int(i) for i in _BLOCK_ID.findall(c["user"])] for c in provider.calls]


def test_translate_job_fills_every_block_and_completes(session_factory, tmp_storage, test_settings):
    data = (PUBLIC / "pg932.epub").read_bytes()
    t = imported(session_factory, tmp_storage, data=data, filename="pg932.epub")
    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))

    job = job_row(session_factory, job_id)
    assert job.status == "completed" and job.type == "translate" and job.passes == ["translate"]
    rows = blocks(session_factory, t)
    assert all(b.translation_status == "done" and b.text.startswith("DE ") for b in rows)
    assert all(b.hash == hashlib.sha256(b.text.encode()).hexdigest() for b in rows)
    assert job.stats["translated"] == len(rows) and job.stats.get("flagged", 0) == 0
    assert job.cost_actual > 0
    assert {u.pass_name for u in usage_rows(session_factory, job_id)} == {"translate"}
    assert job.cost_actual == CALL_COST * len(provider.calls)
    with session_factory() as s:
        segments = select(func.count()).select_from(Segment).join(Block)
        assert s.scalar(segments.where(Block.book_id == t)) > 0
        src_id = s.get(Block, rows[0].source_block_id).book_id
        assert src_id != t
        assert (
            s.scalar(
                select(func.count())
                .select_from(Block)
                .where(Block.book_id == src_id, Block.translation_status.is_not(None))
            )
            == 0
        )  # the source book is never modified
    # every block was sent exactly once
    ids = [i for c in called_ids(provider) for i in c]
    assert sorted(ids) == sorted(b.id for b in rows)
    assert job.estimate["chapter_chunks"] and sum(job.estimate["chapter_chunks"].values()) == len(
        provider.calls
    )


def test_marks_and_placeholders_come_back_and_dropped_marks_are_counted(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["Hello **bold** world.[^1]", "Plain *it* here."], footnotes={1: "A note."})
    t = imported(session_factory, tmp_storage, data=data)
    first, second, note = [b.id for b in blocks(session_factory, t)]
    # the second reply leaves out its <i> tag: the text is kept, the mark counted as dropped
    replies = {first: "Hallo <b>fett</b> Welt.⟦1⟧", second: "Klar hier.", note: "⟦1⟧ Eine Notiz."}
    provider = FakeProvider([scripted(replies)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    b1, b2 = blocks(session_factory, t)[:2]
    assert b1.text == "Hallo fett Welt.￼" and b1.translation_status == "done"
    assert b1.marks == [{"start": 6, "end": 10, "kind": "bold", "href": None}]
    assert b1.placeholders is None  # identity
    assert b2.text == "Klar hier." and b2.marks == []
    assert job_row(session_factory, job_id).stats["marks_dropped"] == 1


def test_reply_missing_and_duplicate_blocks_are_retried_then_flagged(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["First para.", "Second para.", "Third para."])
    t = imported(session_factory, tmp_storage, data=data)
    b1, b2, b3 = [b.id for b in blocks(session_factory, t)]

    def first_reply(_kwargs):  # repeats block 1 and leaves out block 2
        return {
            "blocks": [
                {"block_id": b1, "text": "Erster."},
                {"block_id": b1, "text": "Erster."},
                {"block_id": b3, "text": "Dritter."},
            ]
        }

    provider = FakeProvider(
        [first_reply, scripted({b1: "Erster Absatz."}), scripted({b2: "kaputt <i>"})]
    )
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))

    assert called_ids(provider) == [[b1, b2, b3], [b1], [b2]]
    one, two, three = blocks(session_factory, t)
    assert (one.text, one.translation_status) == ("Erster Absatz.", "done")
    assert (two.text, two.translation_status) == ("Second para.", "flagged")
    assert two.placeholders is None and two.hash == hashlib.sha256(b"Second para.").hexdigest()
    assert (three.text, three.translation_status) == ("Dritter.", "done")
    job = job_row(session_factory, job_id)
    assert job.status == "completed"
    assert job.stats["flagged"] == 1 and job.stats["translated"] == 2
    assert len(usage_rows(session_factory, job_id)) == 3  # the chunk and both retries
    assert job.cost_actual == 3 * CALL_COST
    # the retry of block 2 carries the block before it, translated in this run, as context
    assert "<source>First para.</source>" in provider.calls[2]["user"]
    assert "<translation>Erster Absatz.</translation>" in provider.calls[2]["user"]


def test_refused_chunk_retries_each_block_alone(session_factory, tmp_storage, test_settings):
    t = imported(session_factory, tmp_storage)
    refusal = ModelRefusal("no", usage=Usage(1000, 150), model="claude-opus-5-5", provider="fake")
    provider = FakeProvider([refusal, *echo(2)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    rows = blocks(session_factory, t)
    assert called_ids(provider) == [[rows[0].id, rows[1].id], [rows[0].id], [rows[1].id]]
    assert all(b.translation_status == "done" for b in rows)
    job = job_row(session_factory, job_id)
    assert job.status == "completed" and job.cost_actual == 3 * CALL_COST
    assert job.stats["refusals"] == 1


def test_unusable_chunk_reply_is_billed_and_the_task_retried(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage)
    bad = ModelOutputError("bad", usage=Usage(1000, 150), model="claude-opus-5-5", provider="fake")
    provider = FakeProvider([bad, *echo(1)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    with session_factory() as s:
        task_id = claim_task(s)
    run_task(session_factory, task_id, deps_for(reg))
    with session_factory() as s:
        task = s.get(JobTask, task_id)
        assert (task.status, task.attempts, task.chunks_done) == ("queued", 1, 0)
    rows = usage_rows(session_factory, job_id)
    assert [u.pass_name for u in rows] == ["translate"]
    assert {b.translation_status for b in blocks(session_factory, t)} == {"pending"}
    run_task(session_factory, task_id, deps_for(reg))  # the retry, without waiting for backoff
    assert job_row(session_factory, job_id).status == "completed"


def test_resume_after_stop_does_not_retranslate_done_blocks(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=make_docx([LONG, LONG, LONG]))
    stop = threading.Event()
    echo_reply = echo_translator()

    def stop_mid_call(kwargs):
        stop.set()  # the worker is asked to stop while the first chunk is with the model
        return echo_reply(kwargs)

    provider = FakeProvider([stop_mid_call, *echo(5)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg), stop)
    assert len(provider.calls) == 1
    assert job_row(session_factory, job_id).status == "queued"
    assert [b.translation_status for b in blocks(session_factory, t)] == [
        "done",
        "pending",
        "pending",
    ]

    drain(session_factory, deps_for(reg))  # resume
    ids = [i for c in called_ids(provider) for i in c]
    assert len(ids) == len(set(ids)) == 3  # no call carried a block already done
    assert len(provider.calls) == 3  # chunks + retries (none)
    assert len(usage_rows(session_factory, job_id)) == 3
    assert job_row(session_factory, job_id).status == "completed"


def test_chunk_with_no_pending_block_advances_without_a_call(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=make_docx([LONG, LONG, LONG]))
    first, middle, last = [b.id for b in blocks(session_factory, t)]
    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    with session_factory() as s:  # e.g. done by an earlier run of the same chunk
        b = s.get(Block, middle)
        b.text, b.translation_status = "Schon fertig.", "done"
        s.commit()
    drain(session_factory, deps_for(reg))
    assert called_ids(provider) == [[first], [last]]
    assert blocks(session_factory, t)[1].text == "Schon fertig."
    with session_factory() as s:
        task = s.scalars(select(JobTask).where(JobTask.job_id == job_id)).one()
        assert (task.status, task.chunks_done) == ("done", 3)


def test_cost_cap_pauses_translate_job(session_factory, tmp_storage, test_settings):
    t = imported(session_factory, tmp_storage, data=make_docx([LONG, LONG, LONG]))
    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t, max_cost=Decimal("0.001"))
    drain(session_factory, deps_for(reg))
    job = job_row(session_factory, job_id)
    assert job.status == "paused_cost_cap" and job.cost_actual == CALL_COST
    assert len(provider.calls) == 1
    with session_factory() as s:
        task = s.scalars(select(JobTask).where(JobTask.job_id == job_id)).one()
        assert (task.status, task.chunks_done) == ("queued", 1)


def test_estimate_translation_scales_output_and_needs_a_model(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=make_docx([LONG, LONG, LONG]))
    reg = registry_for(test_settings, FakeProvider([], token_count=1000))
    with session_factory() as s:
        est = estimate_translation(s, reg, t)
    price = reg.price("claude-opus-5-5")
    assert est.chunks == 3 and list(est.per_pass) == ["translate"]
    assert est.per_pass["translate"].high == 3 * cost(price, Usage(1000, 1400))
    assert est.per_pass["translate"].low < est.per_pass["translate"].high
    assert est.total.high == est.per_pass["translate"].high
    assert sum(est.chapter_chunks.values()) == 3

    local = imported(session_factory, tmp_storage, local_only=True)
    remote = registry_for(test_settings, FakeProvider([], local=False, token_count=1000))
    with session_factory() as s:
        with pytest.raises(Conflict, match="no translation model is available for this book"):
            estimate_translation(s, remote, local)
        with pytest.raises(Conflict, match="no translation model"):
            create_translation_job(
                s, remote, local, max_cost=Decimal(1), callback_url=None, actor="a"
            )


def test_create_translation_job_guards(session_factory, tmp_storage, test_settings):
    from jerome.core.books import import_book

    t = imported(session_factory, tmp_storage)
    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    with session_factory() as s:
        plain = import_book(
            s, tmp_storage, filename="p.docx", data=make_docx(["Hi."]), variant="en-GB"
        ).id
        src = s.get(Block, blocks(session_factory, t)[0].source_block_id).book_id
        s.commit()
    with session_factory() as s:
        with pytest.raises(Conflict, match="work on book"):
            create_translation_job(s, reg, src, max_cost=Decimal(1), callback_url=None, actor="a")
        with pytest.raises(Conflict):
            create_translation_job(s, reg, plain, max_cost=Decimal(1), callback_url=None, actor="a")
    job_id = start(session_factory, reg, t)
    with session_factory() as s:
        tasks = list(s.scalars(select(JobTask).where(JobTask.job_id == job_id)))
        assert [k.kind for k in tasks] == ["chapter"]
        with pytest.raises(Conflict):  # the one-active-job slot
            create_translation_job(s, reg, t, max_cost=Decimal(1), callback_url=None, actor="a")
    drain(session_factory, deps_for(reg))
    with session_factory() as s:
        with pytest.raises(Conflict):  # nothing is pending any more
            create_translation_job(s, reg, t, max_cost=Decimal(1), callback_url=None, actor="a")


def test_proofread_job_refused_while_blocks_pending(session_factory, tmp_storage, test_settings):
    t = imported(session_factory, tmp_storage)
    reg = registry_for(test_settings, FakeProvider(echo()), ai=True)
    with session_factory() as s:
        with pytest.raises(Conflict, match="translate the book before proofreading it"):
            create_job(s, reg, t, passes=["ai"], max_cost=Decimal(1), callback_url=None, actor="a")


def test_proofreading_skips_flagged_and_kept_blocks(session_factory, tmp_storage, test_settings):
    data = make_docx(["First para.", "Second para.", "Third para."])
    t = imported(session_factory, tmp_storage, data=data)
    b1, b2, b3 = [b.id for b in blocks(session_factory, t)]
    provider = FakeProvider(
        [
            scripted({b1: "Erster.", b2: None, b3: None}),
            scripted({b2: "kaputt <i>"}),
            scripted({b3: "kaputt </b>"}),
        ]
    )
    reg = registry_for(test_settings, provider, ai=True)
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    with session_factory() as s:
        keep_source_block(s, b2, actor="a")
        s.commit()
    assert [b.translation_status for b in blocks(session_factory, t)] == [
        "done",
        "kept",
        "flagged",
    ]

    provider.responses = [{"suggestions": []}] * 5
    with session_factory() as s:
        job = create_job(
            s, reg, t, passes=["ai"], max_cost=Decimal(1), callback_url=None, actor="a"
        )
        assert job.estimate["chunks"] == 1
        s.commit()
    before = len(provider.calls)
    drain(session_factory, deps_for(reg))
    proofread_calls = called_ids(provider)[before:]
    assert proofread_calls == [[b1]]


def test_chapter_title_follows_translated_heading(session_factory, tmp_storage, test_settings):
    data = make_epub([("Chapter One", ["A line."]), ("Chapter Two", ["Another."])])
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    heading_two = blocks(session_factory, t)[2].id

    def reply(kwargs):  # the second chapter's heading comes back broken, twice
        out = echo_translator()(kwargs)
        for b in out["blocks"]:
            if b["block_id"] == heading_two:
                b["text"] = "kaputt <i>"
        return out

    provider = FakeProvider([reply] * 10)
    reg = registry_for(test_settings, provider)
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    with session_factory() as s:
        titles = list(
            s.scalars(select(Chapter.title).where(Chapter.book_id == t).order_by(Chapter.ordinal))
        )
    assert titles == ["DE Chapter One", "Chapter Two"]  # a flagged heading keeps the title


def _flagged_book(sf, storage, test_settings):
    data = make_docx(["First para.", "Second para.", "Third para."])
    t = imported(sf, storage, data=data)
    b1, b2, b3 = [b.id for b in blocks(sf, t)]
    provider = FakeProvider(
        [
            scripted({b1: "Erster.", b2: None, b3: None}),
            scripted({b2: "kaputt <i>"}),
            scripted({b3: "kaputt <i>"}),
        ]
    )
    reg = registry_for(test_settings, provider, ai=True)
    job_id = start(sf, reg, t)
    drain(sf, deps_for(reg))
    return t, (b1, b2, b3), provider, reg, job_id


def test_retranslate_flagged_block_bills_latest_translate_job(
    session_factory, tmp_storage, test_settings
):
    t, (b1, b2, b3), provider, reg, job_id = _flagged_book(
        session_factory, tmp_storage, test_settings
    )
    cost_before = job_row(session_factory, job_id).cost_actual
    provider.responses = [scripted({b2: "Zweiter."}), scripted({b3: "<x>kaputt"})]
    with session_factory() as s:
        block = retranslate_block(s, reg, b2, actor="a")
        assert (block.text, block.translation_status) == ("Zweiter.", "done")
        s.commit()
    # the same context a chunk gets: the block before it, with its translation
    assert "<source>First para.</source>" in provider.calls[-1]["user"]
    assert "<translation>Erster.</translation>" in provider.calls[-1]["user"]
    with session_factory() as s:
        block = retranslate_block(s, reg, b3, actor="a")  # broken tags: stays flagged
        assert (block.text, block.translation_status) == ("Third para.", "flagged")
        s.commit()
        segs = s.scalars(select(Segment).where(Segment.block_id == b2)).all()
        assert [(g.start, g.end) for g in segs] == [(0, len("Zweiter."))]
    job = job_row(session_factory, job_id)
    assert job.cost_actual == cost_before + 2 * CALL_COST
    rows = usage_rows(session_factory, job_id)
    assert len(rows) == 5 and {u.pass_name for u in rows} == {"translate"}

    provider.responses = [{"suggestions": []}] * 5
    with session_factory() as s:
        create_job(s, reg, t, passes=["ai"], max_cost=Decimal(1), callback_url=None, actor="a")
        s.commit()
    with session_factory() as s:
        with pytest.raises(Conflict):  # a job is active on the book
            retranslate_block(s, reg, b3, actor="a")


def test_retranslate_refused_after_the_call_still_records_the_spend(
    session_factory, tmp_storage, test_settings
):
    t, (_, b2, _), provider, reg, job_id = _flagged_book(
        session_factory, tmp_storage, test_settings
    )
    cost_before = job_row(session_factory, job_id).cost_actual
    rows_before = len(usage_rows(session_factory, job_id))
    reply = scripted({b2: "Zweiter."})

    def job_started_during_call(kwargs):
        with session_factory() as other:  # another client starts a job on the book meanwhile
            create_job(
                other, reg, t, passes=["ai"], max_cost=Decimal(1), callback_url=None, actor="b"
            )
            other.commit()
        return reply(kwargs)

    provider.responses = [job_started_during_call]
    with session_factory() as s:
        with pytest.raises(Conflict, match="active job"):
            retranslate_block(s, reg, b2, actor="a")
        s.rollback()  # as the API does on an error
    assert job_row(session_factory, job_id).cost_actual == cost_before + CALL_COST
    assert len(usage_rows(session_factory, job_id)) == rows_before + 1
    block = blocks(session_factory, t)[1]
    assert (block.text, block.translation_status) == ("Second para.", "flagged")


def test_retranslate_refuses_a_done_block_with_suggestions(
    session_factory, tmp_storage, test_settings
):
    from tests.fixtures.suggestions import add_one

    t, (b1, _, _), provider, reg, job_id = _flagged_book(
        session_factory, tmp_storage, test_settings
    )
    with session_factory() as s:
        add_one(s, s.get(Block, b1), 0, 6, "Erster", "Erste")
        s.commit()
    calls = len(provider.calls)
    with session_factory() as s:
        with pytest.raises(Conflict, match="suggestions"):
            retranslate_block(s, reg, b1, actor="a")
    assert len(provider.calls) == calls  # refused before any model call
    assert blocks(session_factory, t)[0].text == "Erster."


def test_estimate_counts_every_chunk_but_prices_only_pending_ones(
    session_factory, tmp_storage, test_settings
):
    data = make_epub([("Chapter One", ["A line."]), ("Chapter Two", [LONG, LONG, LONG])])
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    rows = blocks(session_factory, t)
    with session_factory() as s:
        # chapter one is done; in chapter two, the second chunk's only block is done
        for b in (rows[0], rows[1], rows[4]):
            block = s.get(Block, b.id)
            block.text, block.translation_status = "Fertig.", "done"
        s.commit()
        chapter_two = rows[2].chapter_id
    reg = registry_for(test_settings, FakeProvider([], token_count=1000))
    with session_factory() as s:
        est = estimate_translation(s, reg, t)
    assert est.chapter_chunks == {chapter_two: 3}  # every chunk, for progress
    assert est.chunks == sum(est.chapter_chunks.values()) == 3
    price = reg.price("claude-opus-5-5")
    assert est.per_pass["translate"].high == 2 * cost(price, Usage(1000, 1400))  # pending only
    assert est.total.high == est.per_pass["translate"].high

    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert len(provider.calls) == 2  # the done chunk is skipped
    with session_factory() as s:
        task = s.scalars(select(JobTask).where(JobTask.job_id == job_id)).one()
        total = job_row(session_factory, job_id).estimate["chapter_chunks"][str(task.chapter_id)]
        assert task.chunks_done == total == 3  # progress never runs past its total


def test_keep_source_only_for_docx(session_factory, tmp_storage, test_settings):
    t, (b1, b2, _), _, _, _ = _flagged_book(session_factory, tmp_storage, test_settings)
    with session_factory() as s:
        with pytest.raises(Conflict):  # only a flagged block can be kept
            keep_source_block(s, b1, actor="a")
        block = keep_source_block(s, b2, actor="a")
        assert block.translation_status == "kept" and block.text == "Second para."
        s.commit()

    data = make_epub([("Chapter One", ["A line."])])
    e = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    line = blocks(session_factory, e)[1].id

    def broken_line(kwargs):
        out = echo_translator()(kwargs)
        for b in out["blocks"]:
            if b["block_id"] == line:
                b["text"] = "kaputt <i>"
        return out

    provider = FakeProvider([broken_line, scripted({line: "kaputt <i>"})])
    reg = registry_for(test_settings, provider)
    start(session_factory, reg, e)
    drain(session_factory, deps_for(reg))
    assert blocks(session_factory, e)[1].translation_status == "flagged"
    with session_factory() as s:
        with pytest.raises(
            Conflict, match="only DOCX books can keep a paragraph untranslated; translate it again"
        ):
            keep_source_block(s, line, actor="a")


def test_translate_items_cover_every_block_from_the_source(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["Hello **bold** world.", "Second."])
    t = imported(session_factory, tmp_storage, data=data)
    with session_factory() as s:
        chapter_id = s.scalar(select(Chapter.id).where(Chapter.book_id == t))
        items = translate_items(s, t, chapter_id)
    assert [i.text for i in items] == ["Hello bold world.", "Second."]
    assert [i.block_id for i in items] == [b.id for b in blocks(session_factory, t)]
    assert items[0].marks[0].kind.value == "bold"


def test_de_to_en_on_public_german_epub(session_factory, tmp_storage, test_settings):
    data = (PUBLIC / "pg22367.epub").read_bytes()
    t = imported(
        session_factory, tmp_storage, data=data, filename="pg22367.epub", src="de-DE", tgt="en-GB"
    )
    provider = FakeProvider(echo(prefix="EN "))
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert job_row(session_factory, job_id).status == "completed"
    rows = blocks(session_factory, t)
    assert all(b.translation_status == "done" and b.text.startswith("EN ") for b in rows)
    assert {u.pass_name for u in usage_rows(session_factory, job_id)} == {"translate"}
