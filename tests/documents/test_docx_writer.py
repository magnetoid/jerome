import io
import random
import shutil
import subprocess
from datetime import UTC, datetime

import docx
import pytest
from lxml import etree

from jerome.documents.changes import Change, apply_changes, validate_changes
from jerome.documents.docx_reader import read_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.documents.docx_xml import (
    NOTE_PARTS,
    is_block_text,
    iter_paragraphs,
    qn,
    read_package,
    write_package,
)
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.errors import InvalidChangeError, UnsupportedDocumentError
from tests.fixtures.docx_builders import (
    all_blocks,
    custom_props,
    make_docx,
    make_fragmented_docx,
    only_block,
    package_docx,
    para_xml,
    part_xml,
    revision_ids,
)

NOW = datetime(2026, 11, 21, tzinfo=UTC)
DOC = "word/document.xml"
NOTES = "word/footnotes.xml"
P0 = f"{DOC}#0"


def clean(src: bytes, changes, **options) -> bytes:
    return write_docx(src, changes, DocxWriteOptions("clean", NOW, **options))


def tracked(src: bytes, changes, **options) -> bytes:
    return write_docx(src, changes, DocxWriteOptions("tracked", NOW, **options))


# --- from the brief -------------------------------------------------------------------


def test_roundtrip_without_changes_preserves_text_and_marks():
    src = make_docx(
        ["# One", "Plain **bold** [l](https://x.org)[^1].", "|a|b|"], footnotes={1: "n"}
    )
    assert read_docx(write_docx(src, {}, DocxWriteOptions("clean", NOW))) == read_docx(src)


def test_untouched_paragraph_xml_byte_identical():
    src = make_docx(["first para", "second para"])
    out = write_docx(
        src, {"word/document.xml#1": [Change(0, 6, "Second")]}, DocxWriteOptions("clean", NOW)
    )
    assert para_xml(out, 0) == para_xml(src, 0)


def test_clean_change_across_runs_keeps_per_char_formatting():
    src = make_docx(["Plain **bold** text"])  # "bold" at 6..10
    out = write_docx(
        src, {"word/document.xml#0": [Change(8, 13, "LD TE")]}, DocxWriteOptions("clean", NOW)
    )
    b = only_block(out)
    # insertion inherits char before
    assert b.text == "Plain boLD TExt" and Mark(6, 13, MarkKind.BOLD) in b.marks


def test_fragmented_change_applies():  # Review Focus 1
    out = write_docx(
        make_fragmented_docx(),
        {"word/document.xml#0": [Change(4, 9, "slow")]},
        DocxWriteOptions("clean", NOW),
    )
    assert only_block(out).text == "The slow brown fox."


def test_tracked_change_emits_ins_and_del():
    out = write_docx(
        make_docx(["teh cat"]),
        {"word/document.xml#0": [Change(0, 3, "the")]},
        DocxWriteOptions("tracked", NOW),
    )
    doc = part_xml(out, "word/document.xml")
    assert doc.count("<w:del ") == 1 and doc.count("<w:ins ") == 1 and 'w:author="Jerome"' in doc
    assert 'w:date="2026-11-21T00:00:00Z"' in doc
    with pytest.raises(UnsupportedDocumentError):
        read_docx(out)  # it is now a document with pending changes


def test_revision_ids_unique_across_document():
    changes = {
        "word/document.xml#0": [Change(0, 1, "A")],
        "word/document.xml#1": [Change(0, 1, "B")],
    }
    ids = revision_ids(write_docx(make_docx(["a", "b"]), changes, DocxWriteOptions("tracked", NOW)))
    assert len(ids) == len(set(ids))


def test_comment_written_for_change_with_comment():
    ch = Change(0, 3, "the", comment="[typo · high] Misspelling.")
    out = write_docx(
        make_docx(["teh"]), {"word/document.xml#0": [ch]}, DocxWriteOptions("tracked", NOW)
    )
    assert "[typo · high] Misspelling." in part_xml(out, "word/comments.xml")
    assert "commentRangeStart" in part_xml(out, "word/document.xml")


def test_bookmarks_wrap_paragraphs():
    opts = DocxWriteOptions("tracked", NOW, bookmarks={"word/document.xml#0": "_jb7"})
    assert 'w:name="_jb7"' in part_xml(write_docx(make_docx(["x"]), {}, opts), "word/document.xml")


