import io
import random
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from lxml import etree

from jerome.documents.changes import Change, apply_changes
from jerome.documents.docx_xml import merge_marks
from jerome.documents.epub_reader import read_epub
from jerome.documents.epub_translate import (
    TranslatedEpubError,
    fit_epub_text,
    write_translated_epub,
)
from jerome.documents.epub_writer import write_epub
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.documents.translated import TranslatedBlock
from jerome.errors import InvalidChangeError
from tests.fixtures.epub_builders import edit_entry, make_epub, titles, unchanged_entries

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CH1 = "OEBPS/ch1.xhtml"
CH2 = "OEBPS/ch2.xhtml"
F = PLACEHOLDER
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
XHTML = "{http://www.w3.org/1999/xhtml}"


def _pg932() -> bytes:
    path = FIXTURES / "public" / "pg932.epub"
    if not path.exists():
        pytest.skip("pg932.epub missing: run scripts/fetch_public_fixtures.py")
    return path.read_bytes()


def translate_all(src: bytes, translate: Callable[[str], str]) -> dict[str, TranslatedBlock]:
    """A TranslatedBlock per block of ``src``. ``translate`` only adds a prefix to the text,
    so the marks shift by its length and the placeholders keep their order."""
    out = {}
    for c in read_epub(src).chapters:
        for b in c.blocks:
            text = translate(b.text)
            assert text.endswith(b.text)
            shift = len(text) - len(b.text)
            marks = [Mark(m.start + shift, m.end + shift, m.kind, m.href) for m in b.marks]
            out[b.key] = TranslatedBlock(text, marks, None)
    return out


def readback(data: bytes) -> dict[str, tuple[str, list[Mark]]]:
    return {b.key: (b.text, b.marks) for c in read_epub(data).chapters for b in c.blocks}


def expected(blocks: dict[str, TranslatedBlock]) -> dict[str, tuple[str, list[Mark]]]:
    return {k: (v.text, v.marks) for k, v in blocks.items()}


def entry(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)


def root_of(data: bytes, name: str) -> etree._Element:
    return etree.fromstring(entry(data, name))


def block_el(data: bytes, name: str, tag: str = "p", n: int = 0) -> etree._Element:
    return root_of(data, name).findall(f".//{XHTML}{tag}")[n]


def with_body(src: bytes, name: str, body: str) -> bytes:
    """``src`` with the body of entry ``name`` replaced."""

    def edit(xml: str) -> str:
        head, _, rest = xml.partition("<body>")
        return f"{head}<body>{body}</body>{rest.partition('</body>')[2]}"

    return edit_entry(src, name, edit)


def translate(src: bytes, blocks: dict[str, TranslatedBlock], **kw) -> bytes:
    kw.setdefault("language", "de-DE")
    kw.setdefault("title", None)
    kw.setdefault("toc_labels", {})
    return write_translated_epub(src, blocks, **kw)


# --- from the brief -------------------------------------------------------------------


def test_translated_epub_reads_back_as_the_translation():
    src = make_epub([("One", ["Hello *it* world."]), ("Two", ["Bye."])])
    blocks = translate_all(src, lambda t: "DE " + t)
    assert len(blocks) == 4
    blocks[f"{CH1}#1"] = TranslatedBlock(
        "Hallo Welt, *es*.", [Mark(0, 5, MarkKind.ITALIC), Mark(12, 14, MarkKind.BOLD)], None
    )
    out = translate(src, blocks, title="Testbuch")
    assert readback(out) == expected(blocks)
    book = read_epub(out)
    assert book.title == "Testbuch" and book.language == "de-DE"
    p = block_el(out, CH1)
    assert [etree.QName(c).localname for c in p] == ["i", "b"]  # the source's i, a new b


