"""Export of a translated book: the translation, with proofreading applied, written into the
structure of the original file."""

from pathlib import Path

import pytest
from sqlalchemy import select

from jerome.core.exports import export_book
from jerome.core.review_imports import import_review
from jerome.core.suggestions import decide
from jerome.core.translation import keep_source_block, original_for, translated_base
from jerome.documents.epub_reader import read_epub
from jerome.documents.model import Mark, MarkKind
from jerome.errors import Conflict
from jerome.models import FakeProvider
from jerome.orm import Block, Book, Chapter
from tests.fixtures.docx_builders import all_blocks, make_docx
from tests.fixtures.epub_builders import make_epub
from tests.fixtures.suggestions import add_one
from tests.fixtures.word_sim import simulate_word_review
from tests.jobs.test_translate_job import (
    blocks,
    deps_for,
    drain,
    echo,
    imported,
    registry_for,
    start,
)
from tests.translate.fakes import echo_translator, scripted

PUBLIC = Path(__file__).resolve().parent.parent / "fixtures" / "public"


def translated(sf, storage, test_settings, data, filename="b.docx", **kw):
    t = imported(sf, storage, data=data, filename=filename, **kw)
    reg = registry_for(test_settings, FakeProvider(echo()))
    start(sf, reg, t)
    drain(sf, deps_for(reg))
    return t


def flagged_docx(sf, storage, test_settings):
    """A DOCX translation whose second and third paragraphs are flagged."""
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
    reg = registry_for(test_settings, provider)
    start(sf, reg, t)
    drain(sf, deps_for(reg))
    return t, (b1, b2, b3)


def test_clean_docx_export_of_translated_book_has_translation_and_accepted_fix(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["# One", "First para.", "Second para."])
    t = translated(session_factory, tmp_storage, test_settings, data, translated_title="Eins")
    heading, first, second = blocks(session_factory, t)
    assert first.text == "DE First para."
    with session_factory() as s:
        fix = add_one(s, s.get(Block, first.id), 3, 8, "First", "Erster")
        decide(s, fix.id, "accept", actor="ed")
        f = export_book(s, tmp_storage, t, "docx-clean")
        s.commit()
    assert [b.text for b in all_blocks(f.data)] == ["DE One", "DE Erster para.", "DE Second para."]
    with session_factory() as s:
        source_id = s.get(Book, t).source_book_id
    assert (tmp_storage.root / "books" / str(t) / "exports" / f.filename).exists()
    assert not (tmp_storage.root / "books" / str(source_id) / "exports").exists()
    assert tmp_storage.read_original(source_id) == data  # the original is never changed


def test_review_copy_and_review_import_round_trip_on_translated_book(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["First para.", "Second para."])
    t = translated(session_factory, tmp_storage, test_settings, data)
    first, second = blocks(session_factory, t)
    with session_factory() as s:
        add_one(s, s.get(Block, first.id), 3, 8, "First", "Erster")
        add_one(s, s.get(Block, second.id), 3, 9, "Second", "Zweiter")
        copy = export_book(s, tmp_storage, t, "docx-review")
        s.commit()
    # the review copy is made on the translated base, the suggestions as tracked changes
    reviewed = simulate_word_review(copy.data, accept=["Erster"], reject=["Zweiter"])
    with session_factory() as s:
        report = import_review(s, t, reviewed, actor="ed")
        s.commit()
    assert (report.counts["accepted"], report.counts["rejected"]) == (1, 1)
    assert report.unmatched_block_ids == [] and report.conflicts == []
    with session_factory() as s:
        clean = export_book(s, tmp_storage, t, "docx-clean")
    assert [b.text for b in all_blocks(clean.data)] == ["DE Erster para.", "DE Second para."]


def test_export_refused_while_flagged(session_factory, tmp_storage, test_settings):
    t, (_, b2, b3) = flagged_docx(session_factory, tmp_storage, test_settings)
    for fmt in ("docx-clean", "docx-tracked", "docx-review"):
        with session_factory() as s:
            with pytest.raises(Conflict) as exc:
                export_book(s, tmp_storage, t, fmt)
        assert str(exc.value).startswith(
            f"2 paragraphs are not translated yet: blocks [{b2}, {b3}]"
        )
        assert "keep" in str(exc.value)  # a DOCX block can keep its source text instead


def test_kept_block_is_exported_with_its_text(session_factory, tmp_storage, test_settings):
    t, (_, b2, b3) = flagged_docx(session_factory, tmp_storage, test_settings)
    with session_factory() as s:
        keep_source_block(s, b2, actor="ed")
        keep_source_block(s, b3, actor="ed")
        f = export_book(s, tmp_storage, t, "docx-clean")
        s.commit()
    assert [b.text for b in all_blocks(f.data)] == ["Erster.", "Second para.", "Third para."]


