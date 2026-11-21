"""Patch an EPUB in place with changes to its blocks.

The book is never regenerated. Only the text nodes a change touches are rewritten, and only
content documents with changes are re-serialized, the way they were parsed (XML, or HTML
for the lxml.html fallback). Those keep their original bytes up to the root element (BOM,
XML declaration, comments, doctype) and their encoding. Every other zip entry keeps its
bytes, its place in the zip order and its compression.

Block offsets map back to raw node text through the pieces ``element_text`` gives:

- A replacement goes into the node holding the first replaced character, so it takes that
  character's formatting. A pure insertion goes right after the character before it (into
  the tail of a placeholder or entity reference), or before the first character at 0.
- Whitespace that is not block text (leading, trailing, or collapsed into a space) stays,
  except whitespace collapsed into a space that a change removes or puts text right after.
- An entity reference a change covers becomes text; the others stay references.
- An inline element a change empties is removed, unless it has an ``id`` or ``name``
  (it may be a link target).
"""

import codecs
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html

from jerome.documents.changes import Change, validate_changes
from jerome.documents.docx_xml import ParagraphText, write_package
from jerome.documents.epub_xml import (
    ContentDocument,
    EpubPackage,
    EpubPiece,
    content_documents,
    element_text,
    is_block_text,
    iter_epub_blocks,
    parse_content_document,
    read_container,
)
from jerome.errors import InvalidChangeError, UnsupportedDocumentError

_XML_WS = " \t\n\r"
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

# (element, "text" | "tail", raw_start, raw_end, new text)
_Edit = tuple[etree._Element, str, int, int, str]


def write_epub(original: bytes, changes: Mapping[str, Sequence[Change]]) -> bytes:
    """Apply ``changes`` (block key -> changes to its text) to the EPUB ``original``.

    Offsets are those of the block text ``read_epub`` gives. Raises InvalidChangeError,
    before changing anything, for a key that names no block and for changes that
    ``validate_changes`` refuses or whose replacement is not XML text.
    """
    package = read_container(original)
    blocks, docs = _find_blocks(package, set(changes))
    ordered = {key: _validate(blocks[key][1], key, cs) for key, cs in changes.items()}
    dirty: set[str] = set()
    for key, cs in ordered.items():
        if cs:
            element, text = blocks[key]
            _patch(element, text.pieces, cs)  # type: ignore[arg-type]
            dirty.add(key.partition("#")[0])
    for path in dirty:  # only these are re-serialized
        package.files[path] = _serialize(docs[path], package.files[path], path)
    return write_package(package.files, original)


def _find_blocks(
    package: EpubPackage, keys: set[str]
) -> tuple[dict[str, tuple[etree._Element, ParagraphText]], dict[str, ContentDocument]]:
    """The ``(element, text)`` of each block in ``keys``, and the documents holding them."""
    paths = {key.partition("#")[0] for key in keys}
    docs: dict[str, ContentDocument] = {}
    blocks: dict[str, tuple[etree._Element, ParagraphText]] = {}
    for item in content_documents(package):
        if item.path not in paths or item.path in docs:
            continue
        doc = docs[item.path] = parse_content_document(package.files[item.path], item.path)
        for n, el in iter_epub_blocks(doc.root):
            key = f"{item.path}#{n}"
            if key in keys and is_block_text((text := element_text(el)).text):
                blocks[key] = (el, text)
    if missing := keys - blocks.keys():
        raise InvalidChangeError(f"No block with key {min(missing)!r}")
    return blocks, docs


def _validate(text: ParagraphText, key: str, changes: Sequence[Change]) -> list[Change]:
    """Sorted changes, without the ones that change nothing (empty insertions)."""
    ordered = [c for c in validate_changes(text.text, changes) if c.start < c.end or c.replacement]
    for c in ordered:
        if _NOT_XML.search(c.replacement):
            raise InvalidChangeError(f"{key}: change ({c.start}, {c.end}) has non-XML characters")
    return ordered


# --- patching -------------------------------------------------------------------------


@dataclass
class _Run:
    """Block characters ``[start, end)``: text at ``spans`` of one node's raw string, or,
    when ``where`` is "object", a placeholder or an entity reference left as it is."""

    start: int
    end: int
    element: etree._Element
    where: str  # "text", "tail" or "object"
    spans: list[tuple[int, int]] = field(default_factory=list)
    collapsed: list[tuple[etree._Element, str, int, int]] = field(default_factory=list)