def test_custom_property_written():
    opts = DocxWriteOptions("tracked", NOW, custom_properties={"jerome_review_export_id": "42"})
    assert custom_props(write_docx(make_docx(["x"]), {}, opts)) == {"jerome_review_export_id": "42"}


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("soffice") is None, reason="LibreOffice (soffice) is not installed"
)
def test_libreoffice_converts_tracked_output(tmp_path):
    src = make_docx(["# Title", "teh cat sat[^1].", "|a|b|"], footnotes={1: "A nte."})
    changes = {
        P0: [Change(0, 5, "Heading", comment="[style · low] Clearer.")],
        f"{DOC}#1": [Change(0, 3, "The", comment="[typo · high] Misspelling.")],
        f"{NOTES}#0": [Change(4, 7, "note")],
    }
    path = tmp_path / "review.docx"
    path.write_bytes(
        tracked(src, changes, bookmarks={P0: "_jb1"}, custom_properties={"jerome_id": "1"})
    )
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(path)],
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review.pdf").stat().st_size > 0


# --- clean mode -----------------------------------------------------------------------


def test_zero_width_insertion_inherits_char_before():
    src = make_docx(["Plain **bold** text"])
    b = only_block(clean(src, {P0: [Change(10, 10, "er"), Change(2, 2, "-")]}))
    assert b.text == "Pl-ain bolder text"
    assert Mark(7, 13, MarkKind.BOLD) in b.marks  # "bolder": after a bold char


def test_insertion_at_paragraph_start_takes_first_run_format():
    b = only_block(clean(make_docx(["**Bold** start"]), {P0: [Change(0, 0, "Very ")]}))
    assert b.text == "Very Bold start" and Mark(0, 9, MarkKind.BOLD) in b.marks


def test_replacing_whole_formatted_word_keeps_its_format():
    # A replacement takes the format of the first character it replaces, as typing over a
    # selection does in Word; the character before is plain here.
    b = only_block(clean(make_docx(["A *Hamlte* play"]), {P0: [Change(2, 8, "Hamlet")]}))
    assert b.text == "A Hamlet play" and Mark(2, 8, MarkKind.ITALIC) in b.marks


def test_replacement_inside_hyperlink_stays_linked():
    src = make_docx(["See [teh link](https://x.org) now."])
    b = only_block(clean(src, {P0: [Change(4, 7, "the")]}))
    assert b.text == "See the link now."
    assert Mark(4, 12, MarkKind.LINK, "https://x.org") in b.marks


def test_insertion_inside_hyperlink_goes_into_the_link():
    src = make_docx(["See [the link](https://x.org) now."])
    b = only_block(clean(src, {P0: [Change(7, 7, " best")]}))
    assert b.text == "See the best link now."
    assert Mark(4, 17, MarkKind.LINK, "https://x.org") in b.marks


def test_insertion_after_note_reference_drops_reference_styling():
    src = make_docx(["Text[^1] more."], footnotes={1: "Note."})
    out = clean(src, {P0: [Change(5, 5, ",")], f"{NOTES}#0": [Change(1, 1, "See:")]})
    assert [b.text for b in all_blocks(out)] == [
        f"Text{PLACEHOLDER}, more.",
        f"{PLACEHOLDER}See: Note.",
    ]
    for part, text in ((DOC, ","), (NOTES, "See:")):
        (t,) = [
            t
            for t in etree.fromstring(part_xml(out, part).encode()).iter(qn("w:t"))
            if t.text == text
        ]
        rpr = t.getparent().find(qn("w:rPr"))
        assert rpr is None or (
            rpr.find(qn("w:rStyle")) is None and rpr.find(qn("w:vertAlign")) is None
        )


def test_change_in_footnote_paragraph():
    src = make_docx(["Body[^1]."], footnotes={1: "A nte."})
    out = clean(src, {f"{NOTES}#0": [Change(4, 7, "note")]})
    assert [b.text for b in all_blocks(out)] == [f"Body{PLACEHOLDER}.", f"{PLACEHOLDER} A note."]
    original, written = read_package(src), read_package(out)
    assert [name for name in original if original[name] != written[name]] == [NOTES]


