import re
from collections.abc import Sequence
from dataclasses import dataclass

from jerome.documents.model import PLACEHOLDER
from jerome.errors import InvalidChangeError

# characters XML 1.0 cannot carry
NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def check_replacement_text(text: str) -> None:
    """Raise InvalidChangeError if ``text`` cannot be written into a document.

    It must not hold the placeholder (a non-text object) or a character XML 1.0 forbids.
    Tab, line feed and carriage return are fine.
    """
    if PLACEHOLDER in text:
        raise InvalidChangeError("replacement text contains the object placeholder (U+FFFC)")
    if NOT_XML.search(text):
        raise InvalidChangeError("replacement text contains characters not allowed in XML")


@dataclass(frozen=True)
class Change:
    start: int
    end: int
    replacement: str
    ref: str | None = None
    comment: str | None = None
    author: str = "Jerome"


def validate_changes(text: str, changes: Sequence[Change]) -> list[Change]:
    """Return the changes sorted by position, or raise InvalidChangeError."""
    ordered = sorted(changes, key=lambda c: (c.start, c.end))
    for c in ordered:
        if not 0 <= c.start <= c.end <= len(text):
            raise InvalidChangeError(
                f"change ({c.start}, {c.end}) is out of range for text of length {len(text)}"
            )
        if PLACEHOLDER in text[c.start : c.end]:
            raise InvalidChangeError(f"change ({c.start}, {c.end}) covers a placeholder")
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        if nxt.start < prev.end or (nxt.start == prev.start and prev.start == prev.end == nxt.end):
            raise InvalidChangeError(
                f"changes ({prev.start}, {prev.end}) and ({nxt.start}, {nxt.end}) overlap"
            )
    return ordered


def apply_changes(text: str, changes: Sequence[Change]) -> str:
    """Apply validated changes right to left so earlier offsets stay valid."""
    for c in reversed(validate_changes(text, changes)):
        text = text[: c.start] + c.replacement + text[c.end :]
    return text
