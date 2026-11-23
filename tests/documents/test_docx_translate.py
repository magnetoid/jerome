import io
import random
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime

import docx
import pytest
from lxml import etree

from jerome.documents.changes import Change, apply_changes
from jerome.documents.docx_reader import read_docx
from jerome.documents.docx_translate import TranslatedDocxError, write_translated_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.documents.docx_xml import NS, merge_marks, qn
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind
from jerome.documents.translated import TranslatedBlock
from jerome.errors import InvalidChangeError
from jerome.translate.tags import decode
from tests.fixtures.docx_builders import (
    all_blocks,
    make_docx,
    make_fragmented_docx,
    package_docx,
    para_xml,
    part_xml,
    revision_ids,
)

NOW = datetime(2026, 11, 23, tzinfo=UTC)
DOC = "word/document.xml"
NOTES = "word/footnotes.xml"
P0 = f"{DOC}#0"
F = PLACEHOLDER


def translate_all(src: bytes, translate: Callable[[str], str]) -> dict[str, TranslatedBlock]:
    """A TranslatedBlock per block of ``src``. ``translate`` only adds a prefix to the text,
    so the marks shift by its length and the placeholders keep their order."""
    out = {}
    for b in all_blocks(src):
        text = translate(b.text)
        assert text.endswith(b.text)
        shift = len(text) - len(b.text)
        marks = [Mark(m.start + shift, m.end + shift, m.kind, m.href) for m in b.marks]
        out[b.key] = TranslatedBlock(text, marks, None)
    return out


def readback(data: bytes) -> dict[str, tuple[str, list[Mark]]]:
    return {b.key: (b.text, b.marks) for b in all_blocks(data)}


def expected(blocks: dict[str, TranslatedBlock]) -> dict[str, tuple[str, list[Mark]]]:
    return {k: (v.text, v.marks) for k, v in blocks.items()}


def body_paragraph(data: bytes, n: int = 0) -> etree._Element:
    return etree.fromstring(para_xml(data, n))


def local_names(p: etree._Element) -> list[str]:
    return [etree.QName(el).localname for el in p.iter() if isinstance(el.tag, str)]


# --- from the brief -------------------------------------------------------------------


def test_translated_docx_reads_back_as_the_translation():
    src = make_docx(["# Title", "Hello **bold** and *it* text.", "Plain."])
    blocks = translate_all(src, lambda t: "DE " + t)
    out = write_translated_docx(src, blocks, language="de-DE", title="Titel")
    assert readback(out) == expected(blocks)
    assert 'w:val="de-DE"' in part_xml(out, "word/styles.xml")
    assert "updateFields" in part_xml(out, "word/settings.xml")
    assert "Titel" in part_xml(out, "docProps/core.xml")
    assert read_docx(out).language == "de-DE" and read_docx(out).title == "Titel"


def test_translated_docx_keeps_footnote_reference_and_link():
    src = make_docx(["See [the site](https://x.test) now.[^1]"], footnotes={1: "A note."})
    assert readback(src)[P0][0] == f"See the site now.{F}"
    assert readback(src)[f"{NOTES}#0"][0] == f"{F} A note."
    # the footnote placeholder moves to the front and the link is kept
    body = f"{F}Siehe die Seite jetzt."
    blocks = {
        P0: TranslatedBlock(body, [Mark(7, 16, MarkKind.LINK, "https://x.test")], None),
        f"{NOTES}#0": TranslatedBlock(f"{F} Eine Anmerkung.", [], None),
    }
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    xml = part_xml(out, DOC)
    assert 'w:footnoteReference w:id="1"' in xml and "hyperlink" in xml
    assert xml.count("footnoteReference") == 1
    assert xml.index("footnoteReference") < xml.index("Siehe")
    assert 'r:id="rId3"' in xml  # the source link's relationship
    assert readback(out) == expected(blocks)
    notes = part_xml(out, NOTES)
    assert notes.count("<w:footnoteRef/>") == 1 and "Eine Anmerkung." in notes
    assert "Titel" not in part_xml(out, "docProps/core.xml")