def test_epub_clean_export_of_public_english_epub(session_factory, tmp_storage, test_settings):
    data = (PUBLIC / "pg932.epub").read_bytes()
    t = translated(
        session_factory, tmp_storage, test_settings, data, "pg932.epub", translated_title="Titel"
    )
    with session_factory() as s:
        f = export_book(s, tmp_storage, t, "epub-clean")
        stmt = select(Chapter.source_ref, Chapter.title).where(Chapter.book_id == t)
        titles = dict(s.execute(stmt).all())
    book = read_epub(f.data)
    assert book.title == "Titel" and book.language == "de-DE"
    stored = {b.source_ref: b.text for b in blocks(session_factory, t)}
    assert {b.key: b.text for c in book.chapters for b in c.blocks} == stored
    # the TOC labels are the translated chapter titles
    relabelled = {ref: title for ref, title in titles.items() if title.startswith("DE ")}
    assert relabelled
    assert relabelled.items() <= {c.source_ref: c.title for c in book.chapters}.items()


def test_epub_translation_whitespace_is_normalized_when_stored(
    session_factory, tmp_storage, test_settings
):
    data = make_epub([("Chapter One", ["A *fine* line."])])
    t = imported(session_factory, tmp_storage, data=data, filename="b.epub")
    line = blocks(session_factory, t)[1].id

    def spaced(kwargs):  # the model answers with whitespace an EPUB reader would collapse
        out = echo_translator()(kwargs)
        for b in out["blocks"]:
            if b["block_id"] == line:
                b["text"] = "  <i>Eine</i>\n Zeile  mit\tRaum. "
        return out

    reg = registry_for(test_settings, FakeProvider([spaced] * 5))
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    block = blocks(session_factory, t)[1]
    assert block.translation_status == "done"
    assert block.text == "Eine Zeile mit Raum."
    assert block.marks == [{"start": 0, "end": 4, "kind": "italic", "href": None}]
    with session_factory() as s:
        f = export_book(s, tmp_storage, t, "epub-clean")
    para = read_epub(f.data).chapters[0].blocks[1]
    assert (para.text, para.marks) == ("Eine Zeile mit Raum.", [Mark(0, 4, MarkKind.ITALIC)])


def test_original_for_is_the_stored_original_for_a_proofread_book(session, tmp_storage):
    from jerome.core.books import import_book

    data = make_docx(["Plain."])
    book = import_book(session, tmp_storage, filename="p.docx", data=data, variant="en-GB")
    assert original_for(session, tmp_storage, book) == data


def test_translated_base_maps_writer_failures_to_block_ids(
    session_factory, tmp_storage, test_settings
):
    data = make_docx(["First para.", "Second para."])
    t = translated(session_factory, tmp_storage, test_settings, data)
    first, second = blocks(session_factory, t)
    with session_factory() as s:
        for b in (first, second):  # text the writer refuses: a placeholder it does not have
            s.get(Block, b.id).text = "￼DE broken."
        s.commit()
    with session_factory() as s:
        with pytest.raises(Conflict) as exc:
            translated_base(s, tmp_storage, s.get(Book, t))
    message = str(exc.value)
    assert message.startswith(f"2 paragraphs cannot be written: blocks [{first.id}, {second.id}]")
    assert "placeholders" in message  # the first block's reason


def test_kept_block_keeps_its_original_xml(session_factory, tmp_storage, test_settings):
    # ruling R29: a kept paragraph is not rebuilt: its runs and source w:lang stay as they are
    from tests.fixtures.docx_builders import package_docx, para_xml

    def run(text: str) -> str:
        rpr = '<w:rPr><w:lang w:val="en-GB"/></w:rPr>'
        return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'

    body = "".join(
        f"<w:p>{run(a)}{run(b)}</w:p>"
        for a, b in (("First ", "para."), ("Second ", "para."), ("Third ", "para."))
    )
    t = imported(session_factory, tmp_storage, data=package_docx(body, language="en-GB"))
    b1, b2, b3 = [b.id for b in blocks(session_factory, t)]
    provider = FakeProvider(
        [
            scripted({b1: "Erster.", b2: None, b3: None}),
            scripted({b2: "kaputt <i>"}),
            scripted({b3: "kaputt <i>"}),
        ]
    )
    reg = registry_for(test_settings, provider)
    start(session_factory, reg, t)
    drain(session_factory, deps_for(reg))
    with session_factory() as s:
        keep_source_block(s, b2, actor="ed")
        keep_source_block(s, b3, actor="ed")
        s.commit()
    with session_factory() as s:
        book = s.get(Book, t)
        base = translated_base(s, tmp_storage, book)
        original = tmp_storage.read_original(book.source_book_id)
    assert b'w:val="de-DE"' in para_xml(base, 0)  # translated
    assert para_xml(base, 1) == para_xml(original, 1)
    assert para_xml(base, 2) == para_xml(original, 2)
    assert [b.text for b in all_blocks(base)] == ["Erster.", "Second para.", "Third para."]
