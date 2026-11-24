from pathlib import Path

import pytest

from jerome.documents.epub_reader import read_epub
from jerome.documents.epub_writer import write_epub
from jerome.publish.from_epub import epub_to_pub
from jerome.publish.model import PublishTextError
from jerome.publish.textcheck import check_text
from tests.fixtures.epub_builders import edit_entry, make_epub
from tests.fixtures.images import make_png

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "public"


def _check(data: bytes):
    pub, documents, notes = epub_to_pub(data, "en")
    check_text(pub, read_epub(data), documents=documents, notes=notes)
    return pub, documents


def test_epub_chapters_follow_nav():
    data = make_epub([("One", ["a", "b", "c"]), ("Two", ["d", "e"])], split_chapter=True)
    pub, documents = _check(data)
    assert [(c.title, c.in_toc) for c in pub.chapters] == [("One", True), ("Two", True)]
    assert "<p>c</p>" in pub.chapters[0].html  # from the second file of chapter One
    assert "<p>d</p>" not in pub.chapters[0].html
    assert documents == {f"OEBPS/{n}.xhtml" for n in ("ch1", "ch1b", "ch2", "ch2b")}


def test_epub_chapters_in_one_file_split_at_anchors():
    pub, _ = _check(make_epub([("One", ["a"]), ("Two", ["b"])], two_in_one_file=True))
    assert [c.title for c in pub.chapters] == ["One", "Two"]
    assert "<p>b</p>" not in pub.chapters[0].html and "<p>b</p>" in pub.chapters[1].html


def test_epub_front_pages_left_out():
    data = make_epub(
        [("One", ["a"])],
        cover=make_png(1400, 2000),
        front_matter=True,
        front_pages=True,
        landmarks=True,
    )
    pub, documents = _check(data)
    assert pub.left_out == [
        "cover (cover.xhtml)",
        "title page (titlepage.xhtml)",
        "copyright page (copyright.xhtml)",
        "contents (nav.xhtml)",
    ]
    front, one = pub.chapters
    assert (front.title, front.in_toc) == (None, False)
    assert "For my mother." in front.html
    assert (one.title, one.in_toc) == ("One", True)
    assert "OEBPS/dedication.xhtml" in documents and "OEBPS/cover.xhtml" not in documents
    assert pub.images == {}  # the cover is not a picture in the text


def test_epub_cover_page_without_semantics_left_out():
    pub, _ = _check(make_epub([("One", ["a"])], cover=make_png(1400, 2000)))
    assert pub.left_out == ["cover (cover.xhtml)"]
    assert [c.title for c in pub.chapters] == ["One"]


def test_epub_noteref_becomes_inline_note():
    data = make_epub([("One", ["Text[^1] more", "after"])], notes={1: "Note *it*."})
    pub, _ = _check(data)
    html = pub.chapters[0].html
    assert (
        'Text<span class="jr-note"><span class="jr-drop">1</span> Note <i>it</i>.</span> more'
        in html
    )
    assert "<aside" not in html and "epub:type" not in html


def test_epub_doctored_note_fails_textcheck():
    data = make_epub([("One", ["Text[^1] more"])], notes={1: "Note here."})
    pub, documents, notes = epub_to_pub(data, "en")
    pub.chapters[0].html = pub.chapters[0].html.replace("here", "there")
    with pytest.raises(PublishTextError):
        check_text(pub, read_epub(data), documents=documents, notes=notes)


def test_epub_book_css_not_used():
    def doctor(xhtml: str) -> str:
        return xhtml.replace(
            "<p>a</p>",
            '<p class="x" style="color:red" id="p1">a</p><style>p {}</style>'
            "<blockquote><p>q</p></blockquote><table><tr><td>t</td></tr></table>",
        ).replace("<head>", '<head><link rel="stylesheet" href="s.css"/>')

    data = edit_entry(make_epub([("One", ["a"])]), "OEBPS/ch1.xhtml", doctor)
    pub, _ = _check(data)
    html = pub.chapters[0].html
    assert "<link" not in html and "<style" not in html and " style=" not in html
    assert "class=" not in html and 'id="p1"' not in html and "xmlns" not in html
    assert "<p>a</p>" in html and "<blockquote><p>q</p></blockquote>" in html
    assert "<td>t</td>" in html


