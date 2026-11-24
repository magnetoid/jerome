"""A new EPUB 3 for a Word-born book, from the shared publishing form.

Package: ``OEBPS/content.opf``, ``nav.xhtml`` (and ``toc.ncx`` for older readers), Jerome's
``style.css`` (no fonts), a cover page when there is a cover, a title page, a copyright
page, one ``chapter-{n:03}.xhtml`` per chapter, and the images. Every XML part is built
with lxml. Notes become EPUB 3 footnotes: a noteref at the reference, an aside at the end
of the chapter.
"""

import io
import re
import uuid
import zipfile
from datetime import UTC, datetime
from importlib.resources import files
from urllib.parse import urlparse

from lxml import etree

from jerome.core.publication import Publication
from jerome.publish.model import (
    DROP_CLASS,
    NOTE_CLASS,
    PubBook,
    PubChapter,
    chapter_labels,
    word,
)

_XHTML = "http://www.w3.org/1999/xhtml"
_EPUB = "http://www.idpf.org/2007/ops"
_OPF = "http://www.idpf.org/2007/opf"
_DC = "http://purl.org/dc/elements/1.1/"
_NCX = "http://www.daisy.org/z3986/2005/ncx/"
_X = f"{{{_XHTML}}}"
_EPUB_TYPE = f"{{{_EPUB}}}type"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_MEDIA_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/svg+xml": "svg"}
_LINK_SCHEMES = ("http", "https", "mailto")


def book_identifier(
    book_id: int,
    pub: Publication,
    existing: str | None = None,
    *,
    created: datetime | None = None,
) -> str:
    """``urn:isbn:`` with the EPUB ISBN when set; else ``existing`` (an EPUB-born proofread
    book's own identifier, which the caller passes only then); else a uuid5 that stays the
    same for the book. ``created`` (the book row's creation time, to the microsecond) keeps
    book 1 of one Jerome apart from book 1 of another: reading apps key books on it."""
    if pub.isbn_epub:
        return "urn:isbn:" + re.sub(r"[\s-]", "", pub.isbn_epub)
    if existing:
        return existing
    name = f"jerome-book-{book_id}" + (f"-{created.isoformat()}" if created else "")
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, name)}"


def write_epub_book(
    pub_book: PubBook,
    pub: Publication,
    *,
    identifier: str,
    cover: tuple[bytes, str] | None,
    now: datetime,
) -> bytes:
    lang = pub.language
    labels = chapter_labels(pub_book)
    names = [f"chapter-{n:03}.xhtml" for n in range(1, len(pub_book.chapters) + 1)]
    bodies = [_chapter_body(c) for c in pub_book.chapters]
    _fix_links(bodies, names)

    parts: dict[str, bytes] = {}  # path in OEBPS -> content
    items: list[tuple[str, str, str, str | None]] = []  # (id, href, media type, properties)
    spine: list[str] = []

    def add(item_id: str, href: str, media: str, data: bytes, props: str | None = None) -> None:
        parts[href] = data
        items.append((item_id, href, media, props))

    add("css", "style.css", "text/css", files("jerome.publish").joinpath("ebook.css").read_bytes())
    if cover is not None:
        data, media = cover
        image = f"images/cover.{_MEDIA_EXT.get(media, 'jpg')}"
        add("cover-image", image, media, data, "cover-image")
        page = _page(pub.title, lang, "cover", word(lang, "cover"))
        section = page.find(f"{_X}body/{_X}section")
        section.set("class", "cover")
        etree.SubElement(section, f"{_X}img", src=image, alt=pub.title)
        add("cover", "cover.xhtml", "application/xhtml+xml", _xml(page))
        spine.append("cover")
    add("title", "title.xhtml", "application/xhtml+xml", _xml(_title_page(pub)))
    add("copyright", "copyright.xhtml", "application/xhtml+xml", _xml(_copyright_page(pub)))
    spine += ["title", "copyright", "nav"]  # the contents page, written below
    for n, (chapter, name, body) in enumerate(zip(pub_book.chapters, names, bodies, strict=True)):
        title = labels[n] if chapter.in_toc else pub.title
        page = _page(title, lang, "chapter" if chapter.in_toc else None, None)
        page.find(f"{_X}body").getparent().replace(page.find(f"{_X}body"), body)
        props = "svg" if any(_is_svg(el) for el in body.iter()) else None
        add(f"c{n + 1:03}", name, "application/xhtml+xml", _xml(page), props)
        spine.append(f"c{n + 1:03}")
    for image in pub_book.images.values():
        add(f"img-{image.name.replace('.', '-')}", f"images/{image.name}", image.media_type,
            image.data)  # fmt: skip
    toc = [(labels[i], names[i]) for i, c in enumerate(pub_book.chapters) if c.in_toc]
    first = next((names[i] for i, c in enumerate(pub_book.chapters) if c.in_toc), None)
    add("nav", "nav.xhtml", "application/xhtml+xml", _xml(_nav(pub, toc, first, cover)), "nav")
    add("ncx", "toc.ncx", "application/x-dtbncx+xml", _xml(_ncx(pub, identifier, toc)))
    opf = _opf(pub, identifier, now, items, spine)
    return _zip(parts, _xml(opf))


