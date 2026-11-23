from decimal import Decimal

from jerome.core.translation import source_texts
from jerome.jobs.estimate import estimate_job
from jerome.jobs.service import create_job
from jerome.jobs.worker import claim_task, run_task
from jerome.models import FakeProvider
from jerome.orm import Book
from jerome.proofread.ai_pass import Chunk, build_request, run_ai_pass
from jerome.types import BlockText, Source, StyleSheet
from tests.fixtures.docx_builders import make_docx
from tests.jobs.test_translate_job import (
    blocks,
    deps_for,
    drain,
    echo,
    imported,
    registry_for,
    start,
)
from tests.jobs.test_worker import build, make_deps

STYLE_EN = StyleSheet("en-US", "“”‘’", {}, [])
STYLE_DE = StyleSheet("de-DE", "„“‚‘", {}, [])


def test_request_includes_source_blocks_for_translated_book():
    chunk = Chunk([BlockText(2, "Hallo"), BlockText(3, "Welt")], None, {2: "Hello", 3: "World"})
    plain, _ = build_request(STYLE_DE, Chunk(chunk.blocks, None))
    system, user = build_request(STYLE_DE, chunk)
    assert len(system) == len(plain) + 1 and system[:2] == plain and system[2].cache
    assert "translation" in system[2].text and "Quelle" in system[2].text
    assert '<source_block for="2" read_only="true">\nHello\n</source_block>' in user
    assert user.index('<source_block for="2"') < user.index('<block id="2">')
    assert user.index('<source_block for="3"') < user.index('<block id="3">')
    en, _ = build_request(STYLE_EN, Chunk([BlockText(2, "Hello")], None, {2: "Hallo"}))
    assert "source" in en[2].text and "translation" in en[2].text
    # a block without a source gets no source element
    _, partial = build_request(STYLE_DE, Chunk(chunk.blocks, None, {2: "Hello"}))
    assert 'for="3"' not in partial


def test_request_unchanged_without_sources():
    system, user = build_request(STYLE_EN, Chunk([BlockText(2, "Body")], BlockText(1, "Ctx")))
    assert user == (
        '<context_block id="1" read_only="true">\nCtx\n</context_block>\n\n'
        '<block id="2">\nBody\n</block>\n\n'
        "Reminder: U+FFFC marks an inline object. It must never appear in `original` or "
        "`replacement`. Only propose corrections for the <block> elements above."
    )
    assert [p.cache for p in system] == [True, True]
    assert system[1].text == (
        'Style sheet:\n{\n  "house_spellings": {},\n  "ignore_words": [],\n'
        '  "quote_style": "“”‘’",\n  "variant": "en-US"\n}'
    )
    assert "source_block" not in user and "source_block" not in system[0].text
    assert build_request(STYLE_EN, Chunk([BlockText(2, "Body")], BlockText(1, "Ctx"), None)) == (
        system,
        user,
    )


def _items(*cats):
    return {
        "suggestions": [
            {
                "block_id": 1,
                "original": "Hallo",
                "replacement": "Guten Tag",
                "category": c,
                "severity": "medium",
                "explanation": "x",
                "occurrence": 1,
            }
            for c in cats
        ]
    }


def test_translation_category_candidate_is_kept():
    blocks_ = [BlockText(1, "Hallo Welt")]
    fake = FakeProvider([_items("translation")])
    r = run_ai_pass(Chunk(blocks_, None, {1: "Hello world"}), STYLE_DE, fake, "m", None)
    assert [c.category.value for c in r.candidates] == ["translation"]
    assert r.candidates[0].source == Source.MODEL and r.dropped == 0


def test_translation_candidate_dropped_without_sources():  # ruling R1
    fake = FakeProvider([_items("translation", "typo")])
    r = run_ai_pass(Chunk([BlockText(1, "Hallo Welt")], None), STYLE_DE, fake, "m", None)
    assert [c.category.value for c in r.candidates] == ["typo"]
    assert r.dropped == 1


def _translated(sf, storage, test_settings):
    t = imported(sf, storage, data=make_docx(["First para.", "Second para."]))
    reg = registry_for(test_settings, FakeProvider(echo()), ai=True)
    start(sf, reg, t)
    drain(sf, deps_for(reg))
    return t, reg


def test_source_texts_maps_translated_blocks_to_source(session_factory, tmp_storage, test_settings):
    t, _ = _translated(session_factory, tmp_storage, test_settings)
    ids = [b.id for b in blocks(session_factory, t)]
    with session_factory() as s:
        assert source_texts(s, ids + [10**9]) == {ids[0]: "First para.", ids[1]: "Second para."}
        assert source_texts(s, []) == {}


def test_worker_fills_sources_only_for_translated_books(
    session_factory, tmp_storage, test_settings
):
    t, reg = _translated(session_factory, tmp_storage, test_settings)
    ids = [b.id for b in blocks(session_factory, t)]
    provider = FakeProvider([{"suggestions": []}] * 5)
    reg = registry_for(test_settings, provider, ai=True)
    with session_factory() as s:
        job = create_job(
            s, reg, t, passes=["ai"], max_cost=Decimal(1), callback_url=None, actor="a"
        )
        est_with = job.estimate
        s.commit()
    drain(session_factory, deps_for(reg))
    system, user = provider.calls[-1]["system"], provider.calls[-1]["user"]
    assert len(system) == 3
    for bid, text in zip(ids, ["First para.", "Second para."], strict=True):
        assert f'<source_block for="{bid}" read_only="true">\n{text}\n</source_block>' in user

    # a book that is not a translation sends exactly the old request
    plain = build(session_factory, [["Plain one.", "Plain two."]], passes=["ai"])
    fake = FakeProvider([{"suggestions": []}])
    with session_factory() as s:
        task_id = claim_task(s)
    run_task(session_factory, task_id, make_deps(test_settings, fake))
    assert plain.job_id and len(fake.calls[0]["system"]) == 2
    assert "source_block" not in fake.calls[0]["user"]
    assert est_with["chunks"] == 1


def test_estimate_prices_the_source_for_translated_books(
    session_factory, tmp_storage, test_settings
):
    t, _ = _translated(session_factory, tmp_storage, test_settings)
    reg = registry_for(test_settings, FakeProvider([], token_count=None), ai=True)
    with session_factory() as s:
        with_sources = estimate_job(s, reg, t, passes=["ai"]).total.high
        s.get(Book, t).work = "proofread"
        without = estimate_job(s, reg, t, passes=["ai"]).total.high
        s.rollback()
    assert with_sources > without