@pytest.mark.parametrize("toc", ["nav", "ncx"])
def test_toc_labels_rewritten_in_nav_and_ncx(toc):
    src = make_epub([("One", ["a"]), ("Two", ["b"]), ("Three", ["c"])], toc=toc)
    refs = [c.source_ref for c in read_epub(src).chapters]
    assert refs == [f"{CH1}#", f"{CH2}#", "OEBPS/ch3.xhtml#"]
    labels = {refs[0]: "Eins", refs[2]: "Drei & <mehr>"}  # Two has no mapping: kept
    out = translate(src, {}, toc_labels=labels)
    assert titles(out) == ["Eins", "Two", "Drei & <mehr>"]
    toc_file = "OEBPS/nav.xhtml" if toc == "nav" else "OEBPS/toc.ncx"
    assert unchanged_entries(src, out) >= {CH1, CH2, "OEBPS/ch3.xhtml"}
    assert "Drei &amp; &lt;mehr&gt;" in entry(out, toc_file).decode()


def test_toc_labels_match_anchored_targets():
    src = make_epub([("One", ["a"]), ("Two", ["b"])], two_in_one_file=True)
    refs = [c.source_ref for c in read_epub(src).chapters]
    assert refs == ["OEBPS/book.xhtml#c1", "OEBPS/book.xhtml#c2"]
    out = translate(src, {}, toc_labels={refs[1]: "Zwei", "OEBPS/book.xhtml#c9": "Nichts"})
    assert titles(out) == ["One", "Zwei"]


def test_language_set_in_opf_and_documents():
    src = make_epub([("One", ["a"]), ("Two", ["b"])], toc="nav")
    out = translate(src, translate_all(src, lambda t: "DE " + t), toc_labels={f"{CH1}#": "E"})
    assert read_epub(out).language == "de-DE"
    opf = entry(out, "OEBPS/content.opf").decode()
    assert "<dc:language>de-DE</dc:language>" in opf and "<dc:language>en" not in opf
    for name in (CH1, CH2, "OEBPS/nav.xhtml"):
        root = root_of(out, name)
        assert root.get("lang") == "de-DE" and root.get(XML_LANG) == "de-DE", name


def test_public_english_epub_round_trip():
    src = _pg932()
    blocks = translate_all(src, lambda t: "DE " + t)
    labels = {c.source_ref: "DE " + c.title for c in read_epub(src).chapters[1:]}
    out = translate(src, blocks, title="DE Titel", toc_labels=labels)
    back = readback(out)
    assert back.keys() == blocks.keys()
    assert {k: v[0] for k, v in back.items()} == {k: v.text for k, v in blocks.items()}
    assert back == expected(blocks)
    book = read_epub(out)
    assert [c.title for c in book.chapters[1:]] == list(labels.values())
    assert book.title == "DE Titel" and book.language == "de-DE"


def test_untouched_documents_are_byte_identical():
    src = make_epub([("One", ["Hello *it*."]), ("Two", ["Bye."])], toc="ncx")
    blocks = {k: v for k, v in translate_all(src, lambda t: "DE " + t).items() if k.startswith(CH1)}
    out = translate(src, blocks)
    same = unchanged_entries(src, out)
    assert {CH2, "OEBPS/toc.ncx", "META-INF/container.xml", "mimetype"} <= same
    assert CH1 not in same and "OEBPS/content.opf" not in same
    assert readback(out)[f"{CH2}#0"] == readback(src)[f"{CH2}#0"]


# --- links, placeholders and templates ------------------------------------------------


def test_links_and_note_references_are_kept():
    src = make_epub([("C", ["See [the site](https://x.test) now.[^1]"])])
    key = f"{CH1}#1"
    assert readback(src)[key][0] == f"See the site now.{F}"
    link = Mark(7, 16, MarkKind.LINK, "https://x.test")
    blocks = {key: TranslatedBlock(f"{F}Siehe die Seite.", [link], None)}
    out = translate(src, blocks)
    assert readback(out)[key] == expected(blocks)[key]
    p = block_el(out, CH1)
    links = p.findall(f"{XHTML}a")
    assert len(links) == 2
    assert links[0].get("{http://www.idpf.org/2007/ops}type") == "noteref"
    assert links[0].get("href") == "notes.xhtml#n1" and links[0].text == "1"  # moved whole
    assert links[1].get("href") == "https://x.test" and links[1].text == "die Seite"


