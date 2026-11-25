"""Reading a rendered page with Tesseract (the fast, free engine for scanned pages)."""

import io
import shutil
import statistics
import subprocess
from typing import Literal

from jerome.errors import JeromeError
from jerome.pdfimport.model import PageBlock, PageResult, Run

_LANGUAGES = {"en": "eng", "de": "deu"}
_TIMEOUT = 120
_HEADING_RATIO = 1.3  # mean word height against the page median
_MIN_WORDS = 5  # fewer words than this and the page is a picture


class TesseractMissing(JeromeError):
    """The tesseract program is not installed."""


def tesseract_page(png: bytes, page: int, language: Literal["en", "de"]) -> PageResult:
    """Read a page image (``page`` is 0-based). A page with fewer than 5 words of text is a
    picture and comes back as one image block. Tesseract's TSV does not mark picture regions,
    so a picture on a page that also has text is not kept (the AI engine crops those)."""
    program = shutil.which("tesseract")
    if program is None:
        raise TesseractMissing("Tesseract is not installed")
    try:
        done = subprocess.run(
            [program, "stdin", "stdout", "-l", _LANGUAGES[language], "--psm", "3", "tsv"],
            input=png,
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise JeromeError(f"Tesseract took longer than {_TIMEOUT} seconds") from None
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()
        raise JeromeError(f"Tesseract failed: {detail}")
    words = _words(done.stdout.decode("utf-8", "replace"))
    if len(words) < _MIN_WORDS:
        block = PageBlock("image", image=png, image_type="image/png", top=0.0, bottom=1.0)
        return PageResult(page, "image", [block])
    return PageResult(page, "tesseract", _blocks(words, _page_height(png)))


def _words(tsv: str) -> list[dict]:
    """The words (level 5) of Tesseract's TSV, with a confidence and some text."""
    words = []
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t", 11)
        if len(cells) < 12 or cells[0] != "5" or not cells[11].strip():
            continue
        if float(cells[10]) < 0:
            continue
        words.append(
            {
                "par": (int(cells[2]), int(cells[3])),
                "line": int(cells[4]),
                "top": int(cells[7]),
                "height": int(cells[9]),
                "text": cells[11].strip(),
            }
        )
    return words


def _page_height(png: bytes) -> int:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return max(image.height, 1)


def _blocks(words: list[dict], height: int) -> list[PageBlock]:
    paragraphs: dict[tuple[int, int], list[dict]] = {}
    for word in words:
        paragraphs.setdefault(word["par"], []).append(word)
    median = statistics.median(w["height"] for w in words)
    blocks = []
    for group in paragraphs.values():
        ratio = statistics.fmean(w["height"] for w in group) / median if median else 1.0
        heading = ratio >= _HEADING_RATIO
        blocks.append(
            PageBlock(
                kind="heading" if heading else "paragraph",
                runs=[Run(" ".join(w["text"] for w in group))],
                level=1 if ratio >= 1.6 else 2,
                top=min(w["top"] for w in group) / height,
                bottom=min(max(w["top"] + w["height"] for w in group) / height, 1.0),
                size=statistics.fmean(w["height"] for w in group),
            )
        )
    return blocks
