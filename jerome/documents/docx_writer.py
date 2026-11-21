"""Patch a DOCX in place with changes to its blocks.

The document is never regenerated. Only the runs a change touches are rewritten, so other
paragraphs keep their XML, and parts with nothing to change keep their original bytes.

- ``clean`` applies the changes.
- ``tracked`` records each change as Word revisions (``w:del`` then ``w:ins``), with a Word
  comment around both when the change has one.

Either mode can wrap blocks in bookmarks and set custom document properties.
"""

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from lxml import etree

from jerome.documents.changes import NOT_XML, Change, validate_changes
from jerome.documents.docx_runs import (
    REVISIONS,
    deletion_groups,
    end_of,
    insertion_rpr,
    isolate,
    piece_at,
    preserve,
    remove_text,
    start_of,
    text_run,
)
from jerome.documents.docx_xml import (
    DOCUMENT_PART,
    NS,
    ParagraphText,
    is_block_text,
    iter_paragraph_runs,
    parse_xml,
    qn,
    read_package,
    write_package,
)
from jerome.errors import InvalidChangeError, UnsupportedDocumentError

_R = qn("w:r")
_T = qn("w:t")
_RPR = qn("w:rPr")
_PPR = qn("w:pPr")
_ID = qn("w:id")
_AUTHOR = qn("w:author")
_DATE = qn("w:date")

_BOOKMARKS = (qn("w:bookmarkStart"), qn("w:bookmarkEnd"))
_COMMENTS = tuple(
    qn(f"w:{tag}") for tag in "comment commentRangeStart commentRangeEnd commentReference".split()
)


@dataclass(frozen=True)
class DocxWriteOptions:
    mode: Literal["clean", "tracked"]
    date: datetime  # of revisions and comments
    bookmarks: Mapping[str, str] = field(default_factory=dict)  # block key -> bookmark name
    custom_properties: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("clean", "tracked"):
            raise ValueError(f"unknown DOCX write mode {self.mode!r}")


def write_docx(
    original: bytes, changes: Mapping[str, Sequence[Change]], options: DocxWriteOptions
) -> bytes:
    """Apply ``changes`` (block key -> changes to its text) to the DOCX ``original``.

    Offsets are those of the block text ``read_docx`` gives. Raises InvalidChangeError for
    a key in ``changes`` or ``options.bookmarks`` that names no block, and for changes that
    ``validate_changes`` refuses or that have nowhere to go (an insertion before a field
    continued from the previous paragraph, or after one continuing into the next).
    """
    package = read_package(original)
    blocks, trees = _find_blocks(package, set(changes) | set(options.bookmarks))
    ordered = {key: _validate(blocks[key][1], key, cs) for key, cs in changes.items()}
    if blocks:
        patcher = _Patcher(options, _word_roots(package, trees))
        dirty: set[str] = set()
        for key, (p, pt) in blocks.items():  # in document order, so new ids are too
            part = key.partition("#")[0]
            if ordered.get(key):
                patcher.paragraph(pt, ordered[key])
                dirty.add(part)
            if key in options.bookmarks:
                patcher.bookmark(p, options.bookmarks[key])
                dirty.add(part)
        patcher.number_revisions([trees[part].getroot() for part in trees if part in dirty])
        for part in dirty:  # only these are re-serialized
            package[part] = _serialize(trees[part])
        if patcher.comments:
            _write_comments(package, patcher.comments)
    if options.custom_properties:
        _write_custom_properties(package, options.custom_properties)
    return write_package(package, original)


def _find_blocks(
    package: Mapping[str, bytes], keys: set[str]
) -> tuple[dict[str, tuple[etree._Element, ParagraphText]], dict[str, etree._ElementTree]]:
    """The ``(w:p, text)`` of each block in ``keys``, in document order, and the parsed parts."""
    blocks: dict[str, tuple[etree._Element, ParagraphText]] = {}
    trees: dict[str, etree._ElementTree] = {}
    if keys:
        for key, p, tree, pt in iter_paragraph_runs(package):
            trees.setdefault(key.partition("#")[0], tree)
            if key in keys and is_block_text(pt.text):
                blocks[key] = (p, pt)
    if missing := keys - blocks.keys():
        raise InvalidChangeError(f"No block with key {min(missing)!r}")
    return blocks, trees


def _validate(pt: ParagraphText, key: str, changes: Sequence[Change]) -> list[Change]:
    """Sorted changes, without the ones that change nothing (empty insertions)."""
    ordered = [c for c in validate_changes(pt.text, changes) if c.start < c.end or c.replacement]
    for c in ordered:
        if any(NOT_XML.search(s) for s in (c.replacement, c.comment or "", c.author)):
            raise InvalidChangeError(f"{key}: change ({c.start}, {c.end}) has non-XML characters")
        if c.start == c.end and (
            (c.start == 0 and pt.pieces[0].node.tag == _R)  # a field from the paragraph before
            or (c.start == len(pt.text) and pt.open_fields)  # a field going on past the end
        ):
            raise InvalidChangeError(f"{key}: insertion at {c.start} would land inside a field")
    return ordered


