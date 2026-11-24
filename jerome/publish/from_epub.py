"""A clean EPUB in the shared publishing form, for the PDF.

The EPUB-born book's own EPUB is the clean export itself; this form only feeds the PDF.

- **Front pages** Jerome makes itself (cover, title page, copyright page, contents) are left
  out: documents the landmarks, the EPUB 2 guide or ``epub:type`` mark as such, the nav
  document, and a page that only shows the cover image.
- **Chapters** follow the table of contents. A chapter runs from its entry to the next one,
  across documents; a wrapper ``div``/``section`` holding an entry is opened up so the cut
  falls exactly there. Text before the first entry is kept, untitled and not in the
  contents. Without a table of contents every document is a chapter.
- **Notes:** a note reference (``epub:type="noteref"``, or a link to an aside or a typed
  note) becomes a ``jr-note`` span holding the note, and the note is removed from where it
  stood. Its own number or back-link stays in the span as ``jr-drop`` (hidden when rendered).
- **The book's CSS is not used:** ``class``, ``style``, ``id`` and ``epub:type`` attributes
  go, and so do ``style``, ``script`` and embedded media. Internal links lose their target.
- **Images** inside the package are collected as ``images/image{n}.{ext}``. Anything outside
  it (a remote URL) is left out with a warning, never fetched.
"""

import posixpath
from dataclasses import dataclass
from html.entities import html5
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from lxml import etree

from jerome.documents.epub_xml import (
    NS,
    EpubPackage,
    content_documents,
    epub_type,
    iter_epub_blocks,
    local_name,
    parse_content_document,
    read_container,
    resolve_href,
)
from jerome.publish.model import DROP_CLASS, NOTE_CLASS, PubBook, PubChapter, PubImage