def test_untouched_parts_keep_original_bytes():
    src = make_docx(["one", "two"], footnotes={1: "n"})
    original, written = read_package(src), read_package(clean(src, {P0: [Change(0, 3, "1")]}))
    assert written.keys() == original.keys()
    assert [name for name in original if original[name] != written[name]] == [DOC]


def test_split_text_nodes_preserve_spaces():
    src = package_docx("<w:p><w:r><w:t>abc def ghi</w:t></w:r></w:p>")
    outputs = (
        (clean(src, {P0: [Change(4, 7, "DEF")]}), 3),
        (tracked(src, {P0: [Change(4, 7, "x")]}), 4),
    )
    for out, count in outputs:
        root = etree.fromstring(part_xml(out, DOC).encode())
        texts = list(root.iter(qn("w:t"), qn("w:delText")))
        assert len(texts) == count
        assert all(
            t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve" for t in texts
        )
    assert only_block(clean(src, {P0: [Change(4, 7, "DEF")]})).text == "abc DEF ghi"


def test_change_next_to_field_spanning_paragraphs():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Before </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> TOC \\o </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>Entry one</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Entry two</w:t></w:r></w:p>"
        '<w:p><w:r><w:t>Entry three</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:r><w:t xml:space="preserve"> After tetx.</w:t></w:r></w:p>'
    )
    src = package_docx(body)
    last = f"{DOC}#2"
    out = clean(src, {P0: [Change(7, 7, "the ")], last: [Change(1, 1, ":"), Change(8, 12, "text")]})
    assert [b.text for b in all_blocks(out)] == [
        f"Before the {PLACEHOLDER}",
        f"{PLACEHOLDER}: After text.",
    ]
    # No place outside the field for these: before a field continued from the previous
    # paragraph, after a field continuing into the next, or in a paragraph inside one.
    for changes in (
        {last: [Change(0, 0, "x")]},
        {P0: [Change(8, 8, "x")]},
        {f"{DOC}#1": [Change(0, 0, "x")]},
    ):
        with pytest.raises(InvalidChangeError):
            clean(src, changes)


# --- tracked mode ---------------------------------------------------------------------


def test_tracked_multi_change_paragraph_has_a_comment_around_each_change():
    notes = ["[typo · high] a", "[typo · high] b", "[typo · medium] c"]
    changes = [
        Change(0, 3, "the", comment=notes[0]),
        Change(8, 11, "sat", comment=notes[1]),
        Change(15, 18, "", comment=notes[2]),
    ]
    out = tracked(make_docx(["teh cat sta on teh mat"]), {P0: changes})
    (p,) = [p for _, p, _ in iter_paragraphs(read_package(out))]
    ref = qn("w:commentReference")
    shape = ["ref" if child.find(ref) is not None else etree.QName(child).localname for child in p]
    change = ["commentRangeStart", "del", "ins", "commentRangeEnd", "ref"]
    deletion = ["commentRangeStart", "del", "commentRangeEnd", "ref"]
    assert shape == [*change, "r", *change, "r", *deletion, "r"]

    ranges = [el.get(qn("w:id")) for el in p.iter(qn("w:commentRangeStart"))]
    assert ranges == [el.get(qn("w:id")) for el in p.iter(qn("w:commentRangeEnd"))]
    assert ranges == [el.get(qn("w:id")) for el in p.iter(ref)]
    comments = {c.comment_id: c for c in docx.Document(io.BytesIO(out)).comments}
    assert [comments[int(i)].text for i in ranges] == notes
    assert {comments[int(i)].author for i in ranges} == {"Jerome"}
    ids = revision_ids(out)
    assert len(ids) == 5 == len(set(ids))
    assert only_block(_resolve(out, accept=True)).text == "the cat sat on  mat"


def test_fragmented_word_is_one_deletion():
    # "quick" spans four runs, with proofErr, a bookmark and an empty run between them
    out = tracked(make_fragmented_docx(), {P0: [Change(4, 9, "slow")]})
    doc = part_xml(out, DOC)
    assert doc.count("<w:del ") == 1 and doc.count("<w:ins ") == 1
    assert only_block(_resolve(out, accept=True)).text == "The slow brown fox."


