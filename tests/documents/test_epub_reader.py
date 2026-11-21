import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from jerome.documents.epub_reader import read_epub
from jerome.documents.epub_xml import (
    element_text,
    iter_epub_blocks,
    parse_content_document,
    read_container,
)
from jerome.documents.model import PLACEHOLDER, BlockKind, Mark, MarkKind
from jerome.errors import UnsupportedDocumentError
from tests.fixtures.epub_builders import (
    block,
    block_key,
    blocks_of,
    edit_entry,
    make_epub,
    titles,
    zip_names,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_XHTML = '<html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>'
_WS = " \t\n\r"


def _text(markup: str):
    root = etree.fromstring(_XHTML.format(markup).encode())
    (_, el), *_ = iter_epub_blocks(root)
    return element_text(el)


def _assert_pieces_cover(pt):
    pos = 0
    for piece in pt.pieces:
        assert piece.start == pos < piece.end
        if piece.is_text:
            assert len(piece.spans) == piece.end - piece.start
            for i, (a, b) in enumerate(piece.spans):
                out = pt.text[piece.start + i]
                raw = piece.raw[a:b]
                assert raw == out if out != " " else raw and all(c in _WS for c in raw)
        elif piece.where == "entity":
            assert pt.text[piece.start : piece.end] == piece.raw
        else:
            assert pt.text[piece.start : piece.end] == PLACEHOLDER
        pos = piece.end
    assert pos == len(pt.text)


def test_chapters_follow_nav_toc():
    assert titles(read_epub(make_epub([("One", ["a"]), ("Two", ["b"])]))) == ["One", "Two"]


def test_ncx_toc_used_for_epub2():
    assert titles(read_epub(make_epub([("One", ["a"])], toc="ncx"))) == ["One"]


def test_chapter_split_across_two_files_is_one_chapter():
    book = read_epub(make_epub([("One", ["a", "b"])], split_chapter=True))
    assert len(book.chapters) == 1
    assert [b.text for b in book.chapters[0].blocks] == ["One", "a", "b"]


def test_two_chapters_in_one_file_split_at_anchor():
    book = read_epub(make_epub([("One", ["a"]), ("Two", ["b"])], two_in_one_file=True))
    assert titles(book) == ["One", "Two"]
    assert [b.text for b in book.chapters[1].blocks] == ["Two", "b"]
    assert book.chapters[1].source_ref == "OEBPS/book.xhtml#c2"


def test_front_matter_not_proofread():
    book = read_epub(make_epub([("One", ["a"])], front_matter=True))
    assert [c.proofread for c in book.chapters] == [False, True]


def test_no_toc_falls_back_to_spine():
    book = read_epub(make_epub([("One", ["a"]), ("Two", ["b"])], toc="none"))
    assert titles(book) == ["One", "Two"]


def test_no_toc_front_matter_not_proofread():
    book = read_epub(make_epub([("One", ["a"])], toc="none", front_matter=True))
    assert [c.proofread for c in book.chapters] == [False, True]


def test_linear_no_item_not_proofread():
    data = make_epub([("One", ["a"]), ("Two", ["b"])])
    data = edit_entry(
        data,
        "OEBPS/content.opf",
        lambda s: s.replace('idref="ch2.xhtml"', 'idref="ch2.xhtml" linear="no"'),
    )
    assert [c.proofread for c in read_epub(data).chapters] == [True, False]


def test_metadata():
    book = read_epub(make_epub([("One", ["a"])], title="T", language="de"))
    assert (book.format, book.title, book.language) == ("epub", "T", "de")


def test_inline_marks():
    b = read_epub(make_epub([("C", ["x **b** *i* [l](https://e.org)"])])).chapters[0].blocks[1]
    assert {m.kind for m in b.marks} == {MarkKind.BOLD, MarkKind.ITALIC, MarkKind.LINK}
    assert Mark(2, 3, MarkKind.BOLD) in b.marks
    assert Mark(6, 7, MarkKind.LINK, "https://e.org") in b.marks


def test_cluttered_markup_reads_same_as_clean():
    clean = read_epub(make_epub([("C", ["Hello **big** world"])]))
    messy = read_epub(make_epub([("C", ["Hello **big** world"])], cluttered=True))
    assert blocks_of(clean) == blocks_of(messy)


def test_cluttered_has_one_bold_mark_for_split_word():
    b = block(make_epub([("C", ["Hello **big** world"])], cluttered=True), 0, 1)
    assert b.marks == [Mark(6, 9, MarkKind.BOLD)]


def test_block_keys_use_full_path_and_index():
    data = make_epub([("One", ["a", "b"]), ("Two", ["c"])])
    assert block_key(data, 0, 2) == "OEBPS/ch1.xhtml#2"
    assert block_key(data, 1, 1) == "OEBPS/ch2.xhtml#1"
    assert read_epub(data).chapters[0].source_ref == "OEBPS/ch1.xhtml#"


def test_keys_count_empty_blocks():
    root = etree.fromstring(_XHTML.format("<p>a</p><p/><p>b</p>").encode())
    assert [n for n, _ in iter_epub_blocks(root)] == [0, 1, 2]


def test_whitespace_collapses_and_pieces_map_to_raw_nodes():
    pt = _text("<p>\n  Hello   <b>big\n</b>  world \n</p>")
    assert pt.text == "Hello big world"
    _assert_pieces_cover(pt)
    assert [pt.text[p.start : p.end] for p in pt.pieces] == ["Hello ", "big ", "world"]
    first = pt.pieces[0]
    assert first.where == "text" and first.spans[0] == (3, 4) and first.spans[5] == (8, 11)
    assert pt.pieces[1].where == "text" and pt.pieces[2].where == "tail"
    assert pt.marks == [Mark(6, 9, MarkKind.BOLD)]


def test_whitespace_collapsed_from_later_nodes_is_recorded_on_its_space():
    pt = _text("<p>\n  Hello   <b>big\n</b>  world \n</p>")
    hello, big, world = pt.pieces
    assert hello.collapsed == () and world.collapsed == ()  # the run "   " is all in its span
    assert big.collapsed == ((big.element, "tail", 0, 2),)  # "  " before "world"
    pt = _text("<p>Lead <span> </span> split</p>")
    lead, _ = pt.pieces
    span = lead.element[0]
    assert pt.text == "Lead split"
    assert lead.collapsed == ((span, "text", 0, 1), (span, "tail", 0, 1))


def test_nbsp_is_not_collapsed():
    pt = _text("<p>a  b</p>")
    assert pt.text == "a  b"


def test_noteref_and_images_are_placeholders():
    pt = _text(
        '<p xmlns:epub="http://www.idpf.org/2007/ops">Text<a epub:type="noteref" href="#n1">1</a>'
        ' more<img src="x.png"/><br/>end</p>'
    )
    assert pt.text == f"Text{PLACEHOLDER} more{PLACEHOLDER}{PLACEHOLDER}end"
    _assert_pieces_cover(pt)
    assert [p.element.tag.rpartition("}")[2] for p in pt.pieces if not p.is_text] == [
        "a",
        "img",
        "br",
    ]
    assert not any(m.kind == MarkKind.LINK for m in pt.marks)


def test_inline_style_marks_and_empty_spans():
    pt = _text(
        '<p><span style="font-weight:700">b</span><span></span>'
        '<span style="font-style: italic">i</span>'
        '<span style="font-variant:small-caps">s</span>'
        "<sup>2</sup><sub>3</sub><u>u</u><del>d</del><cite>c</cite></p>"
    )
    kinds = {m.kind for m in pt.marks}
    assert kinds == {
        MarkKind.BOLD, MarkKind.ITALIC, MarkKind.SMALLCAPS, MarkKind.SUPERSCRIPT,
        MarkKind.SUBSCRIPT, MarkKind.UNDERLINE, MarkKind.STRIKE,
    }  # fmt: skip
    assert pt.text == "bis23udc"


def test_block_kinds():
    book = read_epub(
        make_epub([("C", ["x"])]),
    )
    assert book.chapters[0].blocks[0].kind == BlockKind.HEADING
    assert book.chapters[0].blocks[1].kind == BlockKind.PARAGRAPH
    root = etree.fromstring(
        _XHTML.format(
            "<ul><li>a</li></ul><blockquote><p>q</p></blockquote><table><tr><td>c</td></tr></table>"
        ).encode()
    )
    from jerome.documents.epub_xml import block_kind

    assert [block_kind(el) for _, el in iter_epub_blocks(root)] == [
        BlockKind.LIST_ITEM, BlockKind.QUOTE, BlockKind.TABLE_CELL,
    ]  # fmt: skip


def test_html_entities_fall_back_to_html_parser():
    doc = parse_content_document(b"<html><body><p>a&nbsp;b</p></body></html>", "x.xhtml")
    assert doc.parser == "html"
    assert parse_content_document(_XHTML.format("<p>a</p>").encode(), "x").parser == "xml"


def test_xxe_is_not_resolved():
    xml = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&x;</p></body></html>'
    )
    doc = parse_content_document(xml, "x")
    (_, el), *_ = iter_epub_blocks(doc.root)
    assert "root:" not in element_text(el).text


