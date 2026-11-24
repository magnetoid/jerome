import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

PLACEHOLDER = "￼"


def title_text(text: str) -> str:
    """A heading's text as a chapter title: inline objects dropped, whitespace collapsed."""
    return " ".join(text.replace(PLACEHOLDER, " ").split())


class MarkKind(StrEnum):
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    SMALLCAPS = "smallcaps"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"
    STRIKE = "strike"
    LINK = "link"


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE_CELL = "table_cell"
    FOOTNOTE = "footnote"


@dataclass
class Mark:
    start: int
    end: int
    kind: MarkKind
    href: str | None = None


@dataclass
class ParsedBlock:
    key: str
    kind: BlockKind
    text: str
    marks: list[Mark] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ParsedChapter:
    title: str
    proofread: bool
    source_ref: str
    blocks: list[ParsedBlock] = field(default_factory=list)


@dataclass
class ParsedBook:
    format: Literal["docx", "epub"]
    title: str | None
    language: str | None
    chapters: list[ParsedChapter] = field(default_factory=list)