# --- patching -------------------------------------------------------------------------


class _Patcher:
    def __init__(self, options: DocxWriteOptions, roots: list[etree._Element]):
        self.tracked = options.mode == "tracked"
        self.date = _iso(options.date)
        self.next_revision = _next_id(roots, REVISIONS)
        self.next_bookmark = _next_id(roots, _BOOKMARKS)
        self.next_comment = _next_id(roots, _COMMENTS)
        self.comments: list[etree._Element] = []  # w:comment elements, in document order

    def paragraph(self, pt: ParagraphText, changes: list[Change]) -> None:
        """Apply sorted changes to one paragraph, right to left so offsets stay valid.

        An insertion at 0 goes first: its anchor, the first piece, may be replaced next.
        """
        comment_ids = {}
        for c in changes:
            if self.tracked and c.comment:
                comment_ids[c] = self.next_comment
                self.comments.append(self._comment(self.next_comment, c))
                self.next_comment += 1
        at_start = [c for c in changes if c.start == c.end == 0]
        for c in at_start + [c for c in reversed(changes) if c not in at_start]:
            self._change(pt, c, comment_ids.get(c))

    def _change(self, pt: ParagraphText, c: Change, comment_id: int | None) -> None:
        if c.start == c.end:
            new = text_run(c.replacement, insertion_rpr(pt, c.start))
            first = last = self._revision("w:ins", c.author, [new]) if self.tracked else new
            if c.start == 0:
                start_of(pt.pieces[0]).addprevious(first)
            else:
                end_of(piece_at(pt, c.start - 1), c.start).addnext(first)
        else:
            covered = isolate(pt.pieces, c.start, c.end)
            # Like typing over a selection in Word: the first replaced character's format.
            new = text_run(c.replacement, covered[0].find(_RPR)) if c.replacement else None
            if not self.tracked:
                if new is not None:
                    covered[-1].addnext(new)
                remove_text(covered)
                return
            dels = [self._delete(group, c.author) for group in deletion_groups(covered)]
            first, last = dels[0], dels[-1]
            if new is not None:
                last.addnext(ins := self._revision("w:ins", c.author, [new]))
                last = ins
        if comment_id is not None:
            first.addprevious(etree.Element(qn("w:commentRangeStart"), {_ID: str(comment_id)}))
            end = etree.Element(qn("w:commentRangeEnd"), {_ID: str(comment_id)})
            last.addnext(end)
            ref = etree.Element(_R)
            etree.SubElement(ref, qn("w:commentReference"), {_ID: str(comment_id)})
            end.addnext(ref)

    def _revision(
        self, tag: str, author: str, content: Sequence[etree._Element] = ()
    ) -> etree._Element:
        """A w:ins or w:del. Its w:id is left empty for ``number_revisions``."""
        el = etree.Element(qn(tag), {_ID: "", _AUTHOR: author, _DATE: self.date})
        el.extend(content)
        return el

    def _delete(self, group: list[etree._Element], author: str) -> etree._Element:
        """Wrap sibling range ``group`` in a w:del; its text becomes w:delText."""
        deletion = self._revision("w:del", author)
        group[0].addprevious(deletion)
        deletion.extend(group)
        for t in list(deletion.iter(_T)):
            t.tag = qn("w:delText")
            preserve(t)
        return deletion

    def _comment(self, comment_id: int, c: Change) -> etree._Element:
        attrs = {_ID: str(comment_id), _AUTHOR: c.author, _DATE: self.date}
        comment = etree.Element(qn("w:comment"), attrs)
        p = etree.SubElement(comment, qn("w:p"))
        etree.SubElement(etree.SubElement(p, _R), qn("w:annotationRef"))
        p.append(text_run(c.comment or "", None))
        return comment

    def bookmark(self, p: etree._Element, name: str) -> None:
        """Wrap the paragraph's content in a bookmark: from just after w:pPr to its end."""
        bookmark_id = str(self.next_bookmark)
        self.next_bookmark += 1
        start = etree.Element(qn("w:bookmarkStart"), {_ID: bookmark_id, qn("w:name"): name})
        ppr = p.find(_PPR)
        if ppr is not None:
            ppr.addnext(start)
        else:
            p.insert(0, start)
        p.append(etree.Element(qn("w:bookmarkEnd"), {_ID: bookmark_id}))

    def number_revisions(self, roots: list[etree._Element]) -> None:
        """Give every revision without a w:id (new ones, copied rPrChange) the next id."""
        for root in roots:
            for el in root.iter(*REVISIONS):
                if not el.get(_ID):
                    el.set(_ID, str(self.next_revision))
                    self.next_revision += 1