@pytest.mark.parametrize(
    "data",
    [b"not a zip", b""],
)
def test_not_a_zip_is_unsupported(data):
    with pytest.raises(UnsupportedDocumentError):
        read_epub(data)


def _rebuild(data: bytes, drop: str = "", edit: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            if info.filename != drop:
                dst.writestr(info.filename, (edit or {}).get(info.filename, src.read(info)))
    return out.getvalue()


def test_missing_container_is_unsupported():
    with pytest.raises(UnsupportedDocumentError):
        read_epub(_rebuild(make_epub([("One", ["a"])]), drop="META-INF/container.xml"))


def test_missing_opf_is_unsupported():
    with pytest.raises(UnsupportedDocumentError):
        read_epub(_rebuild(make_epub([("One", ["a"])]), drop="OEBPS/content.opf"))


def test_empty_spine_is_unsupported():
    data = edit_entry(
        make_epub([("One", ["a"])]),
        "OEBPS/content.opf",
        lambda s: s.replace('<itemref idref="ch1.xhtml"/>', ""),
    )
    with pytest.raises(UnsupportedDocumentError):
        read_epub(data)


def test_oversized_unzip_is_refused(monkeypatch):
    monkeypatch.setattr("jerome.documents.docx_xml.MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(UnsupportedDocumentError, match="too large"):
        read_container(make_epub([("One", ["a"])]))


def test_container_exposes_zip_order_and_mimetype_first():
    pkg = read_container(make_epub([("One", ["a"])]))
    assert pkg.order == zip_names(make_epub([("One", ["a"])])) and pkg.order[0] == "mimetype"
    assert pkg.opf_path == "OEBPS/content.opf" and pkg.nav_points[0].title == "One"


@pytest.mark.parametrize("name,lang", [("pg932.epub", "en"), ("pg22367.epub", "de")])
def test_public_fixtures_parse(name, lang):
    path = FIXTURES / "public" / name
    if not path.exists():
        pytest.skip(f"{name} missing: run scripts/fetch_public_fixtures.py")
    book = read_epub(path.read_bytes())
    assert book.language.startswith(lang) and sum(len(c.blocks) for c in book.chapters) > 50


_XHTML11 = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
    b'"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body>%s</body></html>'
)


def test_named_entities_under_xhtml_doctype_are_text():
    doc = parse_content_document(_XHTML11 % b"<p>a&nbsp;b&mdash;c &amp; d</p>", "x.xhtml")
    (_, el), *_ = iter_epub_blocks(doc.root)
    pt = element_text(el)
    assert doc.parser == "xml"
    assert pt.text == "a b—c & d"
    _assert_pieces_cover(pt)
    entities = [p for p in pt.pieces if p.where == "entity"]
    assert [(pt.text[p.start : p.end], p.element.name) for p in entities] == [
        (" ", "nbsp"),
        ("—", "mdash"),
    ]


def test_internal_subset_entity_is_text_and_kept_in_tree():
    xml = (
        b'<?xml version="1.0"?><!DOCTYPE html [<!ENTITY nbsp "&#160;">]>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>a&nbsp;b &amp; c</p></body></html>'
    )
    doc = parse_content_document(xml, "x.xhtml")
    (_, el), *_ = iter_epub_blocks(doc.root)
    assert element_text(el).text == "a b & c"
    assert b"&nbsp;" in etree.tostring(doc.root)  # still an entity reference when written back


def test_html_fallback_decodes_utf8_without_declaration():
    data = "<html><body><p>Grüße&nbsp;x</p></body></html>".encode()
    doc = parse_content_document(data, "x.xhtml")
    assert doc.parser == "html"
    (_, el), *_ = iter_epub_blocks(doc.root)
    assert element_text(el).text == "Grüße x"


def test_html_fallback_honours_utf16_bom():
    data = "<html><body><p>Grüße&nbsp;x</p></body></html>".encode("utf-16")
    (_, el), *_ = iter_epub_blocks(parse_content_document(data, "x.xhtml").root)
    assert element_text(el).text == "Grüße x"
