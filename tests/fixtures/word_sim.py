"""A stand-in for an editor working in Word on a Jerome review copy.

``simulate_word_review`` takes the exported DOCX and applies what an editor does in Word:
accepts or rejects Jerome's tracked changes, edits text with or without tracking, deletes
paragraphs, and saves (which renumbers every revision, comment and bookmark id).
"""

import random
from collections.abc import Iterable, Mapping

from lxml import etree

from jerome.documents.docx_xml import parse_xml, qn, read_package, write_package

_JEROME = "Jerome"
_DATE = "2026-11-22T09:00:00Z"
_P, _R, _T, _DEL_TEXT = qn("w:p"), qn("w:r"), qn("w:t"), qn("w:delText")
_INS, _DEL = qn("w:ins"), qn("w:del")
_ID, _AUTHOR, _RPR = qn("w:id"), qn("w:author"), qn("w:rPr")
_COMMENTS_PART = "word/comments.xml"
# Elements whose w:id is renumbered, by id space: Word keeps these apart.
_ID_SPACES = {
    "revision": [qn(f"w:{t}") for t in "ins del moveFrom moveTo rPrChange pPrChange".split()],
    "comment": [
        qn(f"w:{t}") for t in "comment commentRangeStart commentRangeEnd commentReference".split()
    ],
    "bookmark": [qn("w:bookmarkStart"), qn("w:bookmarkEnd")],
}


def simulate_word_review(
    data: bytes,
    *,
    accept: Iterable[str] = (),
    reject: Iterable[str] = (),
    untracked_edits: Mapping[str, str] = {},  # noqa: B006 - never mutated
    tracked_edits: Mapping[str, str] = {},  # noqa: B006 - never mutated
    delete_paragraphs_containing: Iterable[str] = (),
    renumber_ids: bool = True,
    editor: str = "Editor",
) -> bytes:
    """The DOCX after the editor's session.

    - ``accept`` / ``reject``: Jerome changes, named by the text they insert (or, for a
      pure deletion, the text they delete); the whole change is accepted or rejected.
    - ``untracked_edits``: old text -> new text, replaced in visible runs without tracking.
    - ``tracked_edits``: the same, as a deletion and insertion by ``editor``.
    - ``delete_paragraphs_containing``: paragraphs removed outright, untracked.
    - ``renumber_ids``: remap every revision, comment and bookmark id, as Word does on save.
    """
    package = read_package(data)
    trees = {
        part: parse_xml(xml, part)
        for part, xml in package.items()
        if part.startswith("word/") and part.count("/") == 1 and part.endswith(".xml")
    }
    accept, reject = set(accept), set(reject)
    ids = _FreshIds(trees)
    applied: set[str] = set()
    for part, tree in trees.items():
        if part == _COMMENTS_PART:
            continue
        for p in list(tree.getroot().iter(_P)):
            for change in _jerome_changes(p):
                key = _change_key(change)
                if key in accept:
                    _resolve(change, accept=True)
                elif key in reject:
                    _resolve(change, accept=False)
            for old, new in tracked_edits.items():
                _tracked_edit(p, old, new, editor, ids)
        applied |= _untracked_edits(tree.getroot(), untracked_edits)
        _delete_paragraphs(tree.getroot(), set(delete_paragraphs_containing))
    if missing := set(untracked_edits) - applied:
        raise ValueError(f"untracked edit text not found in a visible run: {sorted(missing)}")
    if renumber_ids:
        _renumber(trees)
    for part, tree in trees.items():
        package[part] = etree.tostring(
            tree, xml_declaration=True, encoding="UTF-8", standalone=True
        )
    return write_package(package, data)


# --- Jerome's changes -----------------------------------------------------------------


def _jerome_changes(p: etree._Element) -> list[list[etree._Element]]:
    """Jerome's revisions in ``p`` grouped into changes: ``w:del``s, then their ``w:ins``.

    A change crossing a container (a hyperlink) is several ``w:del`` siblings, so a group is
    every ``w:del`` up to and including the next ``w:ins``.
    """
    changes: list[list[etree._Element]] = []
    open_change = False  # the last group may still take dels
    for el in p.iter(_DEL, _INS):
        if el.get(_AUTHOR) != _JEROME:
            continue
        if el.tag == _DEL and open_change:
            changes[-1].append(el)
            continue
        if el.tag == _DEL:
            changes.append([el])
            open_change = True
        else:
            if open_change:
                changes[-1].append(el)
            else:
                changes.append([el])
            open_change = False
    return changes