def test_revision_ids_unique_across_parts():
    src = make_docx(["Body[^1] txet."], footnotes={1: "A nte."})
    out = tracked(src, {P0: [Change(6, 10, "text")], f"{NOTES}#0": [Change(4, 7, "note")]})
    ids = revision_ids(out)
    assert len(ids) == 4 == len(set(ids))


def test_ids_continue_after_existing_ones():
    body = (
        '<w:p><w:bookmarkStart w:id="3" w:name="_GoBack"/><w:r><w:rPr><w:b/><w:rPrChange '
        'w:id="7" w:author="Ed" w:date="2026-01-01T00:00:00Z"><w:rPr/></w:rPrChange></w:rPr>'
        '<w:t>teh cat</w:t></w:r><w:bookmarkEnd w:id="3"/></w:p>'
    )
    out = tracked(
        package_docx(body), {P0: [Change(0, 3, "the", comment="c")]}, bookmarks={P0: "_jb1"}
    )
    root = etree.fromstring(part_xml(out, DOC).encode())
    revisions = [int(el.get(qn("w:id"))) for el in root.iter(qn("w:ins"), qn("w:del"))]
    assert len(revisions) == 2 and min(revisions) > 7
    # copies of the rPrChange (in the split-off run and the new run) get ids of their own
    revisions += [int(el.get(qn("w:id"))) for el in root.iter(qn("w:rPrChange"))]
    assert len(revisions) == 5 == len(set(revisions)) and 7 in revisions
    starts = {el.get(qn("w:name")): el.get(qn("w:id")) for el in root.iter(qn("w:bookmarkStart"))}
    assert starts == {"_GoBack": "3", "_jb1": "4"}


def test_existing_comments_part_is_extended():
    src = make_docx(["teh", "cta"])
    once = tracked(src, {P0: [Change(0, 3, "the", comment="first")]})
    twice = tracked(once, {f"{DOC}#1": [Change(0, 3, "cat", comment="second")]})
    comments = list(docx.Document(io.BytesIO(twice)).comments)
    assert [c.text for c in comments] == ["first", "second"]
    assert len({c.comment_id for c in comments}) == 2
    assert part_xml(twice, "[Content_Types].xml").count("/word/comments.xml") == 1
    assert part_xml(twice, "word/_rels/document.xml.rels").count("comments.xml") == 1
    ids = revision_ids(twice)
    assert len(ids) == 4 == len(set(ids))


def test_tracked_accept_all_equals_clean_and_reject_all_equals_original():
    src = make_docx(["Plain **bold** text", "teh [lnik](https://x.org)."])
    changes = {
        P0: [Change(8, 13, "LD TE"), Change(15, 15, "!")],
        f"{DOC}#1": [Change(4, 8, "link")],
    }
    out = tracked(src, changes)
    assert all_blocks(_resolve(out, accept=True)) == all_blocks(clean(src, changes))
    assert all_blocks(_resolve(out, accept=False)) == all_blocks(src)


# --- bookmarks and custom properties ---------------------------------------------------


@pytest.mark.parametrize("mode", ["clean", "tracked"])
def test_bookmark_starts_after_ppr_and_ends_last(mode):
    src = make_docx(["Body[^1] text."], footnotes={1: "Note."})
    bookmarks = {P0: "_jb1", f"{NOTES}#0": "_jb2"}
    options = DocxWriteOptions(mode, NOW, bookmarks=bookmarks)
    out = write_docx(src, {P0: [Change(6, 10, "words")]}, options)
    starts = {}
    for key, p, _ in iter_paragraphs(read_package(out)):
        if key in bookmarks:
            first = 1 if p[0].tag == qn("w:pPr") else 0
            start, end = p[first], p[-1]
            assert (start.tag, end.tag) == (qn("w:bookmarkStart"), qn("w:bookmarkEnd"))
            assert start.get(qn("w:id")) == end.get(qn("w:id"))
            starts[start.get(qn("w:name"))] = start.get(qn("w:id"))
    assert starts.keys() == {"_jb1", "_jb2"} and len(set(starts.values())) == 2


def test_bookmark_id_follows_existing_bookmarks():
    out = clean(make_fragmented_docx(), {}, bookmarks={P0: "_jb3"})  # it has _GoBack, id 0
    assert '<w:bookmarkStart w:id="1" w:name="_jb3"/>' in part_xml(out, DOC)
    assert only_block(out).text == "The quick brown fox."


