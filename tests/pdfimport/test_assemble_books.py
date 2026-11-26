"""Whole generated books read page by page and assembled: running headers, page numbers,
chapter headings and paragraphs that run across pages."""

import random

from jerome.pdfimport.assemble import assemble
from jerome.pdfimport.reader import count_pages, read_text_page
from tests.fixtures.pdf_builders import text_pdf

_WORDS = (
    "the ship came in on a grey morning and harbour was quiet while gulls circled over "
    "old stone walls where fishermen mended their nets beside boats painted blue green "
    "yellow under clouds that drifted slowly towards hills beyond town"
).split()


def _paragraph(rnd: random.Random, words: int) -> str:
    """One long sentence of lowercase words and commas: every line end inside it, and every
    page break, falls mid-sentence."""
    out = []
    for i in range(words):
        word = rnd.choice(_WORDS)
        out.append(word + ("," if i % 11 == 10 else ""))
    text = " ".join(out).rstrip(",")
    return text[0].upper() + text[1:] + "."


def _book(chapters: int = 3, paragraphs: int = 5, words: int = 380) -> tuple[str, list[str]]:
    """The HTML of a book and the text of each heading and paragraph, in order."""
    rnd = random.Random(11)
    html, expected = [], []
    for n in range(1, chapters + 1):
        html.append(f"<h1>Chapter {n}</h1>")
        expected.append(f"Chapter {n}")
        for _ in range(paragraphs):
            text = _paragraph(rnd, words)
            html.append(f"<p>{text}</p>")
            expected.append(text)
    return "".join(html), expected


# chapters start a page, near its top (inside the header band), in a larger type
_CHAPTERS = "h1 { page-break-before: always; margin-top: 0 } @page { margin-top: 18mm }"


def _assembled(pdf: bytes) -> list:
    pages = [read_text_page(pdf, n) for n in range(count_pages(pdf))]
    return assemble(pages)


def _texts(blocks) -> list[str]:
    return ["".join(r.text for r in b.runs) for b in blocks]


def test_alternating_running_headers_and_page_numbers_are_dropped():
    html, expected = _book()
    css = (
        _CHAPTERS
        + '@page :left { @top-center { content: "Jane Author"; font-size: 9pt } }'
        + '@page :right { @top-center { content: "The Long Voyage"; font-size: 9pt } }'
    )
    pdf = text_pdf(html, page_numbers=True, css=css)
    assert count_pages(pdf) >= 12
    blocks = _assembled(pdf)
    found = _texts(blocks)
    assert found == expected  # no header, no page number, every paragraph joined
    assert [b.kind for b in blocks if b.kind == "heading"] == ["heading"] * 3
    assert all(b.level == 1 for b in blocks if b.kind == "heading")


def test_chapter_title_running_headers_are_dropped():
    html, expected = _book()
    css = (
        _CHAPTERS
        + "h1 { string-set: chapter content() }"
        + "@page { @top-center { content: string(chapter); font-size: 9pt } }"
    )
    pdf = text_pdf(html, page_numbers=True, css=css)
    assert count_pages(pdf) >= 12
    assert _texts(_assembled(pdf)) == expected


def test_heading_levels_are_ranked_across_the_book():
    rnd = random.Random(5)
    html, chapters = [], []
    for n in range(1, 4):
        html.append(f"<h1>Part of the voyage {n}</h1>")
        chapters.append(f"Part of the voyage {n}")
        for m in range(1, 3):
            html.append(f"<h2>Section {n}.{m}</h2><p>{_paragraph(rnd, 60)}</p>")
    css = (
        "body { font-size: 11pt } h1 { font-size: 16pt; page-break-before: always }"
        " h2 { font-size: 13pt }"
    )
    blocks = _assembled(text_pdf("".join(html), css=css))
    headings = [(b.level, t) for b, t in zip(blocks, _texts(blocks), strict=True)]
    headings = [h for h, b in zip(headings, blocks, strict=True) if b.kind == "heading"]
    assert [t for level, t in headings if level == 1] == chapters
    assert [level for level, t in headings if t.startswith("Section")] == [2] * 6


_NAMES = [
    "Arrival",
    "The Storm",
    "Landfall",
    "The Island",
    "Night Watch",
    "The Reef",
    "Calm Water",
    "The Return",
]


def test_running_chapter_titles_of_short_chapters_are_dropped():
    rnd = random.Random(23)
    html, expected = [], []
    for name in _NAMES:
        html.append(f"<h1>{name}</h1>")
        expected.append(name)
        for _ in range(4):
            text = _paragraph(rnd, 330)
            html.append(f"<p>{text}</p>")
            expected.append(text)
    css = (
        _CHAPTERS
        + "h1 { string-set: chapter content() }"
        + '@page :left { @top-center { content: "Jane Author"; font-size: 9pt } }'
        + "@page :right { @top-center { content: string(chapter); font-size: 9pt } }"
    )
    pdf = text_pdf("".join(html), page_numbers=True, css=css)
    assert count_pages(pdf) >= 24
    blocks = _assembled(pdf)
    assert _texts(blocks) == expected
    assert [_texts([b])[0] for b in blocks if b.kind == "heading"] == _NAMES


def test_body_text_on_a_page_of_notes_stays_body_text():
    rnd = random.Random(8)
    body = "".join(f"<p>{_paragraph(rnd, 300)}</p>" for _ in range(2))
    short = "It was over by the evening, and nobody spoke of it again."
    notes = "".join(
        f'<p style="font-size: 9pt">{n} {_paragraph(rnd, 45)}</p>' for n in range(1, 13)
    )
    pdf = text_pdf(
        body + '<div style="page-break-before: always"></div>' + f"<p>{short}</p>" + notes
    )
    blocks = _assembled(pdf)
    (found,) = [b for b in blocks if _texts([b])[0] == short]
    assert found.kind == "paragraph"
