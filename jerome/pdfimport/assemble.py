"""Turn the pages read from a PDF into one clean, ordered list of blocks for the book."""

import re
from dataclasses import replace

from jerome.pdfimport.model import PageBlock, PageResult, Run

BAND = 0.08
REPEATS = 3  # a header repeats on at least this many pages
SHARE = 0.1  # and this share of the pages (or of the even or the odd pages)
LARGER = 1.15  # a block this much larger than the body text is never furniture
NOTE_SIZE = 0.9  # a paragraph ending a page at most this size of the body is a note
SENTENCE_END = ".!?:»“”"
# a hyphen before one of these is a compound ("pre- and post-war"), not a split word
CONJUNCTIONS = {"and", "or", "nor", "to", "und", "oder", "bis", "sowie", "als", "wie"}
ROMAN = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
LONE_NUMBER = re.compile(r"^[\W_]*(\d+|[A-Za-z]+)[\W_]*$")
WORD = re.compile(r"[^\W\d_]+")


def _text(b: PageBlock) -> str:
    return "".join(r.text for r in b.runs)


def _copy(b: PageBlock) -> PageBlock:
    return replace(b, runs=[replace(r) for r in b.runs])


def _bands(b: PageBlock) -> list[str]:
    out = []
    if b.top < BAND:
        out.append("top")
    if b.bottom > 1 - BAND:
        out.append("bottom")
    return out


def _is_lone_number(text: str) -> bool:
    m = LONE_NUMBER.match(text.strip())
    if not m:
        return False
    word = m.group(1)
    if word.isdigit():
        return True
    return (word.islower() or word.isupper()) and bool(ROMAN.match(word.lower()))


def _key(text: str) -> str:
    return " ".join(re.sub(r"\d+", "", text).split()).casefold()


def _weighted_median(pairs: list[tuple[float, int]]) -> float:
    """The median of the values, each counted ``weight`` times."""
    pairs = sorted(pairs)
    half, seen = sum(w for _, w in pairs) / 2, 0
    for value, weight in pairs:
        seen += weight
        if seen >= half:
            return value
    return 0.0


def _body_sizes(pages: list[PageResult], kept: list[list[PageBlock]]) -> dict[str, float]:
    """The book's body size per source: the median paragraph size, by characters. Text
    layers measure in points and Tesseract in pixels, so each has its own; 0 when unknown."""
    sizes: dict[str, list[tuple[float, int]]] = {}
    for p, blocks in zip(pages, kept, strict=True):
        for b in blocks:
            if b.kind == "paragraph" and b.size > 0:
                sizes.setdefault(p.source, []).append((round(b.size, 1), len(_text(b))))
    return {source: _weighted_median(pairs) for source, pairs in sizes.items()}


def _larger_than_body(b: PageBlock, body: float) -> bool:
    return b.size > 0 and body > 0 and b.size >= LARGER * body


def _join_drop_caps(blocks: list[PageBlock]) -> list[PageBlock]:
    """A one-letter heading followed by a paragraph is a drop cap: it starts that paragraph."""
    out: list[PageBlock] = []
    for b in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == "heading"
            and len(_text(prev).strip()) == 1
            and _text(prev).strip().isalpha()
            and b.kind == "paragraph"
            and _first_text_run(b) is not None
        ):
            first = _first_text_run(b)
            first.text = _text(prev).strip() + first.text.lstrip()
            b.top = min(b.top, prev.top)
            out[-1] = b
        else:
            out.append(b)
    return out