def _patch(block: etree._Element, pieces: list[EpubPiece], changes: list[Change]) -> None:
    """Apply sorted changes to one block, right to left so offsets stay valid."""
    runs = _runs(pieces, changes)
    edited: list[tuple[etree._Element, str]] = []
    cleared: set[int] = set()  # runs whose collapsed whitespace is already removed
    for c in reversed(changes):
        # Within a node, from the end, so the offsets of the other edits stay valid.
        for el, where, a, b, new in sorted(_edits(runs, c, cleared), key=lambda e: -e[2]):
            raw = _get(el, where)
            _set(el, where, raw[:a] + new + raw[b:])
            edited.append((el, where))
    _remove_emptied(block, edited)


def _runs(pieces: list[EpubPiece], changes: list[Change]) -> list[_Run]:
    """The pieces as runs, after turning every entity reference a change covers into text.

    Such an entity's characters, then its tail, are appended to the text just before it,
    so the runs from it and from its tail move there.
    """
    moved: dict[tuple[etree._Element, str], tuple[etree._Element, str, int]] = {}
    for p in pieces:  # in document order
        if p.where == "entity" and any(c.start < p.end and p.start < c.end for c in changes):
            entity = p.element
            prev = entity.getprevious()
            el, where = (prev, "tail") if prev is not None else (entity.getparent(), "text")
            before = _get(el, where)
            _set(el, where, before + p.raw + (entity.tail or ""))
            moved[(entity, "entity")] = (el, where, len(before))
            moved[(entity, "tail")] = (el, where, len(before) + len(p.raw))
            entity.tail = None
            entity.getparent().remove(entity)

    def at(el: etree._Element, where: str, a: int, b: int) -> tuple[etree._Element, str, int, int]:
        el, where, shift = moved.get((el, where), (el, where, 0))
        return el, where, a + shift, b + shift

    runs = []
    for p in pieces:
        if p.is_text:
            spans = [at(p.element, p.where, a, b) for a, b in p.spans]
        elif (p.element, "entity") in moved:
            spans = [at(p.element, "entity", i, i + 1) for i in range(p.end - p.start)]
        else:
            runs.append(_Run(p.start, p.end, p.element, "object"))
            continue
        el, where = spans[0][:2]
        collapsed = [at(*c) for c in p.collapsed]
        runs.append(_Run(p.start, p.end, el, where, [s[2:] for s in spans], collapsed))
    return runs


def _edits(runs: list[_Run], c: Change, cleared: set[int]) -> list[_Edit]:
    """The raw text edits that make change ``c``."""
    if c.start == c.end:
        el, where, at = _insertion_point(runs, c.start)
        edits: list[_Edit] = [(el, where, at, at, c.replacement)]
        if c.start > 0 and (before := _run_at(runs, c.start - 1)).end == c.start:
            edits += _clear(before, cleared)
        return edits
    edits = []
    covered = [r for r in runs if r.start < c.end and c.start < r.end]  # all text runs
    for i, run in enumerate(covered):
        a, b = max(c.start, run.start) - run.start, min(c.end, run.end) - run.start
        new = c.replacement if i == 0 else ""
        edits.append((run.element, run.where, run.spans[a][0], run.spans[b - 1][1], new))
        if run.start + b == run.end:
            edits += _clear(run, cleared)
    return edits


def _insertion_point(runs: list[_Run], pos: int) -> tuple[etree._Element, str, int]:
    """``(element, where, raw offset)`` for text inserted at block offset ``pos``."""
    if pos > 0:
        run = _run_at(runs, pos - 1)
        if run.where == "object":  # after a placeholder or entity: at the start of its tail
            return run.element, "tail", 0
        return run.element, run.where, run.spans[pos - 1 - run.start][1]
    run = runs[0]
    if run.where != "object":
        return run.element, run.where, run.spans[0][0]
    prev = run.element.getprevious()  # the block starts with an object: just before it
    if prev is not None:
        return prev, "tail", len(prev.tail or "")
    parent = run.element.getparent()
    return parent, "text", len(parent.text or "")


