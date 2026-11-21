"""Run-level surgery for the DOCX writer.

Pieces come from ``docx_xml.iter_paragraph_runs``. Every split keeps the left part in the
original elements and moves the rest into new ones, so pieces to the left of a split stay
valid; the writer relies on that when it applies a paragraph's changes right to left.
"""

from copy import deepcopy

from lxml import etree

from jerome.documents.docx_xml import ParagraphText, Piece, qn

_R = qn("w:r")
_T = qn("w:t")
_RPR = qn("w:rPr")
_ID = qn("w:id")
_FLD_CHAR = qn("w:fldChar")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Elements whose w:id is in the revision id space. One counter covers all story parts.
REVISIONS = tuple(
    qn(f"w:{tag}")
    for tag in (
        "ins del moveFrom moveTo moveFromRangeStart moveFromRangeEnd moveToRangeStart"
        " moveToRangeEnd rPrChange pPrChange sectPrChange tblPrChange tblPrExChange"
        " tblGridChange trPrChange tcPrChange numberingChange cellIns cellDel cellMerge"
        " customXmlInsRangeStart customXmlInsRangeEnd customXmlDelRangeStart"
        " customXmlDelRangeEnd customXmlMoveFromRangeStart customXmlMoveFromRangeEnd"
        " customXmlMoveToRangeStart customXmlMoveToRangeEnd"
    ).split()
)
# Siblings that may sit between deleted runs inside one w:del: they hold no visible text.
_NEUTRAL = {_R} | {
    qn(f"w:{tag}")
    for tag in (
        "proofErr bookmarkStart bookmarkEnd commentRangeStart commentRangeEnd permStart permEnd"
    ).split()
}


def isolate(pieces: list[Piece], start: int, end: int) -> list[etree._Element]:
    """Split runs so that text ``[start, end)`` fills whole runs; return those runs in order.

    The range holds text only (``validate_changes`` refuses placeholders). Splits keep the
    left part in the original elements, so pieces left of ``start`` stay valid.
    """
    covered = [p for p in pieces if p.start < end and start < p.end]
    cut(covered[-1].node, end - covered[-1].start)
    runs = [cut(covered[0].node, start - covered[0].start)]
    for piece in covered[1:]:
        run = piece.node.getparent()
        if run is not runs[-1]:
            runs.append(run)
    return runs


def start_of(piece: Piece) -> etree._Element:
    """The element that the piece's content starts with, splitting its run if needed."""
    parent = piece.node.getparent()
    if parent is not None and parent.tag == _R:  # text, an in-run object, or fldChar begin
        return cut(piece.node, 0)
    return piece.run  # a paragraph-level object


def end_of(piece: Piece, offset: int) -> etree._Element:
    """The element that the text before ``offset`` (in or at the end of piece) ends with."""
    if piece.is_text:
        node, k = piece.node, offset - piece.start
    elif piece.node.tag in (_FLD_CHAR, _R):  # a complex field: ends at a fldChar in last_run
        node, k = next(reversed(piece.last_run.findall(_FLD_CHAR)), None), 1
        if node is None:
            return piece.last_run
    elif piece.node.getparent().tag == _R:  # an in-run object
        node, k = piece.node, 1
    else:  # a paragraph-level object
        return piece.run
    cut(node, k)
    return node.getparent()


def cut(node: etree._Element, k: int) -> etree._Element | None:
    """Split the run holding ``node`` so that a run starts at character ``k`` of it.

    A w:t counts its characters; any other run child is one character wide. Content from
    the cut on moves to a new run right after, with a copy of the run's properties. Returns
    the run starting at the cut (the original when nothing precedes it), or None when
    nothing follows it.
    """
    run = node.getparent()
    content = [child for child in run if child.tag != _RPR]
    i = content.index(node)
    text = node.text or "" if node.tag == _T else None
    if k <= 0:
        moved = content[i:]
    elif k >= (1 if text is None else len(text)):
        moved = content[i + 1 :]
    else:
        rest = etree.Element(_T)
        rest.text, node.text = text[k:], text[:k]
        preserve(node)
        preserve(rest)
        moved = [rest, *content[i + 1 :]]
    if not moved:
        return None
    if moved[0] is content[0]:
        return run
    new = etree.Element(_R, dict(run.attrib))
    rpr = run.find(_RPR)
    if rpr is not None:
        new.append(copy_rpr(rpr))
    new.extend(moved)
    run.addnext(new)
    return new


def deletion_groups(runs: list[etree._Element]) -> list[list[etree._Element]]:
    """Consecutive sibling ranges to wrap in one w:del each, from first to last run."""
    groups = [[runs[0]]]
    for run in runs[1:]:
        prev = groups[-1][-1]
        if run.getparent() is prev.getparent():
            between = []
            for sibling in prev.itersiblings():
                if sibling is run:
                    break
                between.append(sibling)
            if all(sibling.tag in _NEUTRAL for sibling in between):
                groups[-1] += [*between, run]
                continue
        groups.append([run])
    return groups


def remove_text(runs: list[etree._Element]) -> None:
    """Remove the runs' text, and the runs left with nothing but properties."""
    for run in runs:
        for t in run.findall(_T):
            run.remove(t)
        if all(child.tag == _RPR for child in run):
            run.getparent().remove(run)


def insertion_rpr(pt: ParagraphText, offset: int) -> etree._Element | None:
    """Run properties for text inserted at ``offset``: those of the character before it.

    At 0, those of the first piece. A placeholder's run loses its character style and
    vertical alignment (e.g. a note reference's superscript); a placeholder outside any
    run lends the nearest text run's properties instead.
    """
    piece = piece_at(pt, offset - 1) if offset else pt.pieces[0]
    if piece.is_text:
        return piece.node.getparent().find(_RPR)
    run = piece.last_run if offset else piece.run
    if run.tag != _R:
        texts = [p for p in pt.pieces if p.is_text]
        before = [p for p in texts if p.end <= offset]
        return (before[-1] if before else texts[0]).node.getparent().find(_RPR)
    rpr = copy_rpr(run.find(_RPR))
    if rpr is not None:
        for prop in rpr.findall(qn("w:rStyle")) + rpr.findall(qn("w:vertAlign")):
            rpr.remove(prop)
    return rpr


def piece_at(pt: ParagraphText, offset: int) -> Piece:
    return next(p for p in pt.pieces if p.start <= offset < p.end)


def text_run(text: str, rpr: etree._Element | None) -> etree._Element:
    run = etree.Element(_R)
    if rpr is not None:
        run.append(copy_rpr(rpr))
    t = etree.SubElement(run, _T)
    t.text = text
    preserve(t)
    return run


def copy_rpr(rpr: etree._Element | None) -> etree._Element | None:
    """A copy of run properties whose revisions (rPrChange) await their own w:id."""
    if rpr is None:
        return None
    copy = deepcopy(rpr)
    for el in copy.iter(*REVISIONS):
        el.set(_ID, "")
    return copy


def preserve(t: etree._Element) -> None:
    t.set(_XML_SPACE, "preserve")
