from jerome.pdfimport.assemble import assemble, report
from jerome.pdfimport.model import PageBlock, PageResult, Run


def para(text, top=0.3, bottom=0.4, **kw):
    return PageBlock("paragraph", [Run(text)], top=top, bottom=bottom, **kw)


def text_of(b):
    return "".join(r.text for r in b.runs)


def texts(blocks):
    return [text_of(b) for b in blocks]


def page(n, *blocks, source="text"):
    return PageResult(n, source, list(blocks))


def test_running_header_with_page_number_is_dropped():
    pages = [
        page(
            n,
            para(f"Chapter One · {n}", top=0.03, bottom=0.05),
            para(f"Body {n}.", top=0.2, bottom=0.5),
        )
        for n in (12, 13, 14)
    ]
    assert texts(assemble(pages)) == ["Body 12.", "Body 13.", "Body 14."]


def test_header_seen_on_too_few_pages_stays():
    pages = [
        page(1, para("Chapter One · 1", top=0.03, bottom=0.05), para("A.")),
        page(2, para("B.")),
        page(3, para("C.")),
    ]
    assert "Chapter One · 1" in texts(assemble(pages))


def test_lone_page_numbers_are_dropped():
    nums = ["12", "— 12 —", "· 13", "- 7 -", "xiv", "XII"]
    pages = [
        page(i + 1, para("Text.", top=0.2, bottom=0.5), para(s, top=0.95, bottom=0.98))
        for i, s in enumerate(nums)
    ]
    assert texts(assemble(pages)) == ["Text."] * len(nums)


def test_number_outside_band_and_images_stay():
    img = PageBlock("image", image=b"x", image_type="png", top=0.01, bottom=0.03)
    out = assemble([page(1, para("12", top=0.4, bottom=0.45), img)])
    assert [b.kind for b in out] == ["paragraph", "image"]


def test_paragraph_continues_across_pages():
    p1 = page(1, para("The cat sat on the", top=0.2, bottom=0.9))
    p2 = page(2, para("mat and slept.", top=0.1, bottom=0.2), para("Next."))
    assert texts(assemble([p1, p2])) == ["The cat sat on the mat and slept.", "Next."]


def test_no_join_after_sentence_end_uppercase_or_gap():
    p1 = page(1, para("It ended."))
    p2 = page(2, para("then more"))
    assert len(assemble([p1, p2])) == 2
    p3 = page(3, para("open ended"))
    p4 = page(4, para("More text"))
    assert len(assemble([p3, p4])) == 2
    p5 = page(5, para("open ended"))
    p7 = page(7, para("more text"))
    assert len(assemble([p5, p7])) == 2


def test_join_keeps_styles_and_footnotes_stay_after_body():
    p1 = PageResult(
        1,
        "text",
        [
            PageBlock("paragraph", [Run("one "), Run("two", italic=True)]),
            PageBlock("footnote", [Run("note")], note=1),
        ],
    )
    p2 = page(2, PageBlock("paragraph", [Run("three", bold=True)]))
    out = assemble([p1, p2])
    assert texts(out) == ["one two three", "note"]
    assert out[0].runs[1].italic and out[0].runs[-1].bold


def test_hyphen_joined_only_between_lowercase():
    def one(text):
        return texts(assemble([page(1, para(text))]))[0]

    assert one("a long Hy- Phen here") == "a long Hy- Phen here"
    assert one("a con- tinued word") == "a continued word"
    assert one("Nord- Süd") == "Nord- Süd"
    assert one("pre- and post-war") == "pre- and post-war"
    assert one("Ein- und Ausgang") == "Ein- und Ausgang"
    assert one("Nord- und süd- lich") == "Nord- und südlich"
    # across runs
    b = PageBlock("paragraph", [Run("con-"), Run(" tinued", italic=True)])
    assert texts(assemble([page(1, b)])) == ["continued"]
    # across pages
    out = assemble([page(1, para("a con-")), page(2, para("tinued word"))])
    assert texts(out) == ["a continued word"]
    out = assemble([page(1, para("pre-")), page(2, para("and post"))])
    assert texts(out) == ["pre- and post"]
    out = assemble([page(1, para("Nord-")), page(2, para("Süd"))])
    assert texts(out) == ["Nord-", "Süd"]