_FRONT_PAGES = {
    "cover": "cover",
    "titlepage": "title page",
    "copyright-page": "copyright page",
    "toc": "contents",
}
_NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
_NOTE_ROLES = {"doc-footnote", "doc-endnote"}
_NOTE_BLOCKS = {"aside", "li", "div", "p", "section", "dd", "blockquote", "dt"}
_TEXT_BLOCKS = {"p", "div", "li", "dd", "dt", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
_CONTAINERS = {"div", "section", "article", "main", "header", "footer", "body"}
_REMOVED = {"script", "style", "template", "noscript", "link", "meta", "head", "title"}
_MEDIA = {"iframe", "object", "embed", "video", "audio", "canvas", "form", "input", "button"}
_MEDIA |= {"select", "textarea", "source", "track", "map"}
_KEEP_ATTRS = {"href", "src", "alt", "colspan", "rowspan", "lang", "start", "reversed"}
_KEEP_ATTRS |= {"value", "dir", "type", "scope", "headers"}
_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/svg+xml": "svg"}
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_XHTML = f"{{{NS['xhtml']}}}"
_CUT = "data-jr-cut"  # marks where a table-of-contents entry starts, while assembling


@dataclass
class _Doc:
    path: str
    root: etree._Element
    body: etree._Element


def epub_to_pub(clean_epub: bytes, language: str) -> tuple[PubBook, set[str], set[str]]:
    """The book, the spine paths whose blocks it holds, and the keys of the blocks that
    became notes (both for ``check_text``)."""
    package = read_container(clean_epub)
    pub = PubBook(language=language, chapters=[])
    docs: list[_Doc] = []
    for item in content_documents(package):
        root = parse_content_document(package.files[item.path], item.path).root
        body = next((el for el in root.iter() if local_name(el) == "body"), None)
        kind = _front_page(package, item.path, body)
        if kind is not None:
            pub.left_out.append(f"{kind} ({_relative(package, item.path)})")
        elif body is not None:
            docs.append(_Doc(item.path, root, body))
    note_keys = _inline_notes(docs)
    titles = _mark_cuts(package, docs)
    images = _Images(package, pub)
    for doc in docs:
        _clean(doc, images, pub)
    pub.chapters = _assemble(docs, titles)
    return pub, {d.path for d in docs}, note_keys


def _relative(package: EpubPackage, path: str) -> str:
    return posixpath.relpath(path, posixpath.dirname(package.opf_path) or ".")


# --- front pages ----------------------------------------------------------------------


def _front_page(package: EpubPackage, path: str, body: etree._Element | None) -> str | None:
    """The kind of front page Jerome makes itself that this document is, or None."""
    for kind, label in _FRONT_PAGES.items():
        if package.landmarks.get(kind) == path:
            return label
    if any("nav" in m.properties and m.path == path for m in package.manifest.values()):
        return "contents"
    if body is None:
        return None
    types = set().union(*(epub_type(el) for el in (body, *body) if isinstance(el.tag, str)))
    for kind, label in _FRONT_PAGES.items():
        if kind in types:
            return label
    cover = package.manifest.get(package.cover_item or "")
    if cover is not None and not "".join(body.itertext()).strip():
        for el in body.iter():
            ref = el.get("src") if local_name(el) == "img" else None
            if local_name(el) == "image":
                ref = el.get(_XLINK_HREF) or el.get("href")
            if ref and resolve_href(path, ref)[0] == cover.path:
                return "cover"
    return None


# --- notes ----------------------------------------------------------------------------


def _inline_notes(docs: list[_Doc]) -> set[str]:
    by_id: dict[tuple[str, str], etree._Element] = {}
    leaf_keys: list[tuple[etree._Element, str]] = []
    for doc in docs:
        leaf_keys += [(el, f"{doc.path}#{n}") for n, el in iter_epub_blocks(doc.root)]
        for el in doc.root.iter():
            if isinstance(el.tag, str) and el.get("id"):
                by_id.setdefault((doc.path, el.get("id")), el)

    used: list[etree._Element] = []
    used_ids: set[int] = set()
    for doc in docs:
        links = [el for el in doc.body.iter() if local_name(el) == "a" and el.get("href")]
        for a in links:
            if local_name(a) != "a" or not a.get("href"):
                continue  # a back-link already moved into a note
            if not _attached(a, doc.body) or _inside_note(a):
                continue
            path, anchor = resolve_href(doc.path, a.get("href", ""))
            target = by_id.get((path, anchor)) if anchor else None
            if target is None or not _attached(target, None) or not _is_noteref(a, target):
                continue
            note = _note_block(target)
            if id(note) in used_ids or any(a is d for d in note.iter()):
                continue
            ref = _reference(a)
            back = {i for i in (a.get("id"), ref.get("id")) if i}
            span = etree.Element("span", {"class": NOTE_CLASS})
            _fill_note(span, note, path, doc.path, back)
            span.tail = ref.tail
            ref.getparent().replace(ref, span)
            used.append(note)
            used_ids.add(id(note))
    keys = {
        key
        for el, key in leaf_keys
        if id(el) in used_ids or any(id(x) in used_ids for x in el.iterancestors())
    }
    for note in used:
        if note.getparent() is not None:
            _remove_keep_tail(note)
    return keys


def _attached(el: etree._Element, body: etree._Element | None) -> bool:
    """Whether ``el`` is still in its document (under ``body``, when given)."""
    for ancestor in el.iterancestors():
        if body is not None and ancestor is body:
            return True
        if ancestor.getparent() is None:
            return body is None and local_name(ancestor) == "html"
    return False


def _inside_note(el: etree._Element) -> bool:
    return any(a.get("class") == NOTE_CLASS for a in el.iterancestors())


def _is_noteref(a: etree._Element, target: etree._Element) -> bool:
    if "noteref" in epub_type(a) or a.get("role") == "doc-noteref":
        return True
    return _is_note(_note_block(target))


def _is_note(el: etree._Element) -> bool:
    return (
        local_name(el) == "aside"
        or bool(epub_type(el) & _NOTE_TYPES)
        or el.get("role") in _NOTE_ROLES
    )


def _note_block(target: etree._Element) -> etree._Element:
    """The block a note target stands in: itself, or its nearest block ancestor; an aside
    or typed note whose only element is that block, rather than the block."""
    el: etree._Element | None = target
    while el is not None and local_name(el) not in _NOTE_BLOCKS:
        el = el.getparent()
    note = el if el is not None else target
    parent = note.getparent()
    if (
        parent is not None
        and _is_note(parent)
        and len([c for c in parent if isinstance(c.tag, str)]) == 1
        and not (parent.text or "").strip()
    ):
        return parent
    return note


def _reference(a: etree._Element) -> etree._Element:
    """The note reference to replace: the link, or a ``sup`` holding only it."""
    parent = a.getparent()
    if (
        parent is not None
        and local_name(parent) == "sup"
        and len(parent) == 1
        and not (parent.text or "").strip()
        and not (a.tail or "").strip()
    ):
        return parent
    return a


def _fill_note(
    span: etree._Element, note: etree._Element, note_path: str, ref_path: str, back: set[str]
) -> None:
    for a in [el for el in note.iter() if local_name(el) == "a" and el.get("href")]:
        path, anchor = resolve_href(note_path, a.get("href", ""))
        if (path == ref_path and anchor in back) or a.get("role") == "doc-backlink":
            for name in list(a.attrib):
                del a.attrib[name]
            a.tag = "span"
            a.set("class", DROP_CLASS)
    blocks = [c for c in note if local_name(c) in _TEXT_BLOCKS] or [note]
    for i, block in enumerate(blocks):
        _append_text(span, (" " if i else "") + (block.text or ""))
        for child in list(block):
            tail = child.tail
            span.append(child)
            child.tail = tail
    span.text = (span.text or "").lstrip() or None
    if len(span):
        span[-1].tail = (span[-1].tail or "").rstrip() or None
    elif span.text:
        span.text = span.text.rstrip()


def _append_text(parent: etree._Element, text: str) -> None:
    if not text:
        return
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _remove_keep_tail(el: etree._Element) -> None:
    parent = el.getparent()
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


# --- chapters -------------------------------------------------------------------------


def _mark_cuts(package: EpubPackage, docs: list[_Doc]) -> dict[int, str | None]:
    """Mark where each table-of-contents entry starts; its title by mark number."""
    by_path = {d.path: d for d in docs}
    titles: dict[int, str | None] = {}
    for point in package.nav_points:
        doc = by_path.get(point.path)
        if doc is None:
            continue
        el = doc.body
        if point.anchor:
            found = next((e for e in doc.body.iter() if e.get("id") == point.anchor), None)
            el = found if found is not None else doc.body
        if el.get(_CUT) is not None:
            continue
        el.set(_CUT, str(len(titles)))
        titles[len(titles)] = " ".join(point.title.split()) or None
    return titles


def _assemble(docs: list[_Doc], titles: dict[int, str | None]) -> list[PubChapter]:
    chapters: list[PubChapter] = []
    parts: list[str] = []

    def start(title: str | None, in_toc: bool) -> None:
        nonlocal parts
        parts = []
        chapters.append(PubChapter(title, "", in_toc))

    def finish() -> None:
        if chapters:
            chapters[-1].html = "".join(parts)

    for doc in docs:
        if not titles:
            finish()
            start(_first_heading(doc.body), True)
        for unit in _units(doc.body):
            cut = unit if isinstance(unit, int) else None
            if isinstance(unit, etree._Element):
                cut = _pop_cuts(unit)
            if cut is not None:
                finish()
                start(titles[cut], True)
            elif not chapters:
                start(None, False)
            if isinstance(unit, str):
                parts.append(escape(unit))
            elif isinstance(unit, etree._Element):
                parts.append(etree.tostring(unit, encoding="unicode", with_tail=False))
    finish()
    return [c for c in chapters if c.html.strip()]


def _units(body: etree._Element) -> list[etree._Element | str | int]:
    """The body's content as a flat list: elements, text, and cut marks (ints). A
    container holding a cut deeper inside is opened up, until every cut is on a unit."""
    units: list[etree._Element | str | int] = []
    if body.get(_CUT) is not None:
        units.append(int(body.attrib.pop(_CUT)))
    units += _children(body)
    changed = True
    while changed:
        changed = False
        out: list[etree._Element | str | int] = []
        for u in units:
            if (
                isinstance(u, etree._Element)
                and local_name(u) in _CONTAINERS
                and any(d.get(_CUT) is not None for d in u.iterdescendants())
            ):
                changed = True
                if u.get(_CUT) is not None:
                    out.append(int(u.attrib.pop(_CUT)))
                out += _children(u)
            else:
                out.append(u)
        units = out
    return [u for u in units if not (isinstance(u, str) and not u.strip())]


def _children(el: etree._Element) -> list[etree._Element | str]:
    out: list[etree._Element | str] = [el.text or ""]
    for child in list(el):
        tail, child.tail = child.tail, None
        out += [child, tail or ""]
    return out


def _pop_cuts(el: etree._Element) -> int | None:
    found = [int(e.attrib.pop(_CUT)) for e in el.iter() if e.get(_CUT) is not None]
    return min(found) if found else None


def _first_heading(body: etree._Element) -> str | None:
    for el in body.iter():
        if local_name(el) in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return " ".join(_text_outside_notes(el).split()) or None
    return None


def _text_outside_notes(el: etree._Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if child.get("class") != NOTE_CLASS:
            parts.append(_text_outside_notes(child))
        parts.append(child.tail or "")
    return "".join(parts)


# --- cleaning -------------------------------------------------------------------------


class _Images:
    def __init__(self, package: EpubPackage, pub: PubBook):
        self.package, self.pub = package, pub
        self.by_path: dict[str, str] = {}
        self.types = {m.path: m.media_type for m in package.manifest.values()}

    def src(self, doc_path: str, ref: str) -> str | None:
        """The image's new src, or None when it is left out."""
        if urlparse(ref).scheme or ref.startswith("//"):
            self.pub.warnings.append(f"A remote image ({ref}) was left out; it is never fetched")
            return None
        path, _ = resolve_href(doc_path, ref)
        if path in self.by_path:
            return f"images/{self.by_path[path]}"
        data = self.package.files.get(path)
        media_type = self.types.get(path, "")
        rel = _relative(self.package, path)
        if data is None:
            self.pub.warnings.append(f"An image ({rel}) is missing from the EPUB; left out")
            return None
        if media_type not in _IMAGE_TYPES:
            self.pub.warnings.append(
                f"An image ({rel}, {media_type or 'unknown type'}) was left out; "
                "use JPEG, PNG, GIF or SVG"
            )
            return None
        name = f"image{len(self.pub.images) + 1}.{_IMAGE_TYPES[media_type]}"
        self.pub.images[name] = PubImage(name, media_type, data)
        self.by_path[path] = name
        return f"images/{name}"


def _clean(doc: _Doc, images: _Images, pub: PubBook) -> None:
    for el in list(doc.body.iter()):
        if el is not doc.body and not _attached(el, doc.body):
            continue  # inside an element already removed
        if el.tag is etree.Entity:
            _replace_entity(el)
            continue
        if not isinstance(el.tag, str):  # a comment or processing instruction
            _remove_keep_tail(el)
            continue
        name = local_name(el)
        if el.tag.startswith(_XHTML) or not el.tag.startswith("{"):
            if name in _REMOVED or name in _MEDIA:
                if name in _MEDIA:
                    pub.warnings.append(
                        f"An embedded {name} was left out ({_relative(images.package, doc.path)})"
                    )
                _remove_keep_tail(el)
                continue
            el.tag = name
            _clean_attributes(el)
            if name == "img":
                src = images.src(doc.path, el.get("src") or "")
                if src is None:
                    _remove_keep_tail(el)
                else:
                    el.set("src", src)
        elif name == "image":  # inside an svg
            ref = el.get(_XLINK_HREF) or el.get("href") or ""
            src = images.src(doc.path, ref)
            if src is None:
                _remove_keep_tail(el)
            else:
                el.attrib.pop("href", None)
                el.set(_XLINK_HREF, src)
    for el in doc.root.iter():
        if isinstance(el.tag, str) and el.tag.startswith(_XHTML):
            el.tag = local_name(el)
    etree.cleanup_namespaces(doc.root)


def _clean_attributes(el: etree._Element) -> None:
    lang = el.get(_XML_LANG)
    for attr in list(el.attrib):
        value = el.get(attr) or ""
        keep = attr in _KEEP_ATTRS or attr == _CUT
        keep |= attr == "class" and value in (NOTE_CLASS, DROP_CLASS)
        if attr == "href":
            keep = urlparse(value).scheme in ("http", "https", "mailto")
        if not keep:
            del el.attrib[attr]
    if lang and not el.get("lang"):
        el.set("lang", lang)


def _replace_entity(el: etree._Element) -> None:
    text = html5.get(f"{el.name};", "")
    parent, prev = el.getparent(), el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text + (el.tail or "")
    else:
        parent.text = (parent.text or "") + text + (el.tail or "")
    parent.remove(el)
