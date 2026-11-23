"""A block's translation, as the export writers take it."""

from dataclasses import dataclass

from jerome.documents.model import Mark


@dataclass(frozen=True)
class TranslatedBlock:
    """The translated text and marks of one block, in the readers' terms.

    ``placeholders[i]`` is the 0-based index of the source placeholder that the i-th U+FFFC
    of ``text`` stands for; None means the placeholders keep their source order.
    """

    text: str
    marks: list[Mark]
    placeholders: list[int] | None
