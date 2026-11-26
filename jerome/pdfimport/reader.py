"""Reading a PDF: its page count, what kind each page is, and a text page's blocks.

Text is read with pdfminer.six. Pages that must be rendered or reread run in a ``spawn``
child (``run_isolated``), so one page that hangs or crashes the reader fails alone.
"""

import io
import multiprocessing
import queue as queue_module
import re
import statistics
import sys
import time
import traceback
import unicodedata
from collections import Counter

from jerome.errors import JeromeError, UnsupportedDocumentError
from jerome.pdfimport.model import PageBlock, PageKind, PageResult, Run

MAX_PAGES = 2000
_WIDE = 0.9  # an image covering this much of a page with text is a scan with a text layer
_SCAN = 0.5  # an image covering this much of a page without text is a scan
_HEADING_RATIO = 1.15  # as assemble.LARGER: clearly larger than the body text
_CENTER_TOLERANCE = 0.06  # of the page width
_SHORT_LINE = 80


class PageReadError(JeromeError):
    """Reading one page timed out, crashed the reader, or raised."""


def count_pages(pdf: bytes) -> int:
    """The page count. Raises UnsupportedDocumentError for an encrypted or unreadable PDF,
    or one over 2000 pages."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(pdf)
        try:
            count = len(document)
        finally:
            document.close()
    except Exception as exc:
        raise UnsupportedDocumentError(
            "This PDF cannot be read (it is encrypted, damaged or not a PDF)"
        ) from exc
    if count > MAX_PAGES:
        raise UnsupportedDocumentError(f"This PDF has {count} pages; the limit is {MAX_PAGES}")
    return count


def _layout(pdf: bytes, page: int):
    """pdfminer's layout of one page (0-based). Raises PageReadError if there is no such
    page."""
    from pdfminer.high_level import extract_pages

    if page < 0:
        raise PageReadError(f"There is no page {page}")
    layout = next(iter(extract_pages(io.BytesIO(pdf), page_numbers=[page])), None)
    if layout is None:
        raise PageReadError(f"There is no page {page}")
    return layout


def _walk(item):
    """Every layout object under ``item``, except the characters of a text line."""
    from pdfminer.layout import LTTextLine

    for child in item:
        yield child
        if hasattr(child, "__iter__") and not isinstance(child, LTTextLine):
            yield from _walk(child)


def classify_pages(pdf: bytes) -> list[PageKind]:
    """What each page is: text, scan, scan_with_layer, image or blank. The document is
    parsed once. "blank" means no text and no image: such a page may still show text drawn
    as shapes, so the import renders it to be sure. Runs in the caller's process; call it
    through ``run_isolated``."""
    from pdfminer.high_level import extract_pages

    count_pages(pdf)  # refuses an unreadable, encrypted or over-long PDF
    return [_classify(layout) for layout in extract_pages(io.BytesIO(pdf))]


def _classify(layout) -> PageKind:
    from pdfminer.layout import LTImage, LTTextLine

    page_area = max(layout.width * layout.height, 1.0)
    text: list[str] = []
    image_area = 0.0
    for item in _walk(layout):
        if isinstance(item, LTTextLine) and item.get_text().strip():
            text.append(item.get_text())
        elif isinstance(item, LTImage):
            image_area += max(item.width, 0) * max(item.height, 0)
    share = image_area / page_area
    if text and not _decodes("".join(text)):
        return "scan"  # its text layer is not text: the page needs OCR
    has_text = bool(text)
    if has_text:
        return "scan_with_layer" if share >= _WIDE else "text"
    if share >= _SCAN:  # a scan, maybe with a picture on it: the engines keep pictures
        return "scan"
    return "image" if image_area > 0 else "blank"


_CID = re.compile(r"\(cid:\d+\)")
_BAD_SHARE = 0.1  # more undecodable characters than this, and the layer is not text
_LETTER_SHARE = 0.2  # fewer letters than this, and the layer is not text
_JUDGED = 20  # the letter share is judged from this many characters on (not a lone "12")


def _decodes(text: str) -> bool:
    """Whether a page's text layer is text: few glyphs pdfminer could not map (``(cid:NN)``),
    replacement, private-use or control characters, and enough letters."""
    cids = len(_CID.findall(text))
    chars = [c for c in _CID.sub("", text) if not c.isspace()]
    total = cids + len(chars)
    if total == 0:
        return True
    bad = cids + sum(1 for c in chars if c == "\ufffd" or unicodedata.category(c) in ("Co", "Cc"))
    if bad > _BAD_SHARE * total:
        return False
    letters = sum(1 for c in chars if c.isalpha())
    return total < _JUDGED or letters >= _LETTER_SHARE * total


def read_text_page(pdf: bytes, page: int) -> PageResult:
    """The blocks of a text page (0-based ``page``): headings and paragraphs, with italic
    and bold runs. Raises PageReadError for a page the PDF does not have. Runs in the
    caller's process; call it through ``run_isolated``."""
    from pdfminer.layout import LTTextLine

    layout = _layout(pdf, page)
    height, width = layout.height, layout.width
    lines = [
        _Line(item, height, width)
        for item in _walk(layout)
        if isinstance(item, LTTextLine) and item.get_text().strip()
    ]
    lines.sort(key=lambda line: (-round(line.item.y1), line.item.x0))
    if not lines:
        return PageResult(page, "text", [])
    body = _body_size(lines)
    blocks = [_block(group, body, width, height) for group in _paragraphs(lines)]
    return PageResult(page, "text", blocks)


