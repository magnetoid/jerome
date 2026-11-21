import io
import zipfile

import pytest

from jerome.documents.docx_reader import read_docx
from jerome.documents.docx_xml import (
    iter_paragraphs,
    paragraph_runs,
    qn,
    read_package,
    write_package,
)
from jerome.documents.model import PLACEHOLDER, BlockKind, Mark, MarkKind
from jerome.errors import UnsupportedDocumentError
from tests.fixtures.docx_builders import (
    all_blocks,
    custom_props,
    make_docx,
    make_fragmented_docx,
    make_textbox_docx,
    make_tracked_docx,
    only_block,
    package_docx,
    para_xml,
    part_xml,
    revision_ids,
)


def test_reads_text_and_marks():
    b = read_docx(make_docx(["Plain **bold** and *italic*."])).chapters[0].blocks[0]
    assert b.text == "Plain bold and italic."
    assert Mark(6, 10, MarkKind.BOLD) in b.marks and Mark(15, 21, MarkKind.ITALIC) in b.marks


def test_link_mark_has_href():
    b = only_block(make_docx(["See [here](https://example.org)."]))
    assert Mark(4, 8, MarkKind.LINK, "https://example.org") in b.marks


def test_footnote_reference_is_placeholder_and_footnote_is_block():
    book = read_docx(make_docx(["Text[^1] more."], footnotes={1: "A note."}))
    blocks = [b for c in book.chapters for b in c.blocks]
    assert blocks[0].text == f"Text{PLACEHOLDER} more."
    assert blocks[-1].kind == BlockKind.FOOTNOTE
    assert blocks[-1].key.startswith("word/footnotes.xml#")


def test_tab_is_placeholder():
    assert only_block(make_docx(["a\tb"])).text == f"a{PLACEHOLDER}b"


def test_chapters_split_at_heading_1():
    book = read_docx(make_docx(["# One", "a", "## Sub", "b", "# Two", "c"]))
    assert [c.title for c in book.chapters] == ["One", "Two"]
    assert book.chapters[0].blocks[0].kind == BlockKind.HEADING


def test_custom_style_with_outline_level_0_starts_chapter():
    book = read_docx(
        make_docx(
            ["@ChapterTitle: Eins", "x", "@ChapterTitle: Zwei", "y"], styles={"ChapterTitle": 0}
        )
    )
    assert [c.title for c in book.chapters] == ["Eins", "Zwei"]


def test_no_headings_splits_into_5000_word_parts():
    book = read_docx(make_docx([" ".join(["word"] * 100)] * 120))  # 12,000 words
    assert [c.title for c in book.chapters] == ["Part 1", "Part 2", "Part 3"]


def test_table_cells_are_blocks():
    kinds = {b.kind for b in all_blocks(make_docx(["|a|b|"]))}
    assert kinds == {BlockKind.TABLE_CELL}


def test_text_box_paragraphs_skipped():
    assert all("textbox" not in b.text for b in all_blocks(make_textbox_docx()))


def test_refuses_unresolved_tracked_changes():
    with pytest.raises(UnsupportedDocumentError, match="tracked changes"):
        read_docx(make_tracked_docx())


def test_fragmented_runs_read_as_one_text():  # Review Focus 1
    b = only_block(make_fragmented_docx())
    assert b.text == "The quick brown fox."
    assert Mark(10, 15, MarkKind.BOLD) in b.marks


def test_block_keys_unique_and_ordered():
    keys = [b.key for b in all_blocks(make_docx(["a", "b", "c"]))]
    assert keys == ["word/document.xml#0", "word/document.xml#1", "word/document.xml#2"]


# --- Guarantees the writer (Task 3) and the review reader (Task 19) rely on ---


def _runs(data: bytes, key: str = "word/document.xml#0"):
    return paragraph_runs(next(p for k, p, _ in iter_paragraphs(read_package(data)) if k == key))


def _assert_pieces_cover(pt):
    pos = 0
    for piece in pt.pieces:
        assert piece.start == pos < piece.end
        if piece.is_text:
            assert piece.node.text == pt.text[piece.start : piece.end]
        else:
            assert pt.text[piece.start : piece.end] == PLACEHOLDER
        pos = piece.end
    assert pos == len(pt.text)