def test_reordered_placeholders_move_elements():
    src = make_epub([("C", ["One[^1] two[^2] three."])])
    key = f"{CH1}#1"
    blocks = {key: TranslatedBlock(f"Eins{F} zwei{F} drei.", [], [1, 0])}
    out = translate(src, blocks)
    hrefs = [a.get("href") for a in block_el(out, CH1).iter(f"{XHTML}a")]
    assert hrefs == ["notes.xhtml#n2", "notes.xhtml#n1"]
    assert readback(out)[key] == (f"Eins{F} zwei{F} drei.", [])


def test_mark_elements_copy_the_first_source_element_of_their_kind():
    src = with_body(
        make_epub([("C", ["x"])]),
        CH1,
        '<p id="p1" class="body">A <b class="strong" id="b1">bold</b> and <b class="other">more</b>'
        ' <span class="sc" style="font-variant: small-caps">Caps</span>'
        ' <i class="it" style="font-weight: bold">both</i> <a href="#x" class="l" id="l1">one</a>'
        ' <a href="#x" class="l2">two</a>.</p>',
    )
    key = f"{CH1}#0"
    text = "fett fett2 KAPS kursiv eins zwei"
    marks = [
        Mark(0, 4, MarkKind.BOLD),
        Mark(5, 10, MarkKind.BOLD),
        Mark(11, 15, MarkKind.SMALLCAPS),
        Mark(16, 22, MarkKind.ITALIC),
        Mark(23, 27, MarkKind.LINK, "#x"),
        Mark(28, 32, MarkKind.LINK, "#x"),
    ]
    out = translate(src, {key: TranslatedBlock(text, marks, None)})
    assert readback(out)[key] == (text, marks)
    p = block_el(out, CH1)
    assert p.get("id") == "p1" and p.get("class") == "body"
    kids = list(p)
    assert [(etree.QName(k).localname, k.get("class"), k.get("id")) for k in kids] == [
        ("b", "strong", "b1"),
        ("b", "strong", None),  # the first b again, without its id
        ("span", "sc", None),
        ("i", "it", None),  # its bold style declaration dropped
        ("a", "l", "l1"),  # links by link index
        ("a", "l2", None),
    ]
    assert kids[3].get("style") is None


def test_new_mark_kinds_get_plain_elements():
    src = make_epub([("C", ["plain"])])
    key = f"{CH1}#1"
    kinds = [k for k in MarkKind if k not in (MarkKind.LINK, MarkKind.SUBSCRIPT)]
    marks = [Mark(0, 1, k) for k in kinds] + [Mark(2, 3, MarkKind.SUBSCRIPT)]
    out = translate(src, {key: TranslatedBlock("x y z", marks, None)})
    assert readback(out)[key] == ("x y z", merge_marks(marks))
    p = block_el(out, CH1)
    made = [(etree.QName(el).localname, dict(el.attrib)) for el in p.iterdescendants()]
    assert made == [
        ("b", {}),
        ("i", {}),
        ("u", {}),
        ("span", {"style": "font-variant: small-caps"}),
        ("sup", {}),
        ("s", {}),
        ("sub", {}),
    ]


def test_ids_inside_a_block_survive():
    src = with_body(
        make_epub([("C", ["x"])]),
        CH1,
        '<h1><a id="top"></a>Chapter <a href="#n" id="ref">1</a></h1><p>Text<a id="end"/></p>',
    )
    blocks = {
        f"{CH1}#0": TranslatedBlock("Kapitel", [], None),  # the link is gone
        f"{CH1}#1": TranslatedBlock("Text", [], None),
    }
    out = translate(src, blocks)
    root = root_of(out, CH1)
    ids = [el.get("id") for el in root.iter() if el.get("id")]
    assert ids == ["top", "ref", "end"]
    assert readback(out) == expected(blocks)