def _drop_furniture(
    pages: list[PageResult], kept: list[list[PageBlock]], body: dict[str, float]
) -> list[list[PageBlock]]:
    """Drop running headers, footers and page numbers: text in the top or bottom band that
    repeats there (digits aside) on at least 3 pages and 10% of the pages, or of the even or
    the odd pages alone (alternating headers), and lone numbers there. A block larger than
    the body text (a chapter heading or number) and pictures always stay."""
    counted = {"all": 0, 0: 0, 1: 0}
    seen: dict[tuple, int] = {}
    for p, blocks in zip(pages, kept, strict=True):
        if not any(b.kind != "image" for b in blocks):
            continue
        parity = p.page % 2
        counted["all"] += 1
        counted[parity] += 1
        keys = set()
        for b in blocks:
            if b.kind in ("image", "footnote"):
                continue
            k = _key(_text(b))
            if k:
                keys.update((band, k) for band in _bands(b))
        for item in keys:
            seen[(*item, "all")] = seen.get((*item, "all"), 0) + 1
            seen[(*item, parity)] = seen.get((*item, parity), 0) + 1

    def repeats(band: str, k: str, parity: int) -> bool:
        return any(
            seen.get((band, k, group), 0) >= max(REPEATS, SHARE * counted[group])
            for group in ("all", parity)
        )

    def drop(b: PageBlock, p: PageResult) -> bool:
        if b.kind in ("image", "footnote"):
            return False
        bands = _bands(b)
        if not bands or _larger_than_body(b, body.get(p.source, 0.0)):
            return False
        if _is_lone_number(_text(b)):
            return True
        k = _key(_text(b))
        return bool(k) and any(repeats(band, k, p.page % 2) for band in bands)

    return [[b for b in blocks if not drop(b, p)] for p, blocks in zip(pages, kept, strict=True)]


def _rank_headings(
    pages: list[PageResult], kept: list[list[PageBlock]], body: dict[str, float]
) -> None:
    """Heading levels of text layers, judged across the book: the largest heading size found
    on at least 2 pages (or else the largest) and anything larger is level 1, the next size
    level 2, smaller ones level 3. Other engines' headings keep their levels."""
    text_body = body.get("text", 0.0)
    pages_of: dict[float, set[int]] = {}
    for p, blocks in zip(pages, kept, strict=True):
        if p.source != "text":
            continue
        for b in blocks:
            if b.kind == "heading" and _larger_than_body(b, text_body):
                pages_of.setdefault(_size_step(b.size), set()).add(p.page)
    if not pages_of:
        return
    sizes = sorted(pages_of, reverse=True)
    first = next((s for s in sizes if len(pages_of[s]) >= 2), sizes[0])
    second = next((s for s in sizes if s < first), None)
    for p, blocks in zip(pages, kept, strict=True):
        if p.source != "text":
            continue
        for b in blocks:
            if b.kind != "heading":
                continue
            step = _size_step(b.size) if _larger_than_body(b, text_body) else 0.0
            b.level = 1 if step >= first else 2 if step == second else 3


def _size_step(size: float) -> float:
    """A size to half a point: one style's lines differ by a little."""
    return round(size * 2) / 2


def _split_notes(body: list[PageBlock], size: float) -> tuple[list[PageBlock], list[PageBlock]]:
    """Small paragraphs that end a page (notes the reader could not match): they are kept
    after the page's body, so they never stop a paragraph running on to the next page."""
    end = len(body)
    while (
        end > 0
        and body[end - 1].kind == "paragraph"
        and size > 0
        and 0 < body[end - 1].size <= NOTE_SIZE * size
    ):
        end -= 1
    return body[:end], body[end:]


def _renumber_notes(
    body: list[PageBlock], notes: list[PageBlock], counter: int
) -> tuple[list[PageBlock], int]:
    """Match references to footnotes on one page; return the footnote tail and the counter."""
    by_number: dict[int, PageBlock] = {}
    for n in notes:
        if n.note is not None and n.note not in by_number:
            by_number[n.note] = n
    matched: list[PageBlock] = []
    for b in body:
        for i, r in enumerate(b.runs):
            if r.note is None:
                continue
            foot = by_number.get(r.note)
            if foot is not None and not any(foot is m for m in matched):
                matched.append(foot)
                counter += 1
                b.runs[i] = replace(r, note=counter)
                foot.note = counter
            else:  # no footnote to point at: keep the number as plain text
                b.runs[i] = Run(str(r.note), r.italic, r.bold)
    for n in notes:
        if not any(n is m for m in matched):
            n.kind, n.note = "paragraph", None
    return notes, counter