def test_pieces_map_every_character_to_its_run():
    pt = _runs(make_docx(["a\t**b**[^1] [l](https://x.org)"], footnotes={1: "n"}))
    assert pt.text == f"a{PLACEHOLDER}b{PLACEHOLDER} l"
    _assert_pieces_cover(pt)
    assert all(piece.run.tag == qn("w:r") for piece in pt.pieces)
    assert [piece.node.tag for piece in pt.pieces[:2]] == [qn("w:t"), qn("w:tab")]
    assert pt.pieces[0].run is pt.pieces[1].run  # the tab sits in the same run as "a"


def test_fragmented_pieces_point_at_each_run():  # Review Focus 1
    pt = _runs(make_fragmented_docx())
    _assert_pieces_cover(pt)
    texts = [pt.text[p.start : p.end] for p in pt.pieces]
    assert texts == ["The ", "q", "u", "i", "ck", " ", "brown", " fox."]
    assert len({id(p.run) for p in pt.pieces}) == len(pt.pieces)


def test_fields_are_single_placeholders():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Page </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>3</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:r><w:t xml:space="preserve"> of </w:t></w:r>'
        '<w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>9</w:t></w:r></w:fldSimple>'
        "<w:r><w:t>.</w:t></w:r></w:p>"
    )
    pt = _runs(package_docx(body))
    assert pt.text == f"Page {PLACEHOLDER} of {PLACEHOLDER}."
    _assert_pieces_cover(pt)
    field, simple = pt.pieces[1], pt.pieces[3]
    runs = field.run.getparent().findall(qn("w:r"))
    assert (field.run, field.last_run) == (runs[1], runs[5])  # begin run … end run
    assert simple.run is simple.node and simple.node.tag == qn("w:fldSimple")


def test_runs_inside_smart_tags_and_content_controls_are_text():
    body = (
        '<w:p><w:smartTag w:uri="urn:x" w:element="place"><w:r><w:t>Vienna</w:t></w:r>'
        '</w:smartTag><w:sdt><w:sdtPr/><w:sdtContent><w:r><w:t xml:space="preserve"> is </w:t>'
        '</w:r></w:sdtContent></w:sdt><w:customXml w:element="x"><w:r><w:rPr><w:i/></w:rPr>'
        "<w:t>old</w:t></w:r></w:customXml></w:p>"
    )
    b = only_block(package_docx(body))
    assert (b.text, b.marks) == ("Vienna is old", [Mark(10, 13, MarkKind.ITALIC)])


def test_marks_come_from_direct_run_properties_and_merge():
    body = (
        "<w:p>"
        '<w:r><w:rPr><w:b/><w:u w:val="single"/></w:rPr><w:t>ab</w:t></w:r>'
        '<w:r><w:rPr><w:b w:val="1"/></w:rPr><w:t>cd</w:t></w:r>'
        '<w:r><w:rPr><w:b w:val="0"/><w:smallCaps/><w:strike/></w:rPr><w:t>ef</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>g</w:t></w:r>'
        '<w:r><w:rPr><w:u w:val="none"/><w:vertAlign w:val="subscript"/></w:rPr><w:t>h</w:t></w:r>'
        '<w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr><w:t>i</w:t></w:r>'
        "</w:p>"
    )
    assert only_block(package_docx(body)).marks == [
        Mark(0, 2, MarkKind.UNDERLINE),
        Mark(0, 4, MarkKind.BOLD),
        Mark(4, 6, MarkKind.SMALLCAPS),
        Mark(4, 6, MarkKind.STRIKE),
        Mark(6, 7, MarkKind.SUPERSCRIPT),
        Mark(7, 8, MarkKind.SUBSCRIPT),
    ]


# --- Reader behaviour beyond the brief's tests ---


def test_text_box_anchor_is_one_placeholder():
    texts = [b.text for b in all_blocks(make_textbox_docx())]
    assert texts == [f"Body text with a box.{PLACEHOLDER}", "After the box."]


