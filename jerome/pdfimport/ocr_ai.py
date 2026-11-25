"""Reading a rendered page with a vision model (the engine for pages Tesseract cannot read)."""

import io
import re
from decimal import Decimal
from typing import Any

from jerome.errors import UnknownModelPrice
from jerome.models import cost
from jerome.models.base import CompletionResult, ModelOutputError, Provider, SystemPart, Usage
from jerome.models.registry import PassConfig, Registry
from jerome.pdfimport.model import PageBlock, PageResult, Run

_INPUT_TOKENS = 1600  # per page: the image and the prompt
_OUTPUT_TOKENS = 900
_MAX_TOKENS = 8000
_MAX_LEVEL = 6

_BOX = {"type": ["array", "null"], "items": {"type": "number"}}
SCHEMA: dict = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["heading", "paragraph", "footnote", "image"],
                    },
                    "text": {"type": "string"},
                    "level": {"type": "integer"},
                    "note": {"type": ["integer", "null"]},
                    "box": _BOX,
                },
                "required": ["kind", "text", "level", "note", "box"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}

_PROMPT = """\
You read one page of a book from an image and return its content as structured blocks, in \
reading order.

The page is written in {language}. Read it in that language and keep every word as written: \
do not translate, correct or modernise anything.

Blocks:
- "heading": a chapter or section title. "level" is 1 for a chapter title, 2 for a section, \
3 below that.
- "paragraph": a paragraph of body text, joined into one string (no line breaks; join words \
hyphenated across a line end).
- "footnote": a note printed at the foot of the page. "note" is its number.
- "image": a picture, figure or illustration (not a decorative rule). Give no text.
For every block that is not a heading, "level" is 1; for every block that is not a footnote, \
"note" is null.

Inside "text", mark emphasis as *italic* and **bold**, and a footnote reference in the body as \
[^n] where n is the note's number, placed right after the word it follows.

Give every block a "box": [x0, y0, x1, y1] as fractions of the page width and height, from \
the top left corner (0 to 1). Use null only if you cannot place the block.

Leave out running headers, running footers and page numbers. If the page has no text and no \
picture, return no blocks.\
"""

_TOKEN = re.compile(r"(\*\*|\*|\[\^(\d+)\])")


def ai_page(
    provider: Provider, cfg: PassConfig, png: bytes, page: int, language: str
) -> tuple[PageResult, Usage]:
    """Read a page image (``page`` is 0-based) with the ocr model."""
    result = provider.complete(
        model=cfg.model,
        system=[SystemPart(_PROMPT.format(language=language))],
        user=f"Read this page (page {page + 1}) in {language}.",
        schema=SCHEMA,
        max_tokens=_MAX_TOKENS,
        effort=cfg.effort,
        images=[(png, "image/png")],
    )
    return PageResult(page, "ai", _blocks(result, png)), result.usage


def _blocks(result: CompletionResult, png: bytes) -> list[PageBlock]:
    data = result.data
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        raise ModelOutputError(  # the call was billed even though its output is unusable
            "model output is not an object with a 'blocks' list",
            usage=result.usage,
            model=result.model,
            provider=result.provider,
        )
    blocks = []
    for raw in data["blocks"]:
        if isinstance(raw, dict):
            block = _block(raw, png)
            if block is not None:
                blocks.append(block)
    return blocks


def _block(raw: dict, png: bytes) -> PageBlock | None:
    kind = raw.get("kind")
    box = _box(raw.get("box"))
    if kind == "image":
        if box is None:
            return PageBlock("image", image=png, image_type="image/png", top=0.0, bottom=1.0)
        return PageBlock(
            "image", image=_crop(png, box), image_type="image/png", top=box[1], bottom=box[3]
        )
    if kind not in ("heading", "paragraph", "footnote"):
        return None
    text = raw.get("text")
    runs = _runs(text) if isinstance(text, str) else []
    if not runs:
        return None
    level = raw.get("level")
    note = raw.get("note")
    return PageBlock(
        kind,
        runs,
        level=min(max(level, 1), _MAX_LEVEL) if _is_int(level) else 1,
        note=note if kind == "footnote" and _is_int(note) else None,
        top=box[1] if box else 0.5,
        bottom=box[3] if box else 0.5,
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _box(raw: Any) -> tuple[float, float, float, float] | None:
    """A box as four fractions clamped to 0..1, with x0 <= x1 and y0 <= y1."""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in raw):
        return None
    x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in raw)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _crop(png: bytes, box: tuple[float, float, float, float]) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        w, h = image.size
        left, top = int(box[0] * w), int(box[1] * h)
        right, bottom = max(round(box[2] * w), left + 1), max(round(box[3] * h), top + 1)
        out = io.BytesIO()
        image.crop((left, top, min(right, w), min(bottom, h))).save(out, "PNG")
        return out.getvalue()


def _runs(text: str) -> list[Run]:
    """Runs from ``*italic*``, ``**bold**`` and ``[^n]`` (a footnote reference)."""
    runs: list[Run] = []
    italic = bold = False
    position = 0

    def add(piece: str) -> None:
        if piece:
            runs.append(Run(piece, italic, bold))

    for match in _TOKEN.finditer(text):
        add(text[position : match.start()])
        position = match.end()
        mark = match.group(1)
        if mark == "**":
            bold = not bold
        elif mark == "*":
            italic = not italic
        else:
            runs.append(Run("", note=int(match.group(2))))
    add(text[position:])
    return runs


def estimate_ocr(registry: Registry, pages: int) -> Decimal:
    """What reading ``pages`` pages with the ocr model costs, or 0 when its price is unknown."""
    config = registry.settings.models.get("ocr")
    model = config.get("model") if isinstance(config, dict) else None
    if not model:
        return Decimal(0)
    try:
        price = registry.price(model)
    except UnknownModelPrice:
        return Decimal(0)
    return pages * cost(price, Usage(_INPUT_TOKENS, _OUTPUT_TOKENS))
