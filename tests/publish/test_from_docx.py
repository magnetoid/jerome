from jerome.publish.from_docx import docx_to_pub
from tests.fixtures.docx_builders import make_docx


def _pub(lines, **kw):
    return docx_to_pub(make_docx(lines, **kw), "en-US")


def test_docx_chapters_split_at_level_0():
    pub = _pub(["# One", "a", "## Sub", "b", "# Two", "c"])
    assert [c.title for c in pub.chapters] == ["One", "Two"]
    assert all(c.in_toc for c in pub.chapters)
    assert "<h1>One</h1>" in pub.chapters[0].html
    assert "<h2>Sub</h2>" in pub.chapters[0].html
    assert "<p>c</p>" in pub.chapters[1].html
    assert pub.language == "en-US"


def test_docx_custom_style_with_outline_level_is_a_heading():
    pub = _pub(
        ["@Kapitel: Eins", "a", "@Kapitel: Zwei", "b", "@Tief: deep"],
        styles={"Kapitel": 0, "Tief": 5},
    )
    assert [c.title for c in pub.chapters] == ["Eins", "Zwei"]
    assert "<h1>Eins</h1>" in pub.chapters[0].html
    assert "<h4>deep</h4>" in pub.chapters[1].html


def test_docx_text_before_first_heading_is_untitled_front_content():
    pub = _pub(["For my mother.", "# One", "a"])
    front, one = pub.chapters
    assert (front.title, front.in_toc) == (None, False)
    assert "For my mother." in front.html
    assert (one.title, one.in_toc) == ("One", True)


def test_docx_without_level_0_heading_is_one_chapter():
    pub = _pub(["a", "## Sub", "b"])
    assert len(pub.chapters) == 1
    assert (pub.chapters[0].title, pub.chapters[0].in_toc) == (None, True)


def test_docx_footnote_becomes_inline_note():
    pub = _pub(["# One", "Text[^1] more"], footnotes={1: "Note *it*"})
    html = pub.chapters[0].html
    assert 'Text<span class="jr-note">Note <em>it</em></span> more' in html
    assert "footnote-ref" not in html and "↑" not in html and "<ol" not in html


def test_docx_lists_tables_images():
    pub = _pub(["# One", "- a", "- b", "1. x", "|c|d|", "![A picture](png)"])
    html = pub.chapters[0].html
    assert "<ul><li>a</li><li>b</li></ul>" in html
    assert "<ol><li>x</li></ol>" in html
    assert "<table>" in html and "<td><p>c</p></td>" in html
    [(name, image)] = pub.images.items()
    assert image.media_type == "image/png" and image.data.startswith(b"\x89PNG")
    assert name == image.name == "image1.png"
    assert f'<img src="images/{name}" alt="A picture"/>' in html or (
        f'<img alt="A picture" src="images/{name}"/>' in html
    )


def test_docx_linked_image_dropped_with_warning():
    pub = _pub(["# One", "![Remote](link:https://example.com/a.png)", "b"])
    assert pub.images == {}
    assert "<img" not in pub.chapters[0].html
    assert any("linked" in w for w in pub.warnings)


def test_docx_toc_field_left_out():
    pub = _pub(["[TOC: One\t1]", "# One", "a"])
    assert pub.left_out == ["Word table of contents"]
    assert all("One\t1" not in c.html and "One 1" not in c.html for c in pub.chapters)
    assert [c.title for c in pub.chapters] == ["One"]


def test_empty_heading_is_labelled_later():
    pub = _pub(["# One", "a", "# ![Ornament](png)", "b"])
    assert [c.title for c in pub.chapters] == ["One", None]
    assert pub.chapters[1].in_toc
    assert "<p>b</p>" in pub.chapters[1].html


def test_escaping_survives(tmp_path):  # Review Focus 1
    pub = _pub(["# Fish & <Chips>", "„Straße“ & <b>"])
    html = pub.chapters[0].html
    assert pub.chapters[0].title == "Fish & <Chips>"
    assert "Fish &amp; &lt;Chips&gt;" in html
    assert "„Straße“ &amp; &lt;b&gt;" in html