def test_custom_properties_replace_and_extend_existing_ones():
    once = clean(make_docx(["x"]), {}, custom_properties={"a": "1", "b": "2"})
    twice = clean(once, {}, custom_properties={"b": "3", "c": "4"})
    assert custom_props(twice) == {"a": "1", "b": "3", "c": "4"}
    root = etree.fromstring(read_package(twice)["docProps/custom.xml"])
    assert sorted(prop.get("pid") for prop in root) == ["2", "3", "4"]
    assert part_xml(twice, "_rels/.rels").count("docProps/custom.xml") == 1
    assert docx.Document(io.BytesIO(twice)).paragraphs[0].text == "x"


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, bookmarks",
    [
        ({f"{DOC}#9": [Change(0, 0, "x")]}, {}),  # no such paragraph
        ({f"{DOC}#1": [Change(0, 0, "x")]}, {}),  # empty paragraph: not a block
        ({"word/endnotes.xml#0": []}, {}),
        ({}, {f"{DOC}#9": "_jb1"}),
        ({P0: [Change(0, 2, "x"), Change(1, 3, "y")]}, {}),  # overlapping
        ({P0: [Change(0, 1, "bad\x01char")]}, {}),  # not XML text
    ],
)
def test_invalid_changes_and_keys_raise(changes, bookmarks):
    with pytest.raises(InvalidChangeError):
        clean(make_docx(["abc", ""]), changes, bookmarks=bookmarks)


# --- round trip over random changes ---------------------------------------------------

RICH = make_docx(
    [
        "# A heading",
        "Plain **bold** and *italic* with [a *link*](https://x.org) and a tab\there.",
        "A note[^1] and **[bold link](https://y.org)** end.",
        "|cell **one**|two|",
    ],
    footnotes={1: "A *note* with [a link](https://z.org)."},
)


@pytest.mark.parametrize("src", [RICH, make_fragmented_docx()], ids=["rich", "fragmented"])
def test_random_changes_round_trip(src):
    rng = random.Random(7)
    blocks = all_blocks(src)
    for _ in range(60):
        changes = {b.key: _random_changes(rng, b.text) for b in blocks if rng.random() < 0.7}
        out = clean(src, changes)
        expected = [(b.key, apply_changes(b.text, changes.get(b.key, []))) for b in blocks]
        # a paragraph whose text is all deleted is no longer a block
        assert [(b.key, b.text) for b in all_blocks(out)] == [
            (key, text) for key, text in expected if is_block_text(text)
        ]
        review = tracked(src, changes)
        ids = revision_ids(review)
        assert len(ids) == len(set(ids))
        assert all_blocks(_resolve(review, accept=True)) == all_blocks(out)
        assert all_blocks(_resolve(review, accept=False)) == blocks


def _random_changes(rng: random.Random, text: str) -> list[Change]:
    while True:
        changes = []
        for _ in range(rng.randint(1, 3)):
            start = rng.randint(0, len(text))
            end = min(len(text), start + rng.choice([0, 0, 1, 2, 4, 9]))
            replacement = "".join(rng.choice("xy zÄ“") for _ in range(rng.randint(0, 4)))
            comment = "[typo · low] why" if rng.random() < 0.5 else None
            changes.append(Change(start, end, replacement, comment=comment))
        try:
            return validate_changes(text, changes)
        except InvalidChangeError:
            continue


def _resolve(data: bytes, *, accept: bool) -> bytes:
    """Accept, or reject, every tracked insertion and deletion, the way Word does."""
    package = read_package(data)
    gone, kept = (qn("w:del"), qn("w:ins")) if accept else (qn("w:ins"), qn("w:del"))
    for part in (DOC, *NOTE_PARTS):
        if part not in package:
            continue
        root = etree.fromstring(package[part])
        for el in list(root.iter(gone)):
            el.getparent().remove(el)
        for el in list(root.iter(kept)):
            for text in el.iter(qn("w:delText")):
                text.tag = qn("w:t")
            for child in list(el):
                el.addprevious(child)
            el.getparent().remove(el)
        package[part] = etree.tostring(root)
    return write_package(package, data)
