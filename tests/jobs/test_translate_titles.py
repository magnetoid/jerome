"""Chapter titles of a translated EPUB that are not their first heading's text (a TOC label
such as "The Storm" over a heading "Chapter One", or front matter with no heading): each is
translated as one extra plain item in its chapter's first translate chunk (ruling R28)."""

import io
import threading
import zipfile

from lxml import etree
from sqlalchemy import select

from jerome.api.routes_translation import get_translation
from jerome.core.exports import export_book
from jerome.jobs.estimate import estimate_translation
from jerome.models import FakeProvider
from jerome.orm import Chapter, Job
from tests.fixtures.epub_builders import edit_entry, make_epub
from tests.jobs.test_translate_job import (
    LONG,
    blocks,
    deps_for,
    drain,
    echo,
    imported,
    job_row,
    registry_for,
    start,
)
from tests.translate.fakes import echo_translator

_NS = {
    "x": "http://www.w3.org/1999/xhtml",
    "n": "http://www.daisy.org/z3986/2005/ncx/",
    "epub": "http://www.idpf.org/2007/ops",
}


def relabelled(data: bytes, entry: str, old: str, new: str) -> bytes:
    return edit_entry(data, entry, lambda xml: xml.replace(old, new, 1))


def storm_epub(toc: str = "nav", lines: list[str] | None = None) -> bytes:
    """Chapter one's TOC label is "The Storm", its heading "Chapter One"; chapter two's label
    is its heading."""
    chapters = [("Chapter One", lines or ["A line."]), ("Chapter Two", ["Another."])]
    data = make_epub(chapters, toc=toc)
    if toc == "nav":
        return relabelled(data, "OEBPS/nav.xhtml", ">Chapter One</a>", ">The Storm</a>")
    return relabelled(data, "OEBPS/toc.ncx", "<text>Chapter One</text>", "<text>The Storm</text>")


def nav_links(data: bytes) -> list[etree._Element]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        nav = etree.fromstring(zf.read("OEBPS/nav.xhtml"))
    return nav.xpath('//x:nav[@epub:type="toc"]//x:a', namespaces=_NS)


def nav_labels(data: bytes) -> list[str]:
    return ["".join(a.itertext()) for a in nav_links(data)]


def ncx_labels(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        ncx = etree.fromstring(zf.read("OEBPS/toc.ncx"))
    return [t.text for t in ncx.xpath("//n:navMap//n:navLabel/n:text", namespaces=_NS)]


def chapter_titles(sf, book_id) -> list[str]:
    with sf() as s:
        stmt = select(Chapter.title).where(Chapter.book_id == book_id).order_by(Chapter.ordinal)
        return list(s.scalars(stmt))


def chapter_ids(sf, book_id) -> list[int]:
    with sf() as s:
        stmt = select(Chapter.id).where(Chapter.book_id == book_id).order_by(Chapter.ordinal)
        return list(s.scalars(stmt))


def export(sf, storage, book_id) -> bytes:
    with sf() as s:
        f = export_book(s, storage, book_id, "epub-clean")
        s.commit()
        return f.data


def title_calls(provider, chapter_id: int) -> int:
    return sum(f'<block id="{-chapter_id}">' in c["user"] for c in provider.calls)


def test_toc_label_that_is_not_the_heading_is_translated_in_nav(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=storm_epub(), filename="b.epub")
    assert chapter_titles(session_factory, t) == ["The Storm", "Chapter Two"]
    one, _ = chapter_ids(session_factory, t)
    provider = FakeProvider(echo())
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))

    assert chapter_titles(session_factory, t) == ["DE The Storm", "DE Chapter Two"]
    # the title rode in the chapter's one chunk: no call of its own
    assert len(provider.calls) == 2 and title_calls(provider, one) == 1
    job = job_row(session_factory, job_id)
    assert job.status == "completed" and "untranslated_titles" not in job.stats
    assert job.stats["translated"] == len(blocks(session_factory, t))  # blocks only
    assert nav_labels(export(session_factory, tmp_storage, t)) == [
        "DE The Storm",
        "DE Chapter Two",
    ]


def test_toc_label_that_is_not_the_heading_is_translated_in_ncx(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=storm_epub("ncx"), filename="b.epub")
    reg = registry_for(test_settings, FakeProvider(echo()))
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert ncx_labels(export(session_factory, tmp_storage, t)) == [
        "DE The Storm",
        "DE Chapter Two",
    ]


def test_heading_less_front_matter_gets_a_translated_label(
    session_factory, tmp_storage, test_settings
):
    data = make_epub([("Copyright", ["All rights reserved."]), ("Chapter Two", ["Another."])])
    data = relabelled(data, "OEBPS/ch1.xhtml", '<h1 id="c1">Copyright</h1>', "")
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    assert chapter_titles(session_factory, t) == ["Copyright", "Chapter Two"]
    reg = registry_for(test_settings, FakeProvider(echo()))
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert chapter_titles(session_factory, t) == ["DE Copyright", "DE Chapter Two"]
    assert nav_labels(export(session_factory, tmp_storage, t)) == [
        "DE Copyright",
        "DE Chapter Two",
    ]


