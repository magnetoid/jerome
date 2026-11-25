"""What reading a PDF page gives: blocks of runs, and the page's result (JSON-able)."""

import base64
from dataclasses import dataclass, field
from typing import Literal

PageKind = Literal["text", "scan", "scan_with_layer", "image", "blank"]


@dataclass
class Run:
    text: str
    italic: bool = False
    bold: bool = False
    note: int | None = None  # a footnote reference: empty text, the reference number


@dataclass
class PageBlock:
    kind: Literal["heading", "paragraph", "footnote", "image"]
    runs: list[Run] = field(default_factory=list)
    level: int = 1
    note: int | None = None  # a footnote's number
    image: bytes | None = None
    image_type: str | None = None
    top: float = 0.0  # fractions of the page height, from the top
    bottom: float = 0.0
    size: float = 0.0
    centered: bool = False


@dataclass
class PageResult:
    page: int
    source: Literal["text", "tesseract", "ai", "image", "blank", "failed"]
    blocks: list[PageBlock] = field(default_factory=list)
    error: str | None = None


def to_json(r: PageResult) -> dict:
    return {
        "page": r.page,
        "source": r.source,
        "error": r.error,
        "blocks": [
            {
                "kind": b.kind,
                "runs": [
                    {"text": x.text, "italic": x.italic, "bold": x.bold, "note": x.note}
                    for x in b.runs
                ],
                "level": b.level,
                "note": b.note,
                "image": base64.b64encode(b.image).decode() if b.image is not None else None,
                "image_type": b.image_type,
                "top": b.top,
                "bottom": b.bottom,
                "size": b.size,
                "centered": b.centered,
            }
            for b in r.blocks
        ],
    }


def from_json(d: dict) -> PageResult:
    blocks = [
        PageBlock(
            kind=b["kind"],
            runs=[
                Run(x["text"], x.get("italic", False), x.get("bold", False), x.get("note"))
                for x in b["runs"]
            ],
            level=b.get("level", 1),
            note=b.get("note"),
            image=base64.b64decode(b["image"]) if b.get("image") is not None else None,
            image_type=b.get("image_type"),
            top=b.get("top", 0.0),
            bottom=b.get("bottom", 0.0),
            size=b.get("size", 0.0),
            centered=b.get("centered", False),
        )
        for b in d["blocks"]
    ]
    return PageResult(d["page"], d["source"], blocks, d.get("error"))