def test_reordered_placeholders_move_elements():
    src = make_docx(["One\ttwo[^1] three."], footnotes={1: "n"})
    assert readback(src)[P0][0] == f"One{F}two{F} three."
    # first U+FFFC is the source's second placeholder (the note), the second its tab
    blocks = {P0: TranslatedBlock(f"Eins{F} zwei{F}drei.", [], [1, 0])}
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    names = local_names(body_paragraph(out))
    assert names.count("footnoteReference") == 1 and names.count("tab") == 1  # moved, not copied
    assert names.index("footnoteReference") < names.index("tab")
    assert readback(out)[P0] == (f"Eins{F} zwei{F}drei.", [])


def test_untouched_paragraphs_are_byte_identical():
    src = make_docx(["First **para**.", "Second para.", "Third [l](https://x.test)."], {1: "n"})
    blocks = {f"{DOC}#1": TranslatedBlock("Zweiter Absatz.", [], None)}
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    assert para_xml(out, 0) == para_xml(src, 0)
    assert para_xml(out, 2) == para_xml(src, 2)
    assert part_xml(out, NOTES) == part_xml(src, NOTES)  # no note was translated
    assert readback(out)[f"{DOC}#1"] == ("Zweiter Absatz.", [])


def test_tracked_export_on_translated_base_opens_and_has_revisions():
    src = make_docx(["The cat sat.", "A *dog* ran.[^1]"], footnotes={1: "A note."})
    blocks = translate_all(src, lambda t: "DE " + t)
    base = write_translated_docx(src, blocks, language="de-DE", title="Titel")
    changes = {P0: [Change(0, 2, "XX", comment="a comment")], f"{DOC}#1": [Change(3, 4, "B")]}

    out = write_docx(base, changes, DocxWriteOptions("tracked", NOW))
    ids = revision_ids(out)
    assert len(ids) == 4 and len(ids) == len(set(ids))  # a w:del and a w:ins per change
    assert len(docx.Document(io.BytesIO(out)).paragraphs) == 2

    clean = readback(write_docx(base, changes, DocxWriteOptions("clean", NOW)))
    for key, cs in changes.items():
        assert clean[key][0] == apply_changes(blocks[key].text, cs)


# --- formatting -----------------------------------------------------------------------


def test_every_block_kind_reads_back():
    src = make_docx(
        ["# One", "## Sub", "Plain **bold *both*** [l](https://x.test)[^1].", "|a|*b*|", "End."],
        footnotes={1: "A [note](https://n.test) with **bold**."},
    )
    blocks = translate_all(src, lambda t: "DE " + t)
    assert len(blocks) == 7
    assert readback(write_translated_docx(src, blocks, language="de-DE", title=None)) == expected(
        blocks
    )


def test_span_copies_the_run_properties_of_the_source_run_with_its_marks():
    body = (
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Plain"/></w:rPr><w:t xml:space="preserve">a </w:t>'
        '</w:r><w:r><w:rPr><w:rFonts w:ascii="Bold"/><w:b/><w:color w:val="FF0000"/></w:rPr>'
        "<w:t>b</w:t></w:r></w:p>"
    )
    src = package_docx(body)
    marks = [Mark(0, 4, MarkKind.BOLD), Mark(6, 8, MarkKind.ITALIC)]
    blocks = {P0: TranslatedBlock("fett und kursiv", marks, None)}
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    runs = body_paragraph(out).findall(qn("w:r"))
    fonts = [r.find("w:rPr/w:rFonts", NS) for r in runs]
    assert [f.get(qn("w:ascii")) for f in fonts] == ["Bold", "Plain", "Plain", "Plain"]
    assert runs[0].find(f"{qn('w:rPr')}/{qn('w:color')}") is not None  # copied whole
    assert runs[2].find(f"{qn('w:rPr')}/{qn('w:i')}") is not None  # no italic source: toggled
    assert readback(out)[P0] == ("fett und kursiv", marks)