def test_matching_titles_and_docx_get_no_title_item(session_factory, tmp_storage, test_settings):
    from tests.fixtures.docx_builders import make_docx

    for data, filename, title in (
        (make_epub([("Chapter One", ["A line."])]), "b.epub", "DE Chapter One"),
        (make_docx(["# One", "A line."]), "b.docx", "DE One"),
    ):
        t = imported(session_factory, tmp_storage, data=data, filename=filename)
        provider = FakeProvider(echo())
        reg = registry_for(test_settings, provider)
        start(session_factory, reg, t)
        drain(session_factory, deps_for(reg))
        assert len(provider.calls) == 1
        assert all('<block id="-' not in c["user"] for c in provider.calls)
        assert chapter_titles(session_factory, t) == [title]  # from the heading


def translation_state(sf, book_id) -> dict:
    """GET /v1/books/{id}/translation."""
    with sf() as s:
        return get_translation(book_id, s)


def test_failed_title_item_is_recorded_and_the_label_keeps_its_markup(
    session_factory, tmp_storage, test_settings
):
    data = storm_epub()
    # the unchanged label has inner markup, which the export must keep
    data = relabelled(data, "OEBPS/nav.xhtml", ">The Storm</a>", "><b>The</b> Storm</a>")
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    one, _ = chapter_ids(session_factory, t)
    assert chapter_titles(session_factory, t)[0] == "The Storm"

    def broken_title(kwargs):  # the title comes back unusable, every time
        out = echo_translator()(kwargs)
        for b in out["blocks"]:
            if b["block_id"] == -one:
                b["text"] = "kaputt <i>"
        return out

    provider = FakeProvider([broken_title] * 5)
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))

    assert title_calls(provider, one) == 2  # the chunk, then the per-item retry
    assert chapter_titles(session_factory, t) == ["The Storm", "DE Chapter Two"]
    job = job_row(session_factory, job_id)
    assert job.status == "completed" and job.stats["untranslated_titles"] == [one]
    assert job.stats.get("flagged", 0) == 0  # a title is not a paragraph
    assert translation_state(session_factory, t)["untranslated_titles"] == [one]

    out = export(session_factory, tmp_storage, t)
    first, second = nav_links(out)
    assert etree.tostring(first, encoding="unicode").count("<b>The</b>") == 1
    assert "".join(first.itertext()) == "The Storm"
    assert "".join(second.itertext()) == "DE Chapter Two"


def test_untranslated_titles_absent_from_state_when_none(
    session_factory, tmp_storage, test_settings
):
    t = imported(session_factory, tmp_storage, data=storm_epub(), filename="b.epub")
    reg = registry_for(test_settings, FakeProvider(echo()))
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert "untranslated_titles" not in translation_state(session_factory, t)


def test_resume_does_not_rebuy_the_title_item(session_factory, tmp_storage, test_settings):
    data = storm_epub(lines=[LONG, LONG, LONG])
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    one, _ = chapter_ids(session_factory, t)
    stop = threading.Event()
    echo_reply = echo_translator()

    def stop_mid_call(kwargs):
        stop.set()  # the worker is asked to stop while the first chunk is with the model
        return echo_reply(kwargs)

    provider = FakeProvider([stop_mid_call, *echo(10)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg), stop)
    assert len(provider.calls) == 1 and title_calls(provider, one) == 1
    assert chapter_titles(session_factory, t)[0] == "DE The Storm"

    drain(session_factory, deps_for(reg))  # resume
    assert job_row(session_factory, job_id).status == "completed"
    assert title_calls(provider, one) == 1  # bought once
    assert chapter_titles(session_factory, t)[0] == "DE The Storm"


def test_a_new_job_skips_a_title_already_translated(session_factory, tmp_storage, test_settings):
    data = storm_epub(lines=[LONG, LONG])
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    one, _ = chapter_ids(session_factory, t)
    stop = threading.Event()
    echo_reply = echo_translator()

    def stop_mid_call(kwargs):
        stop.set()
        return echo_reply(kwargs)

    provider = FakeProvider([stop_mid_call, *echo(10)])
    reg = registry_for(test_settings, provider)
    job_id = start(session_factory, reg, t)
    drain(session_factory, deps_for(reg), stop)
    with session_factory() as s:  # cancel it and start over with a new job
        s.get(Job, job_id).status = "cancelled"
        s.commit()
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    assert title_calls(provider, one) == 1
    assert chapter_titles(session_factory, t) == ["DE The Storm", "DE Chapter Two"]


class _CountingProvider(FakeProvider):
    def __init__(self):
        super().__init__([], token_count=1000)
        self.counted: list[str] = []

    def count_tokens(self, *, model, system, user):
        self.counted.append(user)
        return super().count_tokens(model=model, system=system, user=user)


def test_estimate_counts_the_title_item(session_factory, tmp_storage, test_settings):
    t = imported(session_factory, tmp_storage, data=storm_epub(), filename="b.epub")
    one, _ = chapter_ids(session_factory, t)
    provider = _CountingProvider()
    reg = registry_for(test_settings, provider)
    with session_factory() as s:
        est = estimate_translation(s, reg, t)
    assert est.chapter_chunks == dict.fromkeys(chapter_ids(session_factory, t), 1)
    assert sum(f'<block id="{-one}">The Storm</block>' in u for u in provider.counted) == 1
