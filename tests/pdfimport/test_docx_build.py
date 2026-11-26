import io
import re
import zipfile

from lxml import etree
from PIL import Image

from jerome.documents.docx_reader import read_docx
from jerome.documents.model import BlockKind, MarkKind
from jerome.pdfimport.docx_build import build_docx
from jerome.pdfimport.model import PageBlock, Run
from jerome.publish.from_docx import docx_to_pub
from tests.fixtures.images import make_png

TRICKY = 'Fish & chips <b> "quoted" it\'s  '


def blocks(*extra):
    return [
        PageBlock("heading", [Run("Chapter One")], level=1),
        PageBlock(
            "paragraph",
            [
                Run("The "),
                Run("ship", italic=True),
                Run(" sailed"),
                Run("", note=1),
                Run(" and "),
                Run("sank", bold=True),
                Run("."),
            ],
        ),
        PageBlock("heading", [Run("A Section")], level=2),
        PageBlock("paragraph", [Run(TRICKY)]),
        PageBlock("footnote", [Run("It was "), Run("old", italic=True), Run(".")], note=1),
        *extra,
    ]


def test_round_trip_through_read_docx():
    book = read_docx(build_docx(blocks(), title="A & B <Book>", language="de-DE"))
    assert book.title == "A & B <Book>"
    assert book.language == "de-DE"
    [chapter] = book.chapters
    assert chapter.title == "Chapter One"
    by_text = {b.text: b for b in chapter.blocks}
    ship = by_text["The ship sailed\ufffc and sank."]  # the note reference is a marker char
    marks = {(ship.text[m.start : m.end], m.kind) for m in ship.marks}
    assert marks == {("ship", MarkKind.ITALIC), ("sank", MarkKind.BOLD)}
    assert by_text["A Section"].kind == BlockKind.HEADING
    assert by_text[TRICKY].kind == BlockKind.PARAGRAPH  # & < > quotes and edge spaces survive
    [note] = [b for b in chapter.blocks if b.kind == BlockKind.FOOTNOTE]
    assert note.text.endswith("It was old.")


def test_styles_and_metadata_parts():
    data = build_docx(
        blocks(PageBlock("heading", [Run("Deep")], level=5)), title="T", language="en-US"
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        styles = zf.read("word/styles.xml").decode()
        core = zf.read("docProps/core.xml").decode()
        doc = zf.read("word/document.xml").decode()
        types = zf.read("[Content_Types].xml").decode()
        etree.fromstring(zf.read("word/footnotes.xml"))
    assert 'w:val="en-US"' in styles and "Heading3" in styles
    assert "<dc:language>en-US</dc:language>" in core and "<dc:title>T</dc:title>" in core
    assert 'w:val="Heading3"' in doc
    assert "footnotes+xml" in types


def _jpeg(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(out, "JPEG")
    return out.getvalue()


def test_images_survive_to_the_publish_model():
    data = build_docx(
        [
            PageBlock("heading", [Run("One")]),
            PageBlock("image", image=make_png(4, 3), image_type="image/png"),
            PageBlock("image", image=_jpeg(2000, 1000), image_type="image/jpeg"),
        ],
        title="T",
        language="en-US",
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
        doc = zf.read("word/document.xml").decode()
        assert len(media) == 2 and "image/jpeg" in zf.read("[Content_Types].xml").decode()
    pub = docx_to_pub(data, "en-US")
    assert sorted(i.media_type for i in pub.images.values()) == ["image/jpeg", "image/png"]
    # the wide picture is scaled to the text width, keeping its aspect ratio
    sizes = [(int(x), int(y)) for x, y in re.findall(r'extent cx="(\d+)" cy="(\d+)"', doc)]
    assert sizes[0] == (4 * 9525, 3 * 9525)
    assert sizes[1][0] == 5731510 and abs(sizes[1][0] / sizes[1][1] - 2) < 0.01


def test_control_characters_are_stripped():
    dirty = "a\x00b\x0cc\x1bd"
    data = build_docx(
        [
            PageBlock("heading", [Run("H\x00i")]),
            PageBlock("paragraph", [Run(dirty)]),
            PageBlock("paragraph", [Run("x", note=1)]),
            PageBlock("footnote", [Run(dirty)], note=1),
        ],
        title=dirty,
        language="en-US\x00",
    )
    book = read_docx(data)
    assert book.title == "ab cd" and book.language == "en-US"
    texts = [b.text for b in book.chapters[0].blocks]
    assert "Hi" in texts and "ab cd" in texts


def test_oversized_image_takes_the_size_fallback(monkeypatch):
    def bomb(*args, **kwargs):
        raise Image.DecompressionBombError("too big")

    monkeypatch.setattr(Image, "open", bomb)
    data = build_docx(
        [PageBlock("image", image=make_png(4, 3), image_type="image/png")],
        title="T",
        language="en-US",
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        doc = zf.read("word/document.xml").decode()
    assert f'cx="{400 * 9525}" cy="{300 * 9525}"' in doc