def test_link_span_without_an_exact_match_keeps_the_link_style():
    src = make_docx(["See **[the site](https://x.test)** now."])
    marks = [Mark(6, 15, MarkKind.LINK, "https://x.test")]  # the link, no longer bold
    blocks = {P0: TranslatedBlock("Siehe die Seite jetzt.", marks, None)}
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    link = body_paragraph(out).find(qn("w:hyperlink"))
    rpr = link.find(f"{qn('w:r')}/{qn('w:rPr')}")
    assert rpr.find(qn("w:rStyle")).get(qn("w:val")) == "Hyperlink"
    assert rpr.find(qn("w:b")) is None
    assert readback(out)[P0] == ("Siehe die Seite jetzt.", marks)


def test_toggles_are_written_in_schema_order():
    body = (
        '<w:p><w:r><w:rPr><w:rStyle w:val="X"/><w:color w:val="00FF00"/><w:sz w:val="20"/>'
        '<w:lang w:val="en-US"/></w:rPr><w:t>a</w:t></w:r></w:p>'
    )
    marks = [
        Mark(0, 1, kind) for kind in MarkKind if kind not in (MarkKind.LINK, MarkKind.SUBSCRIPT)
    ]
    blocks = {P0: TranslatedBlock("x", marks, None)}
    out = write_translated_docx(package_docx(body), blocks, language="de-DE", title=None)
    rpr = body_paragraph(out).find(f"{qn('w:r')}/{qn('w:rPr')}")
    assert [etree.QName(c).localname for c in rpr] == [
        "rStyle", "b", "i", "smallCaps", "strike", "color", "sz", "u", "vertAlign", "lang"
    ]  # fmt: skip
    assert readback(out)[P0] == ("x", merge_marks(marks))


@pytest.mark.parametrize("seed", range(40))
def test_random_marks_and_placeholder_orders_read_back_exactly(seed):
    rnd = random.Random(seed)
    src = make_docx(["Plain **bold** *it*\t[l](https://x.test) end.[^1]"], footnotes={1: "n"})
    words = [rnd.choice(["wort", "zwei", "drei", "ü", "ß"]) for _ in range(rnd.randint(2, 7))]
    chars = list(" ".join(words))
    for _ in range(2):
        chars.insert(rnd.randint(0, len(chars)), F)
    text = "".join(chars)
    kinds = [k for k in MarkKind if k not in (MarkKind.SUPERSCRIPT, MarkKind.SUBSCRIPT)]
    kinds.append(rnd.choice([MarkKind.SUPERSCRIPT, MarkKind.SUBSCRIPT]))  # not both at once
    marks = []
    for _ in range(rnd.randint(0, 6)):
        start = rnd.randrange(len(text))
        end = rnd.randint(start + 1, len(text))
        kind = rnd.choice(kinds)
        marks.append(Mark(start, end, kind, "https://x.test" if kind == MarkKind.LINK else None))
    marks = merge_marks(marks)
    order = rnd.choice([None, [1, 0]])
    out = write_translated_docx(
        src, {P0: TranslatedBlock(text, marks, order)}, language="de-DE", title=None
    )
    assert readback(out)[P0] == (text, marks)
    names = local_names(body_paragraph(out))
    assert names.count("footnoteReference") == 1 and names.count("tab") == 1


def _decoded(src: bytes, replies: dict[str, str]) -> dict[str, TranslatedBlock]:
    """TranslatedBlocks the way the translate pass makes them: decode with the source text."""
    source = {b.key: b for b in all_blocks(src)}
    out = {}
    for key, reply in replies.items():
        b = source[key]
        d = decode(reply, b.marks, b.text.count(F), source_text=b.text)
        out[key] = TranslatedBlock(d.text, d.marks, d.placeholders)
    return out