# --- chapters -------------------------------------------------------------------------


def _chapter_body(chapter: PubChapter) -> etree._Element:
    """The chapter as an XHTML body: elements in the XHTML namespace, notes as footnotes."""
    body = etree.Element(f"{_X}body", nsmap={None: _XHTML, "epub": _EPUB})
    section = etree.SubElement(body, f"{_X}section")
    if chapter.in_toc:
        section.set(_EPUB_TYPE, "chapter")
    fragment = etree.fromstring(f"<div>{chapter.html}</div>".encode())
    section.text = fragment.text
    for child in list(fragment):
        section.append(child)
    for el in section.iter():
        if isinstance(el.tag, str) and not el.tag.startswith("{"):
            el.tag = f"{_X}{el.tag}"
    for drop in [el for el in section.iter(f"{_X}span") if el.get("class") == DROP_CLASS]:
        _remove_keep_tail(drop)
    asides = []
    for n, span in enumerate(
        [el for el in section.iter(f"{_X}span") if el.get("class") == NOTE_CLASS], 1
    ):
        sup = etree.Element(f"{_X}sup", id=f"noteref-{n}")
        link = etree.SubElement(sup, f"{_X}a", {_EPUB_TYPE: "noteref", "href": f"#note-{n}"})
        link.text = str(n)
        sup.tail = span.tail
        span.getparent().replace(span, sup)
        aside = etree.Element(f"{_X}aside", {_EPUB_TYPE: "footnote", "id": f"note-{n}"})
        p = etree.SubElement(aside, f"{_X}p")
        back = etree.SubElement(p, f"{_X}a", href=f"#noteref-{n}")
        back.text = str(n)
        back.tail = " " + (span.text or "")
        for child in list(span):
            p.append(child)
        asides.append(aside)
    section.extend(asides)
    return body


def _fix_links(bodies: list[etree._Element], names: list[str]) -> None:
    """Point ``#id`` links at the chapter file holding the id; drop links to ids no file
    has, and links with a scheme other than http(s) or mailto."""
    where: dict[str, str] = {}
    for body, name in zip(bodies, names, strict=True):
        for el in body.iter():
            if isinstance(el.tag, str) and el.get("id"):
                where.setdefault(el.get("id"), name)
    for body, name in zip(bodies, names, strict=True):
        for a in body.iter(f"{_X}a"):
            href = a.get("href")
            if href is None or a.get(_EPUB_TYPE) == "noteref" or href.startswith("#note"):
                continue
            if href.startswith("#"):
                target = where.get(href[1:])
                if target is None:
                    del a.attrib["href"]
                elif target != name:
                    a.set("href", f"{target}{href}")
            elif urlparse(href).scheme not in _LINK_SCHEMES:
                del a.attrib["href"]