def test_footnotes_match_their_references_and_strays_stay_paragraphs():
    p1 = PageResult(
        1,
        "text",
        [
            PageBlock("paragraph", [Run("A"), Run("", note=3), Run(" and B"), Run("", note=7)]),
            PageBlock("footnote", [Run("three")], note=3),
            PageBlock("footnote", [Run("stray")], note=9),
        ],
    )
    p2 = PageResult(
        2,
        "text",
        [
            PageBlock("paragraph", [Run("Sea"), Run("", note=1)]),
            PageBlock("footnote", [Run("one")], note=1),
        ],
    )
    out = assemble([p1, p2])
    assert [b.kind for b in out] == ["paragraph", "footnote", "paragraph", "paragraph", "footnote"]
    assert [r.note for r in out[0].runs] == [None, 1, None, None]
    assert text_of(out[0]) == "A and B7"
    assert out[1].note == 1 and text_of(out[1]) == "three"
    assert out[2].note is None and text_of(out[2]) == "stray"
    assert out[3].runs[1].note == 2 and out[4].note == 2
    # input untouched
    assert p1.blocks[0].runs[1].note == 3 and p1.blocks[1].note == 3


def test_report_counts_and_lists_failed_pages():
    pages = [
        page(3, para("x"), source="ai"),
        page(1, para("x"), source="text"),
        PageResult(5, "failed", [], "boom"),
        page(2, para("x"), source="tesseract"),
        PageResult(4, "image", []),
        PageResult(6, "failed", [], "boom"),
        page(7, para("x"), source="ai"),
        PageResult(0, "blank", []),
    ]
    assert report(pages) == {
        "pages": 8,
        "text_pages": 1,
        "ocr_pages": 3,
        "engine_pages": {"ai": 2, "tesseract": 1},
        "image_pages": 1,
        "blank_pages": 1,
        "failed_pages": [6, 7],  # 1-based, as a reader counts pages
    }


def test_a_larger_chapter_number_or_heading_in_the_band_stays():
    pages = [
        page(
            n,
            PageBlock("heading", [Run(num)], top=0.04, bottom=0.07, size=20),
            PageBlock("heading", [Run(f"Chapter {n}")], top=0.06, bottom=0.075, size=20),
            para(f"Body {n}.", size=12),
            para("12", top=0.95, bottom=0.97, size=9),
        )
        for n, num in ((1, "I"), (5, "II"), (9, "III"), (13, "IV"))
    ]
    out = texts(assemble(pages))
    assert out == [
        x
        for n, num in ((1, "I"), (5, "II"), (9, "III"), (13, "IV"))
        for x in (num, f"Chapter {n}", f"Body {n}.")
    ]


def test_a_header_on_every_other_page_is_dropped():
    pages = []
    for n in range(11):
        header = "Jane Author" if n % 2 else "The Long Voyage"
        pages.append(page(n, para(header, top=0.03, bottom=0.05, size=9), para(f"Body {n}.")))
    assert texts(assemble(pages)) == [f"Body {n}." for n in range(11)]


def test_a_drop_cap_joins_its_paragraph():
    p = page(
        1,
        PageBlock("heading", [Run("T")], top=0.2, bottom=0.25, size=36),
        para("he ship came in.", top=0.2, bottom=0.4, size=12),
    )
    out = assemble([p])
    assert texts(out) == ["The ship came in."] and out[0].kind == "paragraph"


def test_small_notes_at_the_foot_of_a_text_page_do_not_stop_the_join():
    p1 = page(
        1,
        para("The cat sat on the", top=0.2, bottom=0.85, size=12),
        para("1 A note set small at the foot.", top=0.88, bottom=0.9, size=9),
    )
    p2 = page(2, para("mat and slept.", top=0.1, bottom=0.2, size=12))
    out = texts(assemble([p1, p2]))
    assert out == ["The cat sat on the mat and slept.", "1 A note set small at the foot."]


_TITLES = [
    "The Harbour",
    "The Storm",
    "Landfall",
    "The Island",
    "Night Watch",
    "The Reef",
    "Calm Water",
    "The Return",
    "Old Friends",
    "The Lighthouse",
    "Departure",
    "Home Port",
]