def test_decoded_placeholders_inside_bold_read_back_exactly():
    body = (  # a tab inside bold, and a simple field between two bold runs
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Figure </w:t></w:r>'
        '<w:fldSimple w:instr=" SEQ Figure "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve"> shows</w:t><w:tab/>'
        '<w:t>this</w:t></w:r><w:r><w:t xml:space="preserve"> today.</w:t></w:r></w:p>'
    )
    src = package_docx(body)
    assert all_blocks(src)[0].text == f"Figure {F} shows{F}this today."
    # the model merges the bold over both tokens
    blocks = _decoded(src, {P0: "<b>Abbildung ⟦1⟧ zeigt⟦2⟧dies</b> heute."})
    bold = [(m.start, m.end) for m in blocks[P0].marks]
    assert bold == [(0, 10), (11, 22)]  # the field is not bold in the source, the tab is
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    assert readback(out) == expected(blocks)


def test_decoded_footnote_reference_keeps_superscript():
    body = (
        '<w:p><w:r><w:t>Text</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/>'
        '</w:rPr><w:footnoteReference w:id="1"/></w:r></w:p>'
    )
    notes = (
        '<w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve">'
        " A note.</w:t></w:r></w:p></w:footnote>"
    )
    src = package_docx(body, footnotes=notes)
    assert all_blocks(src)[0].marks == [Mark(4, 5, MarkKind.SUPERSCRIPT)]
    # the model drops the <sup> and moves the token to the front
    blocks = _decoded(src, {P0: "⟦1⟧Der Text", f"{NOTES}#0": "⟦1⟧ Eine Anmerkung."})
    assert blocks[P0].text == f"Der Text{F}"
    assert blocks[P0].marks == [Mark(8, 9, MarkKind.SUPERSCRIPT)]
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    assert readback(out) == expected(blocks)
    ref = body_paragraph(out).find(f".//{qn('w:footnoteReference')}").getparent()
    assert ref.find(f"{qn('w:rPr')}/{qn('w:vertAlign')}").get(qn("w:val")) == "superscript"


# --- structure ------------------------------------------------------------------------


def test_complex_field_is_moved_whole():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">See page </w:t><w:fldChar w:fldCharType="begin"/>'
        '</w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>3</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/><w:t xml:space="preserve"> now.</w:t></w:r></w:p>'
    )
    src = package_docx(body)
    assert readback(src)[P0][0] == f"See page {F} now."
    blocks = {P0: TranslatedBlock(f"{F} ist die Seite.", [], None)}
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    p = body_paragraph(out)
    assert local_names(p).count("fldChar") == 3 and local_names(p).count("instrText") == 1
    assert "".join(p.itertext()).startswith(" PAGE 3")  # instruction and cached result, first
    assert readback(out)[P0] == (f"{F} ist die Seite.", [])


def _field_across_paragraphs() -> bytes:
    return package_docx(
        '<w:p><w:r><w:t xml:space="preserve">Intro </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> TOC </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>entry</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>more</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        "<w:r><w:t>After.</w:t></w:r></w:p>"
    )


def test_fields_across_paragraphs_stay_at_the_paragraph_edges():
    src = _field_across_paragraphs()
    assert readback(src) == {P0: (f"Intro {F}", []), f"{DOC}#1": (f"{F}After.", [])}
    blocks = {
        P0: TranslatedBlock(f"Einleitung {F}", [], None),
        f"{DOC}#1": TranslatedBlock(f"{F}Danach.", [], None),
    }
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    assert readback(out) == expected(blocks)


@pytest.mark.parametrize(
    "key, text", [(P0, f"{F} Einleitung"), (f"{DOC}#1", f"Danach.{F}")], ids=["open", "continued"]
)
def test_text_cannot_move_into_a_field_across_paragraphs(key, text):
    # unreachable from decode(source_text=...), which keeps edge placeholders at their edge
    with pytest.raises(TranslatedDocxError) as exc:
        write_translated_docx(
            _field_across_paragraphs(),
            {key: TranslatedBlock(text, [], None)},
            language="de-DE",
            title=None,
        )
    assert list(exc.value.problems) == [key] and "field" in exc.value.problems[key]