def _run_at(runs: list[_Run], pos: int) -> _Run:
    return next(r for r in runs if r.start <= pos < r.end)


def _clear(run: _Run, cleared: set[int]) -> list[_Edit]:
    """Edits removing the whitespace that collapsed into the space ending ``run``, once."""
    if not run.collapsed or id(run) in cleared:
        return []
    cleared.add(id(run))
    return [(el, where, a, b, "") for el, where, a, b in run.collapsed]


def _get(el: etree._Element, where: str) -> str:
    return (el.text if where == "text" else el.tail) or ""


def _set(el: etree._Element, where: str, value: str) -> None:
    if where == "text":
        el.text = value  # "" rather than None: an element left empty is not self-closed
    else:
        el.tail = value or None


def _remove_emptied(block: etree._Element, edited: list[tuple[etree._Element, str]]) -> None:
    """Remove the inline elements that edits left empty, and ancestors left empty in turn,
    unless they have an ``id`` or ``name``. Elements that were empty already are kept."""
    for el, where in edited:
        target = el if where == "text" else el.getparent()
        while (
            target is not None
            and target is not block
            and target.getparent() is not None
            and not target.text
            and len(target) == 0
            and target.get("id") is None
            and target.get("name") is None
        ):
            parent = target.getparent()
            _remove(target)
            target = parent


def _remove(el: etree._Element) -> None:
    """Remove ``el``, keeping its tail text in place."""
    parent, prev = el.getparent(), el.getprevious()
    if el.tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


# --- serialization --------------------------------------------------------------------

_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_COMMENT_OR_PI = re.compile(r"<!--.*?-->|<\?.*?\?>", re.S)
_DOCTYPE = re.compile("<!DOCTYPE", re.I)


def _serialize(doc: ContentDocument, original: bytes, path: str) -> bytes:
    """The patched document: the original's bytes up to its root element, then the root
    and what follows it serialized as parsed, then the original's trailing whitespace."""
    codec = _codec(doc, original, path)
    tostring = lxml_html.tostring if doc.parser == "html" else etree.tostring
    nodes = (doc.root, *doc.root.itersiblings())
    body = "".join(tostring(node, encoding="unicode") for node in nodes)
    try:
        text = original.decode(codec, "surrogateescape")  # a BOM stays, as U+FEFF
        start, end = _root_start(text), len(text.rstrip(_XML_WS))
        return (
            text[:start].encode(codec, "surrogateescape")
            + body.encode(codec, "xmlcharrefreplace")  # character references if need be
            + text[max(start, end) :].encode(codec, "surrogateescape")
        )
    except UnicodeError as exc:
        raise UnsupportedDocumentError(f"{path} cannot be written back as {codec}") from exc


def _codec(doc: ContentDocument, original: bytes, path: str) -> str:
    """The original's encoding: from its BOM, else the one it was parsed with."""
    for bom, codec in _BOMS:
        if original.startswith(bom):
            return codec
    name = doc.root.getroottree().docinfo.encoding or "utf-8"
    try:
        return codecs.lookup(name).name
    except LookupError as exc:
        raise UnsupportedDocumentError(f"{path} is in an unknown encoding: {name}") from exc


def _root_start(text: str) -> int:
    """Where the root element starts: after a BOM, and the XML declaration, comments,
    processing instructions, doctype and whitespace before the root."""
    pos = 1 if text.startswith("﻿") else 0
    while True:
        while pos < len(text) and text[pos] in _XML_WS:
            pos += 1
        if m := _COMMENT_OR_PI.match(text, pos):
            pos = m.end()
        elif _DOCTYPE.match(text, pos) and (end := _doctype_end(text, pos)):
            pos = end
        else:
            return pos


def _doctype_end(text: str, pos: int) -> int | None:
    """The end of the doctype at ``pos``, or None if it has none. Quoted strings, and
    comments and processing instructions in the internal subset, may hold ``>`` or ``]``."""
    i, subset = pos + len("<!DOCTYPE"), False
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = text.find(ch, i + 1)
            if i < 0:
                return None
        elif subset and (m := _COMMENT_OR_PI.match(text, i)):
            i = m.end() - 1
        elif ch == "[":
            subset = True
        elif ch == "]":
            subset = False
        elif ch == ">" and not subset:
            return i + 1
        i += 1
    return None