def _is_svg(el: etree._Element) -> bool:
    return isinstance(el.tag, str) and el.tag.startswith("{http://www.w3.org/2000/svg}")


def _remove_keep_tail(el: etree._Element) -> None:
    parent, prev = el.getparent(), el.getprevious()
    if el.tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


# --- pages ----------------------------------------------------------------------------


def _page(title: str, lang: str, kind: str | None, heading: str | None) -> etree._Element:
    html = etree.Element(f"{_X}html", nsmap={None: _XHTML, "epub": _EPUB})
    html.set(_XML_LANG, lang)
    html.set("lang", lang)
    head = etree.SubElement(html, f"{_X}head")
    etree.SubElement(head, f"{_X}title").text = title
    etree.SubElement(head, f"{_X}link", rel="stylesheet", type="text/css", href="style.css")
    body = etree.SubElement(html, f"{_X}body")
    section = etree.SubElement(body, f"{_X}section")
    if kind:
        section.set(_EPUB_TYPE, kind)
    if heading and kind not in ("cover",):
        section.set("aria-label", heading)
    return html


def _title_page(pub: Publication) -> etree._Element:
    page = _page(pub.title, pub.language, "titlepage", None)
    section = page.find(f"{_X}body/{_X}section")
    section.set("class", "titlepage")
    etree.SubElement(section, f"{_X}h1", {"class": "title"}).text = pub.title
    if pub.authors:
        etree.SubElement(section, f"{_X}p", {"class": "author"}).text = ", ".join(pub.authors)
    if pub.translator:
        text = word(pub.language, "translated_by", name=pub.translator)
        etree.SubElement(section, f"{_X}p", {"class": "translator"}).text = text
    if pub.publisher:
        etree.SubElement(section, f"{_X}p", {"class": "publisher"}).text = pub.publisher
    return page


def _copyright_page(pub: Publication) -> etree._Element:
    page = _page(word(pub.language, "copyright"), pub.language, "copyright-page", None)
    section = page.find(f"{_X}body/{_X}section")
    section.set("class", "copyright")
    lines = [pub.title, pub.rights]
    if pub.translator:
        lines.append(word(pub.language, "translated_by", name=pub.translator))
    lines += [pub.publisher, f"ISBN {pub.isbn_epub}" if pub.isbn_epub else None]
    for line in lines:
        if line:
            etree.SubElement(section, f"{_X}p").text = line
    return page


def _nav(pub: Publication, toc: list[tuple[str, str]], first: str | None, cover) -> etree._Element:
    page = _page(pub.title, pub.language, None, None)
    body = page.find(f"{_X}body")
    body.remove(body[0])
    nav = etree.SubElement(body, f"{_X}nav", {_EPUB_TYPE: "toc", "id": "toc"})
    etree.SubElement(nav, f"{_X}h1").text = word(pub.language, "contents")
    ol = etree.SubElement(nav, f"{_X}ol")
    for label, href in toc:
        li = etree.SubElement(ol, f"{_X}li")
        etree.SubElement(li, f"{_X}a", href=href).text = label
    marks = etree.SubElement(body, f"{_X}nav", {_EPUB_TYPE: "landmarks", "hidden": "hidden"})
    ol = etree.SubElement(marks, f"{_X}ol")
    entries = [("cover", "cover.xhtml", word(pub.language, "cover"))] if cover else []
    entries += [
        ("titlepage", "title.xhtml", word(pub.language, "title_page")),
        ("copyright-page", "copyright.xhtml", word(pub.language, "copyright")),
        ("toc", "nav.xhtml#toc", word(pub.language, "contents")),
    ]
    if first:
        entries.append(("bodymatter", first, pub.title))
    for kind, href, label in entries:
        li = etree.SubElement(ol, f"{_X}li")
        etree.SubElement(li, f"{_X}a", {_EPUB_TYPE: kind, "href": href}).text = label
    return page