def _rejoin_hyphens(b: PageBlock) -> None:
    full = "".join(r.text for r in b.runs)
    drop: set[int] = set()
    for m in re.finditer(r"-(?= )", full):
        i = m.start()
        if i == 0 or not (full[i - 1].isalpha() and full[i - 1].islower()):
            continue
        w = WORD.match(full, i + 2)
        if not w or not w.group()[0].islower() or w.group() in CONJUNCTIONS:
            continue
        drop.update((i, i + 1))
    if not drop:
        return
    pos = 0
    for r in b.runs:
        end = pos + len(r.text)
        r.text = "".join(c for k, c in zip(range(pos, end), r.text, strict=True) if k not in drop)
        pos = end
    b.runs = [r for r in b.runs if r.text or r.note is not None]


def _first_text_run(b: PageBlock) -> Run | None:
    return next((r for r in b.runs if r.text.strip()), None)


def _last_text_run(b: PageBlock) -> Run | None:
    return next((r for r in reversed(b.runs) if r.text.strip()), None)


def _continues(prev: PageBlock, nxt: PageBlock) -> bool:
    last, first = _last_text_run(prev), _first_text_run(nxt)
    if last is None or first is None:
        return False
    end, start = last.text.rstrip()[-1], first.text.lstrip()[0]
    return end not in SENTENCE_END and start.isalpha() and start.islower()


def _join(prev: PageBlock, nxt: PageBlock) -> None:
    last, first = _last_text_run(prev), _first_text_run(nxt)
    last.text = last.text.rstrip()
    first.text = first.text.lstrip()
    before = last.text[:-1]
    word = WORD.match(first.text)
    split_word = (
        last.text.endswith("-")
        and before[-1:].isalpha()
        and before[-1:].islower()
        and word is not None
        and word.group()[0].islower()
        and word.group() not in CONJUNCTIONS
    )
    if split_word:
        last.text = before
    else:
        first.text = " " + first.text
    prev.runs.extend(nxt.runs)


def assemble(pages: list[PageResult]) -> list[PageBlock]:
    ordered = sorted(pages, key=lambda p: p.page)
    copies = [_join_drop_caps([_copy(b) for b in p.blocks]) for p in ordered]
    body_size = _body_sizes(ordered, copies)
    kept = _drop_furniture(ordered, copies, body_size)
    _rank_headings(ordered, kept, body_size)
    bodies: list[list[PageBlock]] = []
    tails: list[list[PageBlock]] = []
    counter = 0
    for p, blocks in zip(ordered, kept, strict=True):
        body, small = _split_notes(
            [b for b in blocks if b.kind != "footnote"], body_size.get(p.source, 0.0)
        )
        tail, counter = _renumber_notes(body, [b for b in blocks if b.kind == "footnote"], counter)
        for b in body + small:
            _rejoin_hyphens(b)
        bodies.append(body)
        tails.append(small + tail)

    # a paragraph that runs on to the next page, but only across consecutive page numbers
    carry: PageBlock | None = None  # the paragraph that ends the previous page
    for i, p in enumerate(ordered):
        body = bodies[i]
        consecutive = i > 0 and p.page == ordered[i - 1].page + 1
        if (
            consecutive
            and carry is not None
            and body
            and body[0].kind == "paragraph"
            and _continues(carry, body[0])
        ):
            _join(carry, body.pop(0))
            end = body[-1] if body else carry
        else:
            end = body[-1] if body else None
        carry = end if end is not None and end.kind == "paragraph" else None

    out: list[PageBlock] = []
    for body, tail in zip(bodies, tails, strict=True):
        out.extend(body)
        out.extend(tail)
    return out


def report(pages: list[PageResult]) -> dict:
    count = {
        s: sum(1 for p in pages if p.source == s)
        for s in ("text", "ai", "tesseract", "image", "blank")
    }
    return {
        "pages": len(pages),
        "text_pages": count["text"],
        "ocr_pages": count["ai"] + count["tesseract"],
        "engine_pages": {"ai": count["ai"], "tesseract": count["tesseract"]},
        "image_pages": count["image"],
        "blank_pages": count["blank"],
        # 1-based, as a reader counts pages (PageResult.page is 0-based)
        "failed_pages": sorted(p.page + 1 for p in pages if p.source == "failed"),
    }