def _change_key(change: list[etree._Element]) -> str:
    inserted = "".join("".join(el.itertext()) for el in change if el.tag == _INS)
    return inserted or "".join(t.text or "" for el in change for t in el.iter(_DEL_TEXT))


def _resolve(change: list[etree._Element], *, accept: bool) -> None:
    for el in change:
        keep = (el.tag == _INS) == accept
        if keep:
            for t in el.iter(_DEL_TEXT):
                t.tag = _T
            _unwrap(el)
        else:
            el.getparent().remove(el)


def _unwrap(el: etree._Element) -> None:
    parent = el.getparent()
    index = parent.index(el)
    for child in list(el):
        parent.insert(index, child)
        index += 1
    parent.remove(el)


# --- the editor's own edits -----------------------------------------------------------


def _untracked_edits(root: etree._Element, edits: Mapping[str, str]) -> set[str]:
    """Replace in every visible ``w:t``; return the edits that found their text."""
    applied: set[str] = set()
    for t in root.iter(_T):
        for old, new in edits.items():
            if t.text and old in t.text:
                t.text = t.text.replace(old, new)
                applied.add(old)
    return applied


def _tracked_edit(p: etree._Element, old: str, new: str, editor: str, ids: "_FreshIds") -> None:
    """Replace the first plain run holding ``old``: split it around a w:del and a w:ins."""
    for r in p.iter(_R):
        t = r.find(_T)
        if t is None or t.text is None or old not in t.text or len(r.findall(_T)) != 1:
            continue
        if r.getparent().tag in (_INS, _DEL):
            continue
        before, _, after = t.text.partition(old)
        rpr = r.find(_RPR)
        deleted = _revision(_DEL, editor, ids, [_run(rpr, old, _DEL_TEXT)])
        pieces = [deleted, _revision(_INS, editor, ids, [_run(rpr, new, _T)])]
        if before:
            pieces.insert(0, _run(rpr, before, _T))
        if after:
            pieces.append(_run(rpr, after, _T))
        parent = r.getparent()
        index = parent.index(r)
        parent.remove(r)
        for piece in pieces:
            parent.insert(index, piece)
            index += 1
        return


def _run(rpr: etree._Element | None, text: str, tag: str) -> etree._Element:
    r = etree.Element(_R)
    if rpr is not None:
        r.append(etree.fromstring(etree.tostring(rpr)))
    t = etree.SubElement(r, tag)
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _revision(tag: str, author: str, ids: "_FreshIds", content: list[etree._Element]):
    el = etree.Element(tag, {_ID: str(ids.next()), _AUTHOR: author, qn("w:date"): _DATE})
    el.extend(content)
    return el


def _delete_paragraphs(root: etree._Element, needles: set[str]) -> None:
    for p in list(root.iter(_P)):
        text = "".join(t.text or "" for t in p.iter(_T, _DEL_TEXT))
        if any(needle in text for needle in needles):
            p.getparent().remove(p)


class _FreshIds:
    """Ids above every revision id already in the document."""

    def __init__(self, trees: Mapping[str, etree._ElementTree]):
        used = [
            int(el.get(_ID))
            for tree in trees.values()
            for el in tree.getroot().iter(*_ID_SPACES["revision"])
            if (el.get(_ID) or "").isdigit()
        ]
        self.value = max(used, default=0)

    def next(self) -> int:
        self.value += 1
        return self.value


# --- saving ---------------------------------------------------------------------------


def _renumber(trees: Mapping[str, etree._ElementTree]) -> None:
    """Remap each id space to new ids, by a fixed shuffle so every id really changes."""
    for space, tags in _ID_SPACES.items():
        elements = [el for tree in trees.values() for el in tree.getroot().iter(*tags)]
        old_ids = sorted({el.get(_ID) for el in elements if el.get(_ID)}, key=int)
        new_ids = [str(1000 + n) for n in range(len(old_ids))]
        random.Random(space).shuffle(new_ids)
        mapping = dict(zip(old_ids, new_ids, strict=True))
        for el in elements:
            if el.get(_ID):
                el.set(_ID, mapping[el.get(_ID)])
