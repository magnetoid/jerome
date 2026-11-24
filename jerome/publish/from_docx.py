"""A clean DOCX in the shared publishing form, by mammoth.

Before mammoth runs, the Word file is adjusted so that its headings are exactly Jerome's:
every paragraph Jerome reads as a heading (outline level L, from the paragraph or its
style) gets a style mammoth maps to ``h{L+1}`` (deeper levels to ``h4``), and the Word table
of contents is taken out (Jerome makes its own). After it, footnote references become
inline ``jr-note`` spans holding the note, and the notes list is removed.
"""

import io
import zipfile

import mammoth
from lxml import etree

from jerome.documents.docx_reader import _Styles
from jerome.documents.docx_xml import DOCUMENT_PART, NS, parse_xml, qn, read_package, serialize
from jerome.publish.model import NOTE_CLASS, PubBook, PubChapter, PubImage, PublishError

_HEADING_STYLE = "JeromeHeading{}"
_STYLE_MAP = "\n".join(f"p.{_HEADING_STYLE.format(n)} => h{n}:fresh" for n in (1, 2, 3, 4))
_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/svg+xml": "svg"}
TOC_LEFT_OUT = "Word table of contents"


def docx_to_pub(clean_docx: bytes, language: str) -> PubBook:
    pub = PubBook(language=language, chapters=[])
    prepared = _prepare(read_package(clean_docx), pub)
    images = _Images(pub)
    try:
        result = mammoth.convert_to_html(
            io.BytesIO(prepared),
            style_map=_STYLE_MAP,
            convert_image=images.convert,
            external_file_access=False,
        )
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise PublishError(f"The Word file could not be converted: {exc}") from exc
    body = etree.fromstring(f"<body>{result.value}</body>".encode())
    pub.note_keys = _inline_notes(body)
    pub.chapters = _split(body)
    return pub


# --- before mammoth -------------------------------------------------------------------


def _prepare(package: dict[str, bytes], pub: PubBook) -> bytes:
    styles = _Styles.load(package)
    tree = parse_xml(package[DOCUMENT_PART], DOCUMENT_PART)
    body = tree.getroot().find("w:body", NS)
    toc = False
    for p in list(body.iter(qn("w:p"))) if body is not None else []:
        if _is_toc(p, styles):
            p.getparent().remove(p)
            toc = True
            continue
        if next(p.iterancestors(qn("w:tc")), None) is not None:
            continue  # Jerome reads table cells as cells, never headings
        level = styles.outline_level(p)
        if level is not None:
            _set_style(p, _HEADING_STYLE.format(min(level, 3) + 1))
    if toc:
        pub.left_out.append(TOC_LEFT_OUT)
    package = dict(package)
    package[DOCUMENT_PART] = serialize(tree)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in package.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _is_toc(p: etree._Element, styles: _Styles) -> bool:
    style_id = p.find("w:pPr/w:pStyle", NS)
    sid = (style_id.get(qn("w:val")) or "") if style_id is not None else ""
    if sid.upper().startswith("TOC") and sid[3:].isdigit():
        return True
    return any(s.name.startswith("toc ") or s.name == "toc heading" for s in styles.chain(p))


def _set_style(p: etree._Element, style_id: str) -> None:
    ppr = p.find("w:pPr", NS)
    if ppr is None:
        ppr = etree.SubElement(p, qn("w:pPr"))
        p.insert(0, ppr)
    pstyle = ppr.find("w:pStyle", NS)
    if pstyle is None:
        pstyle = etree.Element(qn("w:pStyle"))
        ppr.insert(0, pstyle)
    pstyle.set(qn("w:val"), style_id)


# --- images ---------------------------------------------------------------------------


class _Images:
    def __init__(self, pub: PubBook):
        self.pub = pub

    def convert(self, image) -> list:
        try:
            with image.open() as f:
                data = f.read()
        except (OSError, ValueError, KeyError):
            self.pub.warnings.append(
                "A linked (not embedded) image was left out; embed it in the Word file"
            )
            return []
        ext = _IMAGE_TYPES.get(image.content_type or "")
        if ext is None:
            self.pub.warnings.append(
                f"An image of type {image.content_type or 'unknown'} was left out; "
                "use JPEG, PNG, GIF or SVG"
            )
            return []
        name = f"image{len(self.pub.images) + 1}.{ext}"
        self.pub.images[name] = PubImage(name, image.content_type, data)
        attrs = {"src": f"images/{name}", "alt": image.alt_text or ""}
        return [mammoth.html.element("img", attrs)]


# --- after mammoth --------------------------------------------------------------------


def _inline_notes(body: etree._Element) -> list[str]:
    """Move each note's content into a jr-note span at its reference; drop the notes list.
    Returns the note ids in reading order."""
    notes: dict[str, etree._Element] = {}
    order: list[str] = []
    for li in body.iter("li"):
        note_id = li.get("id") or ""
        if note_id.startswith(("footnote-", "endnote-")):
            notes[note_id] = li
    for sup in list(body.iter("sup")):
        links = list(sup)
        href = links[0].get("href", "") if len(links) == 1 and links[0].tag == "a" else ""
        li = notes.get(href[1:]) if href.startswith("#") else None
        if li is None:
            continue
        order.append(href[1:])
        span = etree.Element("span", {"class": NOTE_CLASS})
        _fill_note(span, li)
        span.tail = sup.tail
        sup.getparent().replace(sup, span)
    for li in notes.values():
        ol = li.getparent()
        ol.remove(li)
        if len(ol) == 0 and ol.getparent() is not None:
            ol.getparent().remove(ol)
    return order


def _fill_note(span: etree._Element, li: etree._Element) -> None:
    for a in list(li.iter("a")):
        if (a.get("href") or "").startswith(("#footnote-ref-", "#endnote-ref-")):
            _remove_keep_tail(a)
    paras = [p for p in li if p.tag == "p"] or [li]
    for i, p in enumerate(paras):
        _append_text(span, (" " if i else "") + (p.text or ""))
        for child in list(p):
            span.append(child)
    span.text = (span.text or "").lstrip()
    last = span[-1] if len(span) else None
    if last is not None:
        last.tail = (last.tail or "").rstrip() or None
    else:
        span.text = span.text.rstrip()


def _append_text(parent: etree._Element, text: str) -> None:
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _remove_keep_tail(el: etree._Element) -> None:
    parent = el.getparent()
    prev = el.getprevious()
    if el.tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _split(body: etree._Element) -> list[PubChapter]:
    """Split at h1. Content before the first h1 is untitled and not in the contents; a book
    with no h1 is one chapter."""
    groups: list[list[etree._Element]] = [[]]
    for child in body:
        if child.tag == "h1":
            groups.append([])
        groups[-1].append(child)
    chapters = []
    has_h1 = len(groups) > 1
    for i, group in enumerate(groups):
        if not group:
            continue
        html = "".join(etree.tostring(el, encoding="unicode") for el in group)
        if i == 0:
            chapters.append(PubChapter(None, html, in_toc=not has_h1))
        else:
            title = " ".join(_text_outside_notes(group[0]).split()) or None
            chapters.append(PubChapter(title, html))
    return chapters


def _text_outside_notes(el: etree._Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if child.get("class") != NOTE_CLASS:
            parts.append(_text_outside_notes(child))
        parts.append(child.tail or "")
    return "".join(parts)