def test_nested_marks_and_placeholders_read_back():
    src = make_epub([("C", ["A **b *bi* [l](https://x.test)** c.[^1]"])])
    key = f"{CH1}#1"
    text = f"x{F} y z w"
    marks = [
        Mark(0, 4, MarkKind.BOLD),
        Mark(1, 6, MarkKind.ITALIC),
        Mark(3, 8, MarkKind.LINK, "https://x.test"),
    ]
    out = translate(src, {key: TranslatedBlock(text, marks, None)})
    assert readback(out)[key] == (text, merge_marks(marks))


@pytest.mark.parametrize("seed", range(40))
def test_random_marks_and_placeholder_orders_read_back_exactly(seed):
    rnd = random.Random(seed)
    src = make_epub([("C", ["Plain **bold** *it* [l](https://x.test) end.[^1] and[^2]"])])
    key = f"{CH1}#1"
    words = [rnd.choice(["wort", "zwei", "drei", "ü", "ß"]) for _ in range(rnd.randint(2, 7))]
    chars = list(" ".join(words))
    for _ in range(2):
        chars.insert(rnd.randint(0, len(chars)), F)
    text = "".join(chars)
    kinds = list(MarkKind)
    marks = []
    for _ in range(rnd.randint(0, 6)):
        start = rnd.randrange(len(text))
        end = rnd.randint(start + 1, len(text))
        kind = rnd.choice(kinds)
        marks.append(Mark(start, end, kind, "https://x.test" if kind == MarkKind.LINK else None))
    marks = [_trim(m, text) for m in merge_marks(marks)]
    marks = merge_marks([m for m in marks if m.start < m.end])
    order = rnd.choice([None, [1, 0]])
    out = translate(src, {key: TranslatedBlock(text, marks, order)})
    assert readback(out)[key] == (text, marks)
    refs = [a.get("href") for a in block_el(out, CH1).iter(f"{XHTML}a") if "notes" in a.get("href")]
    assert refs == (
        ["notes.xhtml#n2", "notes.xhtml#n1"] if order else ["notes.xhtml#n1", "notes.xhtml#n2"]
    )


def _trim(m: Mark, text: str) -> Mark:
    start, end = m.start, m.end
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    return Mark(start, end, m.kind, m.href)


def test_style_declarations_of_other_marks_are_dropped_from_templates():
    src = with_body(
        make_epub([("C", ["x"])]),
        CH1,
        '<p xml:lang="en"><span style="font-weight:bold; font-style:italic; color:red">a</span>'
        ' <b style="font-style: italic">b</b> <img src="i.png"/></p>',
    )
    key = f"{CH1}#0"
    marks = [Mark(0, 1, MarkKind.BOLD), Mark(2, 3, MarkKind.ITALIC)]
    out = translate(src, {key: TranslatedBlock(f"x y {F}", marks, None)})
    assert readback(out)[key] == (f"x y {F}", marks)
    p = block_el(out, CH1)
    assert p.get(XML_LANG) == "de-DE"  # the block's own language
    spans = [(etree.QName(k).localname, k.get("style")) for k in p]
    assert spans == [
        ("span", "font-weight:bold; color:red"),
        ("span", "font-style:italic; color:red"),  # the b makes bold: no italic template
        ("img", None),
    ]


def test_marks_the_block_element_makes_are_not_repeated_inside():
    src = with_body(
        make_epub([("C", ["x"])]), CH1, '<p style="font-style: italic">Some <b>words</b>.</p>'
    )
    key = f"{CH1}#0"
    marks = [Mark(0, 9, MarkKind.ITALIC), Mark(0, 4, MarkKind.BOLD)]
    out = translate(src, {key: TranslatedBlock("Wort und.", marks, None)})
    assert readback(out)[key] == ("Wort und.", merge_marks(marks))
    assert [etree.QName(k).localname for k in block_el(out, CH1)] == ["b"]