def test_outline_level_from_style_chain_or_direct():
    styles = (
        '<w:style w:type="paragraph" w:styleId="Base"><w:name w:val="Base"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Derived"><w:name w:val="Derived"/>'
        '<w:basedOn w:val="Base"/></w:style>'
    )
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Derived"/></w:pPr><w:r><w:t>Eins</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Zwei</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:outlineLvl w:val="9"/></w:pPr>'
        "<w:r><w:t>not a heading</w:t></w:r></w:p>"
    )
    book = read_docx(package_docx(body, styles=styles))
    assert [c.title for c in book.chapters] == ["Eins", "Zwei"]
    assert [b.kind for b in book.chapters[1].blocks] == [BlockKind.HEADING, BlockKind.PARAGRAPH]


def test_text_before_first_chapter_heading_is_kept():
    book = read_docx(make_docx(["Dedication", "# One", "a"]))
    assert [(c.title, c.source_ref, [b.text for b in c.blocks]) for c in book.chapters] == [
        ("Front matter", "word/document.xml#0", ["Dedication"]),
        ("One", "word/document.xml#1", ["One", "a"]),
    ]
    assert all(c.proofread for c in book.chapters)


def test_footnotes_follow_the_body_in_the_last_chapter():
    book = read_docx(
        make_docx(
            ["# One", "a[^1]", "# Two", "b[^2]"],
            footnotes={1: "See [x](https://a.org).", 2: "Second."},
        )
    )
    notes = book.chapters[-1].blocks[-2:]
    assert [(b.key, b.kind, b.text) for b in notes] == [
        ("word/footnotes.xml#0", BlockKind.FOOTNOTE, f"{PLACEHOLDER} See x."),
        ("word/footnotes.xml#1", BlockKind.FOOTNOTE, f"{PLACEHOLDER} Second."),
    ]
    assert Mark(6, 7, MarkKind.LINK, "https://a.org") in notes[0].marks


def test_empty_paragraphs_are_not_blocks_but_keep_their_index():
    keys = [b.key for b in all_blocks(make_docx(["a", "", "b"]))]
    assert keys == ["word/document.xml#0", "word/document.xml#2"]


def test_title_and_language_from_package():
    body = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    book = read_docx(package_docx(body, title="Der Titel", language="de-DE"))
    assert (book.format, book.title, book.language) == ("docx", "Der Titel", "de-DE")
    assert read_docx(make_docx(["x"])).title is None


def _zip(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        _zip({"hello.txt": b"hi"}),
        _zip({"word/document.xml": b"<w:document"}),
    ],
    ids=["not-zip", "no-document", "bad-xml"],
)
def test_refuses_non_docx(data):
    with pytest.raises(UnsupportedDocumentError):
        read_docx(data)


def test_write_package_keeps_order_and_compression():
    original = io.BytesIO()
    with zipfile.ZipFile(original, "w") as zf:
        zf.writestr("b.xml", b"<b/>", compress_type=zipfile.ZIP_STORED)
        zf.writestr("a.xml", b"<a/>", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("gone.xml", b"<g/>")
    package = read_package(original.getvalue())
    package["a.xml"] = b"<a>changed</a>"
    package["new.xml"] = b"<n/>"
    del package["gone.xml"]
    with zipfile.ZipFile(io.BytesIO(write_package(package, original.getvalue()))) as out:
        assert [(i.filename, i.compress_type) for i in out.infolist()] == [
            ("b.xml", zipfile.ZIP_STORED),
            ("a.xml", zipfile.ZIP_DEFLATED),
            ("new.xml", zipfile.ZIP_DEFLATED),
        ]
        assert out.read("a.xml") == b"<a>changed</a>"


def test_inspection_helpers():
    src = make_docx(["first para", "second para"])
    assert b"second para" in para_xml(src, 1) and b"first" not in para_xml(src, 1)
    assert "<w:body>" in part_xml(src, "word/document.xml")
    assert revision_ids(make_tracked_docx()) == ["1"] and revision_ids(src) == []
    assert custom_props(src) == {}