def _iso(date: datetime) -> str:
    """ISO 8601 in UTC, as Word writes it: ``2026-11-21T00:00:00Z``."""
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _word_roots(
    package: Mapping[str, bytes], trees: Mapping[str, etree._ElementTree]
) -> list[etree._Element]:
    """Every XML part directly in word/, where revision, bookmark and comment ids live."""
    roots = []
    for name, data in package.items():
        folder, base = posixpath.split(name)
        if folder == "word" and base.endswith(".xml"):
            roots.append((trees[name] if name in trees else parse_xml(data, name)).getroot())
    return roots


def _next_id(roots: list[etree._Element], tags: tuple[str, ...]) -> int:
    """One more than the largest w:id on ``tags`` elements (1 when there are none)."""
    ids = [0]
    for root in roots:
        ids += [_int(el.get(_ID)) for el in root.iter(*tags)]
    return max(ids) + 1


def _int(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 0


# --- package parts --------------------------------------------------------------------

_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CONTENT_TYPES = "[Content_Types].xml"
_CUSTOM_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


def _write_comments(package: dict[str, bytes], comments: list[etree._Element]) -> None:
    part = _related_part(
        package,
        DOCUMENT_PART,
        f"{_OFFICE_REL}/comments",
        "word/comments.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
    )
    if part in package:
        tree = parse_xml(package[part], part)
    else:
        tree = etree.ElementTree(etree.Element(qn("w:comments"), nsmap={"w": NS["w"]}))
    tree.getroot().extend(comments)
    package[part] = _serialize(tree)


def _write_custom_properties(package: dict[str, bytes], properties: Mapping[str, str]) -> None:
    """Set string properties in docProps/custom.xml, replacing any of the same name."""
    part = _related_part(
        package,
        "",
        f"{_OFFICE_REL}/custom-properties",
        "docProps/custom.xml",
        "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    )
    if part in package:
        tree = parse_xml(package[part], part)
    else:
        nsmap = {None: NS["op"], "vt": NS["vt"]}
        tree = etree.ElementTree(etree.Element(qn("op:Properties"), nsmap=nsmap))
    root = tree.getroot()
    existing = {prop.get("name"): prop for prop in root.iterfind("op:property", NS)}
    pid = max([1] + [_int(prop.get("pid")) for prop in existing.values()])  # pids start at 2
    for name, value in properties.items():
        prop = existing.get(name)
        if prop is None:
            pid += 1
            attrs = {"fmtid": _CUSTOM_FMTID, "pid": str(pid), "name": name}
            prop = etree.SubElement(root, qn("op:property"), attrs)
        for child in list(prop):
            prop.remove(child)
        etree.SubElement(prop, qn("vt:lpwstr")).text = value
    package[part] = _serialize(tree)


def _related_part(
    package: dict[str, bytes], source: str, rel_type: str, default: str, content_type: str
) -> str:
    """The part ``source`` (``""`` for the package) relates to by ``rel_type``.

    Adds the relationship, to ``default``, when there is none, and the part's content type
    override when it is missing.
    """
    folder, name = posixpath.split(source)
    rels_part = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels_part in package:
        rels = parse_xml(package[rels_part], rels_part)
    else:
        rels = etree.ElementTree(etree.Element(qn("pr:Relationships"), nsmap={None: NS["pr"]}))
    root = rels.getroot()
    part = None
    for rel in root.iterfind("pr:Relationship", NS):
        if rel.get("Type") == rel_type and rel.get("TargetMode") != "External":
            target = rel.get("Target", "")
            part = target[1:] if target.startswith("/") else posixpath.join(folder, target)
            part = posixpath.normpath(part)
            break
    if part is None:
        part = default
        ids = {rel.get("Id") for rel in root}
        n = len(ids) + 1
        while f"rId{n}" in ids:
            n += 1
        target = posixpath.relpath(default, folder or ".")
        etree.SubElement(
            root, qn("pr:Relationship"), {"Id": f"rId{n}", "Type": rel_type, "Target": target}
        )
        package[rels_part] = _serialize(rels)
    _add_override(package, part, content_type)
    return part


def _add_override(package: dict[str, bytes], part: str, content_type: str) -> None:
    if _CONTENT_TYPES not in package:
        raise UnsupportedDocumentError(f"Not a DOCX file: {_CONTENT_TYPES} is missing")
    tree = parse_xml(package[_CONTENT_TYPES], _CONTENT_TYPES)
    root = tree.getroot()
    name = f"/{part}"
    if any(o.get("PartName", "").lower() == name.lower() for o in root.iterfind("ct:Override", NS)):
        return
    etree.SubElement(root, qn("ct:Override"), {"PartName": name, "ContentType": content_type})
    package[_CONTENT_TYPES] = _serialize(tree)


def _serialize(xml: etree._ElementTree) -> bytes:
    return _DECLARATION + etree.tostring(xml, encoding="UTF-8")