def _ncx(pub: Publication, identifier: str, toc: list[tuple[str, str]]) -> etree._Element:
    q = lambda tag: f"{{{_NCX}}}{tag}"  # noqa: E731
    ncx = etree.Element(q("ncx"), nsmap={None: _NCX}, version="2005-1")
    head = etree.SubElement(ncx, q("head"))
    for name, content in (("dtb:uid", identifier), ("dtb:depth", "1")):
        etree.SubElement(head, q("meta"), name=name, content=content)
    for name in ("dtb:totalPageCount", "dtb:maxPageNumber"):
        etree.SubElement(head, q("meta"), name=name, content="0")
    etree.SubElement(etree.SubElement(ncx, q("docTitle")), q("text")).text = pub.title
    nav_map = etree.SubElement(ncx, q("navMap"))
    for n, (label, href) in enumerate(toc, 1):
        point = etree.SubElement(nav_map, q("navPoint"), id=f"np-{n}", playOrder=str(n))
        etree.SubElement(etree.SubElement(point, q("navLabel")), q("text")).text = label
        etree.SubElement(point, q("content"), src=href)
    return ncx


def _opf(
    pub: Publication,
    identifier: str,
    now: datetime,
    items: list[tuple[str, str, str, str | None]],
    spine: list[str],
) -> etree._Element:
    q = lambda tag: f"{{{_OPF}}}{tag}"  # noqa: E731
    dc = lambda tag: f"{{{_DC}}}{tag}"  # noqa: E731
    package = etree.Element(
        q("package"), nsmap={None: _OPF}, version="3.0", **{"unique-identifier": "pub-id"}
    )
    package.set(_XML_LANG, pub.language)
    meta = etree.SubElement(package, q("metadata"), nsmap={"dc": _DC})
    etree.SubElement(meta, dc("identifier"), id="pub-id").text = identifier
    etree.SubElement(meta, dc("title")).text = pub.title
    etree.SubElement(meta, dc("language")).text = pub.language
    for n, author in enumerate(pub.authors, 1):
        etree.SubElement(meta, dc("creator"), id=f"creator-{n}").text = author
        role = etree.SubElement(meta, q("meta"), refines=f"#creator-{n}", property="role")
        role.set("scheme", "marc:relators")
        role.text = "aut"
    if pub.translator:
        etree.SubElement(meta, dc("contributor"), id="translator").text = pub.translator
        role = etree.SubElement(meta, q("meta"), refines="#translator", property="role")
        role.set("scheme", "marc:relators")
        role.text = "trl"
    for tag, value in (
        ("publisher", pub.publisher),
        ("date", str(pub.year) if pub.year else None),
        ("description", pub.description),
        ("rights", pub.rights),
    ):
        if value:
            etree.SubElement(meta, dc(tag)).text = value
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    etree.SubElement(meta, q("meta"), property="dcterms:modified").text = stamp
    if any(item_id == "cover-image" for item_id, *_ in items):
        etree.SubElement(meta, q("meta"), name="cover", content="cover-image")
    manifest = etree.SubElement(package, q("manifest"))
    for item_id, href, media, props in items:
        item = etree.SubElement(manifest, q("item"), id=item_id, href=href)
        item.set("media-type", media)
        if props:
            item.set("properties", props)
    spine_el = etree.SubElement(package, q("spine"), toc="ncx")
    for idref in spine:
        etree.SubElement(spine_el, q("itemref"), idref=idref)
    return package


# --- package --------------------------------------------------------------------------


def _xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


_CONTAINER = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    b'<rootfiles><rootfile full-path="OEBPS/content.opf" '
    b'media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _zip(parts: dict[str, bytes], opf: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")  # stored, first
        entries = {"META-INF/container.xml": _CONTAINER, "OEBPS/content.opf": opf}
        entries |= {f"OEBPS/{name}": data for name, data in parts.items()}
        for name, data in entries.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
