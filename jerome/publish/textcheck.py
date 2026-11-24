"""The hard stop: the shared form's text must be exactly the reviewed text.

Both sides lose their whitespace and soft hyphens before they are compared (Word line breaks,
block boundaries and HTML spacing do not map one to one). A PLACEHOLDER in the reviewed text
stands for an inline object (a picture, a field, a note reference): it matches up to
``_GAP`` characters of the finished text, or none. Body text and note text are compared as
two streams, each in reading order.
"""

from lxml import etree

from jerome.documents.model import PLACEHOLDER, BlockKind, ParsedBook
from jerome.publish.model import NOTE_CLASS, PubBook, PublishTextError, chapter_labels

_GAP = 200  # characters one inline object may show as (a field result)
_SHY = "­"
_WORDS_BEFORE, _WORDS_AFTER = 4, 8


def check_text(pub: PubBook, parsed: ParsedBook, *, documents: set[str] | None = None) -> None:
    """Raise PublishTextError at the first difference. ``documents`` limits the reviewed
    blocks to those whose key is in one of the given parts (an EPUB's spine paths)."""
    body_blocks, note_blocks = [], []
    for chapter in parsed.chapters:
        for block in chapter.blocks:
            if documents is not None and block.key.partition("#")[0] not in documents:
                continue
            (note_blocks if block.kind == BlockKind.FOOTNOTE else body_blocks).append(block.text)

    labels = chapter_labels(pub)
    body, notes, starts = [], [], []
    for chapter in pub.chapters:
        starts.append(len(_compact(" ".join(body))[0]))
        text, chapter_notes = _texts(chapter.html)
        body.append(text)
        notes += chapter_notes
    _compare(" ".join(body), " ".join(body_blocks), labels, starts)
    _compare(" ".join(notes), " ".join(note_blocks), ["the footnotes"], [0])


def _texts(html: str) -> tuple[str, list[str]]:
    """The fragment's text outside notes, and each note's text."""
    root = etree.fromstring(f"<div>{html}</div>".encode())
    notes: list[str] = []

    def walk(el: etree._Element) -> str:
        parts = [el.text or ""]
        for child in el:
            if isinstance(child.tag, str) and child.get("class") == NOTE_CLASS:
                notes.append("".join(child.itertext()))
            elif isinstance(child.tag, str):
                parts.append(" " + walk(child) + " ")
            parts.append(child.tail or "")
        return "".join(parts)

    return walk(root), notes


def _compact(text: str) -> tuple[str, list[int], str]:
    """(text without whitespace or soft hyphens, each kept char's index in ``norm``, norm)
    where norm is the text with whitespace collapsed."""
    norm = " ".join(text.replace(_SHY, "").split())
    chars, index = [], []
    for i, ch in enumerate(norm):
        if ch != " ":
            chars.append(ch)
            index.append(i)
    return "".join(chars), index, norm


def _compare(ours_text: str, reviewed_text: str, labels: list[str], starts: list[int]) -> None:
    ours, ours_index, ours_norm = _compact(ours_text.replace(PLACEHOLDER, ""))
    reviewed, rev_index, rev_norm = _compact(reviewed_text)
    at = _first_difference(ours, reviewed)
    if at is None:
        return
    o, r = at
    label = labels[0]
    for start, name in zip(starts, labels, strict=True):
        if start <= o:
            label = name
    raise PublishTextError(
        f"The finished file's text differs from the reviewed text near '{label}': "
        f"'{_excerpt(ours_norm, ours_index, o)}' / "
        f"'{_excerpt(rev_norm, rev_index, r).replace(PLACEHOLDER, '[object]')}'"
    )


def _first_difference(ours: str, reviewed: str) -> tuple[int, int] | None:
    """None when they match; else (index in ours, index in reviewed) of the first difference."""
    pos, rpos = 0, 0
    segments = reviewed.split(PLACEHOLDER)
    for i, seg in enumerate(segments):
        if i == 0:
            found = pos if ours.startswith(seg, pos) else -1
        else:
            found = ours.find(seg, pos, pos + _GAP + len(seg)) if seg else pos
        if found < 0:
            start = pos
            if i:  # where the segment most likely starts: its first characters
                probe = seg[:20]
                start = ours.find(probe, pos, pos + _GAP + len(probe))
                if start < 0:
                    return pos, rpos
            common = _common_prefix(ours, start, seg)
            return start + common, rpos + common
        pos = found + len(seg)
        rpos += len(seg) + 1
    last_is_gap = len(segments) > 1 and segments[-1] == ""
    if pos == len(ours) or (last_is_gap and len(ours) - pos <= _GAP):
        return None
    return pos, len(reviewed)


def _common_prefix(a: str, start: int, b: str) -> int:
    """How many characters of ``b`` match ``a`` from ``start``."""
    n = 0
    while start + n < len(a) and n < len(b) and a[start + n] == b[n]:
        n += 1
    return n


def _excerpt(norm: str, index: list[int], at: int) -> str:
    """About 12 words of ``norm`` around compact position ``at``."""
    i = index[at] if at < len(index) else len(norm)
    before = norm[:i].split(" ")
    after = norm[i:].split(" ")
    words = before[-_WORDS_BEFORE - 1 :] if before != [""] else []
    if words and after:
        words[-1] += after[0]
        after = after[1:]
    elif after:
        words, after = [after[0]], after[1:]
    return " ".join(words + after[:_WORDS_AFTER]).strip() or "(end)"