@pytest.mark.parametrize(
    "options",
    [
        {"cluttered": True},
        {"split_chapter": True},
        {"front_matter": True},
        {"two_in_one_file": True},
        {"toc": "ncx", "cluttered": True},
        {"toc": "none"},
    ],
    ids=["cluttered", "split", "front", "one-file", "ncx-cluttered", "no-toc"],
)
def test_builder_variants_read_back(options):
    chapters = [
        ("One **big**", ["A *b* [c](https://x.test) d.[^1]", "Two", "Three **x *y***."]),
        ("Two", ["More [c](https://x.test) and [c](https://x.test).", "End."]),
    ]
    src = make_epub(chapters, **options)
    blocks = translate_all(src, lambda t: "DE " + t)
    labels = {c.source_ref: "DE " + c.title for c in read_epub(src).chapters}
    out = translate(src, blocks, toc_labels=labels)
    assert readback(out) == expected(blocks)
    assert titles(out) == ["DE " + t for t in titles(src)]  # from labels, or headings
    assert [len(c.blocks) for c in read_epub(out).chapters] == [
        len(c.blocks) for c in read_epub(src).chapters
    ]
    for name in set(_names(src)) - unchanged_entries(src, out):  # the rewritten entries
        assert b"ns0" not in entry(out, name), name


def _names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


# --- documents ------------------------------------------------------------------------


def test_html_fallback_document_is_translated():
    src = edit_entry(
        make_epub([("C", ["Hello."])]),
        CH1,
        lambda xml: xml.replace("<p>Hello.</p>", "<p>Hello<br>there &amp; <b>you</b>.</p>"),
    )
    key = f"{CH1}#1"
    assert readback(src)[key][0] == f"Hello{F}there & you."
    blocks = {key: TranslatedBlock(f"Hallo{F}du & <da>.", [Mark(11, 15, MarkKind.BOLD)], None)}
    out = translate(src, blocks)
    assert readback(out)[key] == expected(blocks)[key]
    assert b'lang="de-DE"' in entry(out, CH1)


def test_toc_page_in_the_spine_keeps_its_block_translation():
    src = make_epub([("One", ["a"])], toc="nav")
    src = edit_entry(
        src,
        "OEBPS/content.opf",
        lambda x: x.replace("<spine>", '<spine><itemref idref="nav.xhtml"/>'),
    )
    keys = [k for k in readback(src) if k.startswith("OEBPS/nav.xhtml")]
    assert len(keys) == 1
    blocks = {keys[0]: TranslatedBlock("Erstes", [], None)}
    out = translate(src, blocks, toc_labels={f"{CH1}#": "Eins"})
    assert readback(out)[keys[0]] == ("Erstes", [])  # the block translation wins


def test_proofreading_applies_on_the_translated_base():
    src = make_epub([("One", ["The *cat* sat."]), ("Two", ["Bye."])])
    blocks = translate_all(src, lambda t: "DE " + t)
    base = translate(src, blocks)
    key = f"{CH1}#1"
    changes = {key: [Change(0, 2, "XX")]}
    out = write_epub(base, changes)
    assert readback(out)[key][0] == apply_changes(blocks[key].text, changes[key])


# --- metadata and TOC -----------------------------------------------------------------