def test_decoded_edge_field_token_is_written():
    src = _field_across_paragraphs()
    source = {b.key: b for b in all_blocks(src)}
    blocks = {}
    for key, reply in [(P0, "⟦1⟧ Einleitung"), (f"{DOC}#1", "Danach.⟦1⟧")]:  # tokens moved
        d = decode(reply, source[key].marks, 1, source_text=source[key].text)
        blocks[key] = TranslatedBlock(d.text, d.marks, d.placeholders)
    assert blocks[P0].text.endswith(F) and blocks[f"{DOC}#1"].text.startswith(F)
    out = write_translated_docx(src, blocks, language="de-DE", title=None)
    assert readback(out) == expected(blocks)


def test_bookmarks_and_paragraph_properties_stay():
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr>'
        '<w:bookmarkStart w:id="7" w:name="_Toc1"/><w:proofErr w:type="spellStart"/>'
        '<w:r><w:t>Chaptr</w:t></w:r><w:proofErr w:type="spellEnd"/>'
        '<w:r><w:t xml:space="preserve"> one</w:t></w:r><w:bookmarkEnd w:id="7"/></w:p>'
    )
    src = package_docx(body)
    out = write_translated_docx(
        src, {P0: TranslatedBlock("Kapitel eins", [], None)}, language="de-DE", title=None
    )
    p = body_paragraph(out)
    names = [etree.QName(c).localname for c in p]
    assert names[:2] == ["pPr", "bookmarkStart"] and names[-1] == "bookmarkEnd"
    assert etree.tostring(p.find(qn("w:pPr"))) == etree.tostring(body_paragraph(src)[0])
    assert readback(out)[P0] == ("Kapitel eins", [])


def test_fragmented_source_leaves_no_empty_runs():
    src = make_fragmented_docx()
    marks = [Mark(4, 9, MarkKind.BOLD)]
    out = write_translated_docx(
        src, {P0: TranslatedBlock("Der braune Fuchs.", marks, None)}, language="de-DE", title=None
    )
    p = body_paragraph(out)
    assert [r.findtext(qn("w:t")) for r in p.iter(qn("w:r"))] == ["Der ", "braun", "e Fuchs."]
    assert p.find(qn("w:bookmarkStart")) is not None  # _GoBack is kept
    assert readback(out)[P0] == ("Der braune Fuchs.", marks)


def test_containers_left_without_text_are_unwrapped():
    body = (
        '<w:p><w:smartTag w:uri="u" w:element="city"><w:smartTagPr/><w:r><w:t>Paris</w:t></w:r>'
        '</w:smartTag><w:r><w:t xml:space="preserve"> and </w:t></w:r><w:sdt><w:sdtPr/>'
        "<w:sdtContent><w:r><w:t>Rome</w:t></w:r><w:r><w:tab/></w:r></w:sdtContent></w:sdt></w:p>"
    )
    src = package_docx(body)
    assert readback(src)[P0][0] == f"Paris and Rome{F}"
    out = write_translated_docx(
        src, {P0: TranslatedBlock(f"Paris und Rom{F}", [], None)}, language="de-DE", title=None
    )
    names = local_names(body_paragraph(out))
    assert "smartTag" not in names and "sdt" not in names  # the tab moved out too
    assert names.count("tab") == 1
    assert readback(out)[P0] == (f"Paris und Rom{F}", [])


def test_language_replaces_lang_on_rebuilt_runs_only():
    body = (
        '<w:p><w:r><w:rPr><w:lang w:val="en-GB" w:eastAsia="ja-JP"/></w:rPr><w:t>Colour</w:t>'
        '</w:r></w:p><w:p><w:r><w:rPr><w:lang w:val="en-GB"/></w:rPr><w:t>Kept</w:t></w:r></w:p>'
    )
    src = package_docx(body, language="en-GB")
    out = write_translated_docx(
        src, {P0: TranslatedBlock("Farbe", [], None)}, language="de-DE", title=None
    )
    lang = body_paragraph(out).find(f"{qn('w:r')}/{qn('w:rPr')}/{qn('w:lang')}")
    assert lang.get(qn("w:val")) == "de-DE" and lang.get(qn("w:eastAsia")) == "ja-JP"
    assert para_xml(out, 1) == para_xml(src, 1)
    assert read_docx(out).language == "de-DE"