def test_epub_images_collected():
    pub, _ = _check(make_epub([("One", ["a"]), ("Two", ["b"])], images=True))
    assert sorted(pub.images) == ["image1.png", "image2.png"]
    assert pub.images["image1.png"].media_type == "image/png"
    assert pub.images["image1.png"].data.startswith(b"\x89PNG")
    assert '<img src="images/image1.png" alt="Picture 1"/>' in pub.chapters[0].html


def test_escaping_survives():  # Review Focus 1
    pub, _ = _check(make_epub([("Fish & <Chips>", ["„Straße“ & <b>"])]))
    assert pub.chapters[0].title == "Fish & <Chips>"
    assert "„Straße“ &amp; &lt;b&gt;" in pub.chapters[0].html


@pytest.mark.parametrize("name", ["pg932.epub", "pg22367.epub"])
def test_public_fixtures_convert_and_pass_textcheck(name):
    clean = write_epub((PUBLIC / name).read_bytes(), {})
    pub, _ = _check(clean)
    assert any(c.in_toc for c in pub.chapters)
    assert "cover (wrap0000.xhtml)" in pub.left_out


def test_remote_resources_never_fetched(respx_mock):
    def doctor(xhtml: str) -> str:
        return xhtml.replace(
            "<p>a</p>", '<p>a</p><p><img src="https://example.com/x.png" alt="x"/></p>'
        )

    data = edit_entry(make_epub([("One", ["a"])]), "OEBPS/ch1.xhtml", doctor)
    pub, _ = _check(data)
    assert any("https://example.com/x.png" in w for w in pub.warnings)
    assert "example.com" not in pub.chapters[0].html
    assert not respx_mock.calls


def _guide(href: str, kind: str = "toc"):
    return lambda opf: opf.replace(
        "</package>", f'<guide><reference type="{kind}" title="x" href="{href}"/></guide></package>'
    )


def test_guide_entry_at_a_chapter_anchor_keeps_the_chapters():
    data = make_epub([("One", ["Alpha text."]), ("Two", ["Beta text."])], two_in_one_file=True)
    data = edit_entry(data, "OEBPS/content.opf", _guide("book.xhtml#c1"))
    pub, documents = _check(data)
    assert [c.title for c in pub.chapters] == ["One", "Two"]
    assert pub.left_out == [] and "OEBPS/book.xhtml" in documents


def test_titlepage_section_does_not_drop_the_chapters_after_it():
    data = make_epub([("One", ["Alpha text."])])
    data = edit_entry(
        data,
        "OEBPS/ch1.xhtml",
        lambda x: x.replace(
            "<body>", '<body><section epub:type="titlepage"><p>A Title</p></section>'
        ),
    )
    pub, documents = _check(data)
    assert "OEBPS/ch1.xhtml" in documents
    assert any("Alpha text." in c.html for c in pub.chapters)


def test_a_long_page_marked_as_copyright_is_kept_with_a_warning():
    long = [
        f"Sentence number {n} of real chapter text that runs on for a while." for n in range(80)
    ]
    data = make_epub([("One", ["Alpha text."]), ("Two", long)])
    data = edit_entry(data, "OEBPS/content.opf", _guide("ch2.xhtml", "copyright-page"))
    data = edit_entry(
        data, "OEBPS/nav.xhtml", lambda x: x.replace('href="ch2.xhtml', 'href="chX.xhtml')
    )
    pub, documents = _check(data)
    assert "OEBPS/ch2.xhtml" in documents
    assert any("too much text for a copyright page" in w for w in pub.warnings)