def _with_files(data: bytes, files: dict[str, str]) -> bytes:
    """``data`` with entries added or replaced (order kept, new ones last)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            content = files.get(info.filename)
            dst.writestr(info, content.encode() if content is not None else src.read(info))
        for name, content in files.items():
            if name not in src.namelist():
                dst.writestr(name, content.encode())
    return out.getvalue()


def _ncx(points: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="en">'
        f"<head/><docTitle><text>T</text></docTitle><navMap>{points}</navMap></ncx>"
    )


def _point(n: int, label: str, src: str, inner: str = "") -> str:
    return (
        f'<navPoint id="p{n}" playOrder="{n}"><navLabel><text>{label}</text></navLabel>'
        f'<content src="{src}"/>{inner}</navPoint>'
    )


def _ncx_labels(data: bytes) -> list[str]:
    root = root_of(data, "OEBPS/toc.ncx")
    return [t.text for t in root.iter("{http://www.daisy.org/z3986/2005/ncx/}text")][1:]


def test_title_none_or_blank_keeps_dc_title():
    src = make_epub([("C", ["Hello."])], title="Original")
    for title in (None, "", "  \t"):
        out = translate(src, {}, title=title)
        assert read_epub(out).title == "Original"
        assert "<dc:title>Original</dc:title>" in entry(out, "OEBPS/content.opf").decode()


def test_missing_dc_language_is_created():
    src = edit_entry(
        make_epub([("C", ["Hello."])]),
        "OEBPS/content.opf",
        lambda opf: opf.replace("<dc:language>en</dc:language>", ""),
    )
    assert read_epub(src).language is None
    out = translate(src, {})
    assert read_epub(out).language == "de-DE"
    assert "<dc:language>de-DE</dc:language>" in entry(out, "OEBPS/content.opf").decode()


def test_blank_toc_label_keeps_the_label():
    src = make_epub([("One", ["a"]), ("Two", ["b"])])
    out = translate(src, {}, toc_labels={f"{CH1}#": " ", f"{CH2}#": "Zwei"})
    assert titles(out) == ["One", "Zwei"]


def test_nav_and_ncx_are_both_relabelled():
    src = make_epub([("One", ["a"]), ("Two", ["b"])], toc="nav")
    src = _with_files(
        src,
        {"OEBPS/toc.ncx": _ncx(_point(1, "One", "ch1.xhtml") + _point(2, "Two", "ch2.xhtml"))},
    )
    src = edit_entry(
        src,
        "OEBPS/content.opf",
        lambda opf: opf.replace(
            "</manifest><spine>",
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest><spine toc="ncx">',
        ),
    )
    out = translate(src, {}, toc_labels={f"{CH1}#": "Eins", f"{CH2}#": "Zwei"})
    assert titles(out) == ["Eins", "Zwei"]  # from the nav
    assert _ncx_labels(out) == ["Eins", "Zwei"]
    assert root_of(out, "OEBPS/toc.ncx").get(XML_LANG) == "de-DE"


def test_ncx_is_relabelled_at_nested_depth():
    src = make_epub([("One", ["a"]), ("Two", ["b"])], toc="ncx")
    nested = _point(
        1, "One", "ch1.xhtml", _point(2, "Two", "ch2.xhtml", _point(3, "A", "ch2.xhtml#x"))
    )
    src = _with_files(src, {"OEBPS/toc.ncx": _ncx(nested)})
    labels = {f"{CH2}#": "Zwei", f"{CH2}#x": "Anker"}
    out = translate(src, {}, toc_labels=labels)
    assert _ncx_labels(out) == ["One", "Zwei", "Anker"]


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blocks",
    [
        {f"{CH1}#9": TranslatedBlock("x", [], None)},  # no such block
        {f"{CH1}#1": TranslatedBlock("kein Platzhalter", [], None)},  # placeholder lost
        {f"{CH1}#1": TranslatedBlock(f"a{F}", [], [1])},  # not a permutation
        {f"{CH1}#1": TranslatedBlock(f"a{F}\x01", [], None)},  # not XML
        {f"{CH1}#1": TranslatedBlock(f"a{F}", [Mark(0, 1, MarkKind.LINK, "https://o.test")], None)},
        {f"{CH1}#1": TranslatedBlock(f"a{F}", [Mark(0, 9, MarkKind.BOLD)], None)},  # out of range
        {f"{CH1}#1": TranslatedBlock(f"a  b{F}", [], None)},  # would collapse
        {f"{CH1}#1": TranslatedBlock(f" a{F}", [], None)},  # leading space
        {f"{CH1}#1": TranslatedBlock(f"a\n{F}", [], None)},  # newline
        {f"{CH1}#1": TranslatedBlock(f"{F}", [], None)},  # no text left
    ],
    ids=["key", "count", "order", "xml", "href", "range", "spaces", "lead", "newline", "empty"],
)
def test_bad_translations_are_refused(blocks):
    src = make_epub([("C", ["Hello [l](https://x.test).[^1]"])])
    with pytest.raises(TranslatedEpubError) as exc:
        translate(src, blocks)
    assert list(exc.value.problems) == list(blocks)


def test_every_failing_block_is_reported_once():
    src = make_epub([("C", ["Hello [l](https://x.test) and [m](https://y.test).[^1]"])])
    links = [
        Mark(0, 3, MarkKind.LINK, "https://x.test"),
        Mark(2, 5, MarkKind.LINK, "https://y.test"),
    ]
    blocks = {
        f"{CH1}#9": TranslatedBlock("x", [], None),
        f"{CH1}#0": TranslatedBlock("Kopf", [], None),  # fine
        f"{CH1}#1": TranslatedBlock(f"abcde{F}", links, None),
    }
    with pytest.raises(TranslatedEpubError) as exc:
        translate(src, blocks)
    assert isinstance(exc.value, InvalidChangeError)
    assert exc.value.problems == {
        f"{CH1}#9": "no block with this key",
        f"{CH1}#1": "links to 'https://x.test' and 'https://y.test' overlap",
    }
    assert f"{CH1}#9: no block with this key" in str(exc.value)


@pytest.mark.parametrize(
    "kw",
    [{"title": "a\x00b"}, {"toc_labels": {f"{CH1}#": "a\x01"}}, {"language": "de\x02"}],
    ids=["title", "label", "language"],
)
def test_bad_metadata_is_refused(kw):
    with pytest.raises(InvalidChangeError):
        translate(make_epub([("C", ["Hello."])]), {}, **kw)


# --- fitting text to what the reader gives back ---------------------------------------


def test_fit_epub_text_collapses_whitespace_and_shifts_marks():
    text = "  Ein\n\t Satz  hier. "
    start = text.index("Satz")
    marks = [
        Mark(start, start + 4, MarkKind.ITALIC),
        Mark(0, len(text), MarkKind.BOLD),
        Mark(start + 4, start + 5, MarkKind.UNDERLINE),  # one of two collapsed spaces
        Mark(start + 5, start + 6, MarkKind.STRIKE),  # the other: nothing left of it
    ]
    fitted, shifted = fit_epub_text(text, marks)
    assert fitted == "Ein Satz hier."
    assert shifted == [
        Mark(4, 8, MarkKind.ITALIC),
        Mark(0, 14, MarkKind.BOLD),
        Mark(8, 9, MarkKind.UNDERLINE),
    ]
    assert fit_epub_text(fitted, shifted) == (fitted, shifted)
    src = make_epub([("One", ["a"])])
    out = translate(src, {f"{CH1}#1": TranslatedBlock(fitted, shifted[:1], None)})
    assert readback(out)[f"{CH1}#1"] == (fitted, shifted[:1])  # written, not refused


def test_fit_epub_text_keeps_placeholders_and_other_spaces():
    text = f"{F}  Satz\u00a0mit {F} "
    fitted, marks = fit_epub_text(text, [Mark(0, 1, MarkKind.SUPERSCRIPT)])
    assert fitted == f"{F} Satz\u00a0mit {F}"
    assert marks == [Mark(0, 1, MarkKind.SUPERSCRIPT)]