def test_settings_part_is_created_once_then_updated():
    src = make_docx(["Hello."])
    once = write_translated_docx(src, {}, language="de-DE", title=None)
    assert '<w:updateFields w:val="true"/>' in part_xml(once, "word/settings.xml")
    assert "settings.xml" in part_xml(once, "word/_rels/document.xml.rels")
    assert 'PartName="/word/settings.xml"' in part_xml(once, "[Content_Types].xml")
    twice = write_translated_docx(once, {}, language="de-AT", title=None)
    assert part_xml(twice, "word/settings.xml").count("updateFields") == 1
    assert part_xml(twice, "word/_rels/document.xml.rels").count("settings.xml") == 1
    assert read_docx(twice).language == "de-AT"
    assert readback(twice) == readback(src)


def test_update_fields_goes_before_later_settings():
    src = make_docx(["Hello."])
    once = write_translated_docx(src, {}, language="de-DE", title=None)
    settings = part_xml(once, "word/settings.xml").replace(
        "<w:updateFields", '<w:zoom w:percent="100"/><w:compat/><w:updateFields'
    )
    # drop updateFields, keep zoom (before it) and compat (after it)
    settings = settings.replace('<w:updateFields w:val="true"/>', "")
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(once)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            zout.writestr(info, settings if info.filename == "word/settings.xml" else data)
    out = write_translated_docx(buf.getvalue(), {}, language="de-DE", title=None)
    root = etree.fromstring(part_xml(out, "word/settings.xml").encode())
    assert [etree.QName(c).localname for c in root] == ["zoom", "updateFields", "compat"]


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blocks",
    [
        {f"{DOC}#9": TranslatedBlock("x", [], None)},  # no such block
        {P0: TranslatedBlock("kein Platzhalter", [], None)},  # placeholder lost
        {P0: TranslatedBlock(f"a{F}", [], [1])},  # not a permutation
        {P0: TranslatedBlock(f" {F} ", [], None)},  # no text
        {P0: TranslatedBlock(f"a{F}\x01", [], None)},  # not XML
        {P0: TranslatedBlock(f"a{F}", [Mark(0, 1, MarkKind.LINK, "https://other.test")], None)},
        {P0: TranslatedBlock(f"a{F}", [Mark(0, 9, MarkKind.BOLD)], None)},  # out of range
    ],
    ids=["key", "count", "order", "blank", "xml", "href", "range"],
)
def test_bad_translations_are_refused(blocks):
    src = make_docx(["Hello\tworld [l](https://x.test)."])
    with pytest.raises(TranslatedDocxError) as exc:
        write_translated_docx(src, blocks, language="de-DE", title=None)
    assert list(exc.value.problems) == list(blocks)
    assert isinstance(exc.value, InvalidChangeError)


def test_every_failing_key_is_reported_and_nothing_written():
    src = make_docx(["One\ttab.", "Two.", "Three."])
    blocks = {
        f"{DOC}#2": TranslatedBlock("   ", [], None),
        f"{DOC}#1": TranslatedBlock("Zwei.", [], None),  # fine
        f"{DOC}#7": TranslatedBlock("x", [], None),
        P0: TranslatedBlock("Eins ohne Tab.", [], None),
    }
    with pytest.raises(TranslatedDocxError) as exc:
        write_translated_docx(src, blocks, language="de-DE", title=None)
    problems = exc.value.problems
    assert list(problems) == [f"{DOC}#2", f"{DOC}#7", P0]  # the caller's keys, in its order
    assert problems[f"{DOC}#2"] == "translation has no text"
    assert problems[f"{DOC}#7"] == "no block with this key"
    assert "placeholders" in problems[P0]
    assert all(key in str(exc.value) for key in problems)


def test_bad_title_is_refused():
    with pytest.raises(InvalidChangeError):
        write_translated_docx(make_docx(["Hello."]), {}, language="de-DE", title="a\x00b")
