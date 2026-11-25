"""Turn the pages read from a PDF into one clean, ordered list of blocks for the book."""

import math
import re
from dataclasses import replace

from jerome.pdfimport.model import PageBlock, PageResult, Run

BAND = 0.08
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


def _drop_furniture(pages: list[list[PageBlock]]) -> list[list[PageBlock]]:
    counted = sum(1 for blocks in pages if any(b.kind != "image" for b in blocks))
    need = max(3, math.ceil(counted / 2))
    seen: dict[tuple[str, str], int] = {}
    for blocks in pages:
        keys = set()
        for b in blocks:
            if b.kind in ("image", "footnote"):
                continue
            k = _key(_text(b))
            if k:
                keys.update((band, k) for band in _bands(b))
        for item in keys:
            seen[item] = seen.get(item, 0) + 1

    def drop(b: PageBlock) -> bool:
        if b.kind in ("image", "footnote"):
            return False
        bands = _bands(b)
        if not bands:
            return False
        if _is_lone_number(_text(b)):
            return True
        k = _key(_text(b))
        return bool(k) and any(seen.get((band, k), 0) >= need for band in bands)

    return [[b for b in blocks if not drop(b)] for blocks in pages]


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
    kept = _drop_furniture([[_copy(b) for b in p.blocks] for p in ordered])
    bodies: list[list[PageBlock]] = []
    tails: list[list[PageBlock]] = []
    counter = 0
    for blocks in kept:
        body = [b for b in blocks if b.kind != "footnote"]
        tail, counter = _renumber_notes(body, [b for b in blocks if b.kind == "footnote"], counter)
        for b in body:
            _rejoin_hyphens(b)
        bodies.append(body)
        tails.append(tail)

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
        s: sum(1 for p in pages if p.source == s) for s in ("text", "ai", "tesseract", "image")
    }
    return {
        "pages": len(pages),
        "text_pages": count["text"],
        "ocr_pages": count["ai"] + count["tesseract"],
        "engine_pages": {"ai": count["ai"], "tesseract": count["tesseract"]},
        "image_pages": count["image"],
        "failed_pages": sorted(p.page for p in pages if p.source == "failed"),
    }