def _book_with_running_titles(chapters: int, length: int):
    """Chapters of ``length`` pages: a large heading on each chapter's first page, the
    chapter's title as the running header of odd pages and the author on even pages."""
    pages = []
    for c in range(chapters):
        for i in range(length):
            n = c * length + i
            header = _TITLES[c] if n % 2 else "Jane Author"
            blocks = [para(header, top=0.03, bottom=0.05, size=9, centered=True)]
            if i == 0:
                blocks.append(
                    PageBlock("heading", [Run(_TITLES[c])], top=0.1, bottom=0.14, size=20)
                )
            blocks.append(para(f"Body {n}.", size=12))
            pages.append(page(n, *blocks))
    return pages


def test_chapter_title_headers_in_a_long_book_are_dropped():
    out = texts(assemble(_book_with_running_titles(12, 6)))  # 72 pages
    assert out == [
        x for c in range(12) for x in [_TITLES[c]] + [f"Body {c * 6 + i}." for i in range(6)]
    ]


def test_chapter_title_headers_of_short_chapters_are_dropped():
    out = texts(assemble(_book_with_running_titles(8, 4)))  # 32 pages, 2 odd pages a chapter
    assert out == [
        x for c in range(8) for x in [_TITLES[c]] + [f"Body {c * 4 + i}." for i in range(4)]
    ]


def test_a_centred_or_unplaced_one_letter_heading_is_not_a_drop_cap():
    centred = PageBlock("heading", [Run("I")], top=0.1, bottom=0.15, size=20, centered=True)
    out = assemble([page(1, centred, para("It was a grey morning.", top=0.2, size=12))])
    assert [b.kind for b in out] == ["heading", "paragraph"]
    unplaced = PageBlock("heading", [Run("V")], top=0.5, bottom=0.5)
    out = assemble([page(1, unplaced, para("It rained.", top=0.5, bottom=0.5), source="ai")])
    assert texts(out) == ["V", "It rained."]


def test_a_text_heading_no_larger_than_the_book_body_is_a_paragraph():
    pages = [page(n, para(f"Body text of page {n}.", size=12)) for n in range(3)]
    pages.append(page(3, PageBlock("heading", [Run("Body text that looked large.")], size=12)))
    pages.append(page(4, PageBlock("heading", [Run("A centred line")], size=12, centered=True)))
    kinds = [b.kind for b in assemble(pages)]
    assert kinds == ["paragraph"] * 4 + ["heading"]


def test_small_paragraphs_ending_a_scanned_page_stay_in_place():
    p1 = page(
        1,
        para("The cat sat on the", top=0.2, bottom=0.8, size=30),
        para("A caption.", top=0.85, bottom=0.88, size=20),
        source="tesseract",
    )
    p2 = page(2, para("mat and slept.", top=0.1, bottom=0.2, size=30), source="tesseract")
    assert texts(assemble([p1, p2])) == ["The cat sat on the", "A caption.", "mat and slept."]


def test_an_ai_chapter_number_in_the_band_stays():
    number = PageBlock("heading", [Run("IV")], top=0.03, bottom=0.06)
    out = assemble([page(1, number, para("It rained."), source="ai")])
    assert texts(out) == ["IV", "It rained."]


def test_ai_chapter_headings_at_the_top_of_their_pages_stay():
    pages = []
    for n in range(1, 5):
        heading = PageBlock("heading", [Run(f"Chapter {n}")], top=0.03, bottom=0.06)
        pages.append(page(n, heading, para(f"Body {n}.", top=0.2, bottom=0.6), source="ai"))
    assert texts(assemble(pages)) == [
        x for n in range(1, 5) for x in (f"Chapter {n}", f"Body {n}.")
    ]


def test_a_centred_body_size_heading_in_the_band_stays():
    first = PageBlock("heading", [Run("Chapter 1")], top=0.1, bottom=0.13, size=20)
    band = PageBlock("heading", [Run("Chapter 2")], top=0.03, bottom=0.06, size=12, centered=True)
    pages = [
        page(1, first, para("Body one.", size=12)),
        page(2, band, para("Body two.", size=12)),
    ]
    assert texts(assemble(pages)) == ["Chapter 1", "Body one.", "Chapter 2", "Body two."]
