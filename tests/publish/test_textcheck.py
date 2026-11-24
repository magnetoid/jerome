import pytest

from jerome.documents.docx_reader import read_docx
from jerome.publish.from_docx import docx_to_pub
from jerome.publish.model import PublishTextError
from jerome.publish.textcheck import check_text
from tests.fixtures.docx_builders import make_docx

LINES = [
    "[TOC: One\t1]",
    "For my mother.",
    "# One",
    "The *quick* brown fox[^1] jumps.",
    "- a list item",
    "1. a numbered item",
    "|cell one|cell two|",
    "![Picture](png)",
    "# Two",
    "Over the lazy dog, again and again and again.",
]
NOTES = {1: "A *note* here."}


def test_textcheck_passes_on_real_conversion():
    docx = make_docx(LINES, footnotes=NOTES)
    check_text(docx_to_pub(docx, "en-US"), read_docx(docx))


def test_textcheck_fails_on_doctored_html():
    docx = make_docx(LINES, footnotes=NOTES)
    pub = docx_to_pub(docx, "en-US")
    pub.chapters[2].html = pub.chapters[2].html.replace("lazy", "sleepy")
    with pytest.raises(PublishTextError) as err:
        check_text(pub, read_docx(docx))
    message = str(err.value)
    assert message.startswith("The finished file's text differs from the reviewed text near 'Two'")
    assert "sleepy" in message and "lazy" in message


def test_textcheck_fails_on_doctored_note():
    docx = make_docx(LINES, footnotes=NOTES)
    pub = docx_to_pub(docx, "en-US")
    pub.chapters[1].html = pub.chapters[1].html.replace("here", "there")
    with pytest.raises(PublishTextError):
        check_text(pub, read_docx(docx))


def test_textcheck_fails_on_dropped_paragraph():
    docx = make_docx(LINES, footnotes=NOTES)
    pub = docx_to_pub(docx, "en-US")
    pub.chapters[0].html = ""
    with pytest.raises(PublishTextError):
        check_text(pub, read_docx(docx))


def test_textcheck_ignores_whitespace_soft_hyphens_and_numbers():
    docx = make_docx(LINES, footnotes=NOTES)
    pub = docx_to_pub(docx, "en-US")
    two = pub.chapters[2]
    two.html = two.html.replace("lazy dog,", "la­zy\n   dog,")
    pub.chapters[1].html = pub.chapters[1].html.replace("<ol>", "<ol start='7'>")
    check_text(pub, read_docx(docx))


def test_textcheck_matches_notes_by_identity_not_by_order():
    # the reference to note 2 comes first; footnotes.xml keeps note 1 first
    docx = make_docx(
        ["# One", "First[^2] and second[^1] words."],
        footnotes={1: "Note number one.", 2: "Note number two."},
    )
    pub = docx_to_pub(docx, "en-US")
    assert pub.note_keys == ["footnote-2", "footnote-1"]
    check_text(pub, read_docx(docx))


def test_textcheck_reads_footnotes_before_endnotes():
    # read_docx gives footnotes.xml, then endnotes.xml; the book shows the endnote first
    from jerome.documents.model import BlockKind, ParsedBlock, ParsedBook, ParsedChapter
    from jerome.publish.model import NOTE_CLASS, PubBook, PubChapter

    parsed = ParsedBook("docx", None, None, [ParsedChapter("One", True, "d#0", [
        ParsedBlock("word/document.xml#0", BlockKind.PARAGRAPH, "A b."),
        ParsedBlock("word/footnotes.xml#0", BlockKind.FOOTNOTE, "Foot."),
        ParsedBlock("word/endnotes.xml#0", BlockKind.FOOTNOTE, "End."),
    ])])  # fmt: skip
    note = f'<span class="{NOTE_CLASS}">{{}}</span>'
    html = f"<p>A{note.format('End.')} b{note.format('Foot.')}.</p>"
    pub = PubBook("en-US", [PubChapter("One", html)], note_keys=["endnote-1", "footnote-1"])
    check_text(pub, parsed)