class _Line:
    def __init__(self, item, page_height: float, page_width: float):
        from pdfminer.layout import LTChar

        self.item = item
        self.chars = [c for c in item if isinstance(c, LTChar)]
        sizes = [c.size for c in self.chars if c.get_text().strip()] or [c.size for c in self.chars]
        self.size = statistics.median(sizes) if sizes else 0.0
        self.top = 1 - item.y1 / page_height
        self.bottom = 1 - item.y0 / page_height
        self.center = (item.x0 + item.x1) / 2 / page_width


def _body_size(lines: list[_Line]) -> float:
    """The most common font size, counted in characters."""
    weight: Counter[float] = Counter()
    for line in lines:
        weight[round(line.size, 1)] += len(line.item.get_text().strip())
    return weight.most_common(1)[0][0]


def _paragraphs(lines: list[_Line]) -> list[list[_Line]]:
    """Group lines into paragraphs by line gap, indent and change of size."""
    left = min(line.item.x0 for line in lines)
    groups: list[list[_Line]] = [[lines[0]]]
    for previous, line in zip(lines, lines[1:], strict=False):
        gap = previous.item.y0 - line.item.y1
        size = max(previous.size, line.size)
        new = (
            gap > 0.8 * size
            or abs(previous.size - line.size) > 0.5
            or (line.item.x0 - left > 1.0 * size and previous.item.x0 - left <= 1.0 * size)
        )
        if new:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def _block(group: list[_Line], body: float, page_width: float, page_height: float) -> PageBlock:
    size = statistics.median(line.size for line in group)
    text_len = sum(len(line.item.get_text().strip()) for line in group)
    centered = all(abs(line.center - 0.5) <= _CENTER_TOLERANCE for line in group)
    large = size >= _HEADING_RATIO * body
    alone = len(group) == 1 and centered and text_len < _SHORT_LINE
    kind = "heading" if large or alone else "paragraph"
    level = 1
    if kind == "heading":
        ratio = size / body if body else 1.0
        level = 1 if ratio >= 1.6 else 2 if ratio >= 1.3 else 3
        if not large:
            level = 2
    return PageBlock(
        kind=kind,
        runs=_runs(group),
        level=level,
        top=group[0].top,
        bottom=group[-1].bottom,
        size=size,
        centered=centered,
    )


def _runs(group: list[_Line]) -> list[Run]:
    """Runs of one style each. A space between two styles goes to the plainer side, as it
    does in the source (``The <em>ship</em> came``), though the PDF styles it as it likes."""
    chars: list[list] = []  # [text, italic, bold, is_space]
    for i, line in enumerate(group):
        if i:
            chars.append([" ", None, None, True])
        for char in line.chars:
            name = char.fontname or ""
            text = char.get_text()
            italic = "Italic" in name or "Oblique" in name
            bold = "Bold" in name
            chars.append([" " if text.isspace() else text, italic, bold, text.isspace()])
    for i, entry in enumerate(chars):
        if not entry[3]:
            continue
        before = next((c for c in reversed(chars[:i]) if not c[3]), None)
        after = next((c for c in chars[i + 1 :] if not c[3]), None)
        sides = [c for c in (before, after) if c is not None]
        if not sides:
            entry[1] = entry[2] = False
            continue
        plain = min(sides, key=lambda c: (c[1], c[2]))
        entry[1], entry[2] = plain[1], plain[2]
    runs: list[Run] = []
    for text, italic, bold, _ in chars:
        if runs and runs[-1].italic == italic and runs[-1].bold == bold:
            runs[-1].text += text
        else:
            runs.append(Run(text, italic, bold))
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    return [r for r in runs if r.text]


# --- isolation ------------------------------------------------------------------------


def run_isolated(fn, *args, timeout: float = 120, memory_mb: int = 1500):
    """Call ``fn(*args)`` in a ``spawn`` child with a time limit and (on Linux) a memory
    cap, and return its result. ``fn`` and its arguments and result must be picklable.
    Raises PageReadError on a timeout, a crash or an exception."""
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    child = ctx.Process(target=_child, args=(fn, args, memory_mb * 1024**2, results), daemon=True)
    child.start()
    deadline = time.monotonic() + timeout
    message = None
    try:
        while message is None:
            try:
                message = results.get(timeout=0.2)
            except queue_module.Empty:
                if not child.is_alive():
                    try:  # it may have answered just before it exited
                        message = results.get(timeout=1)
                    except queue_module.Empty:
                        raise PageReadError(
                            f"The reader stopped (exit code {child.exitcode})"
                        ) from None
                elif time.monotonic() > deadline:
                    raise PageReadError(f"Reading took longer than {timeout:g} seconds") from None
    finally:
        if child.is_alive():
            child.kill()
        child.join()
    status, value = message
    if status != "ok":
        raise PageReadError(value)
    return value


def _child(fn, args, memory: int, results) -> None:
    try:
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        results.put(("ok", fn(*args)))
    except MemoryError:
        results.put(("error", "The page needs more memory than reading may use"))
    except BaseException as exc:  # noqa: BLE001 - everything goes back to the parent
        lines = traceback.format_exception_only(type(exc), exc)
        results.put(("error", lines[-1].strip() if lines else repr(exc)))
