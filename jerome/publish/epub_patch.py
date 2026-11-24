"""The finished EPUB of an EPUB-born book: its clean export, with the publication details
written into the OPF and the uploaded cover put in. The publisher's design stays: every
entry but the OPF (and the NCX's identifier, the cover image, and a page showing a cover of
another type) keeps its bytes."""

import posixpath
import re

from lxml import etree

from jerome.core.publication import Publication
from jerome.documents.docx_xml import write_package
from jerome.documents.epub_xml import (
    NS,
    ContentDocument,
    EpubPackage,
    local_name,
    parse_content_document,
    parse_xml_root,
    read_container,
    resolve_href,
    serialize_document,
)

_OPF = f"{{{NS['opf']}}}"
_DC = f"{{{NS['dc']}}}"
_NCX = f"{{{NS['ncx']}}}"
_OPF_ROLE = f"{_OPF}role"  # EPUB 2: <dc:creator opf:role="aut">
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_EXT = {"image/jpeg": "jpg", "image/png": "png"}
# fields the import fills in from the book: one left empty was cleared, so the book's own goes
CLEARED_WHEN_EMPTY = ("publisher", "date")


def epub_identifier(epub: bytes) -> str | None:
    """The book's own unique identifier (the ``dc:identifier`` the package names)."""
    package = read_container(epub)
    opf = parse_xml_root(package.files[package.opf_path], package.opf_path)
    el = _unique_identifier(opf)
    text = " ".join("".join(el.itertext()).split()) if el is not None else ""
    return text or None


def patch_epub(
    clean_epub: bytes,
    pub: Publication,
    *,
    identifier: str,
    cover: tuple[bytes, str] | None,
) -> bytes:
    package = read_container(clean_epub)
    opf = parse_xml_root(package.files[package.opf_path], package.opf_path)
    epub3 = opf.get("version", "").startswith("3")
    metadata = opf.find(f"{_OPF}metadata")
    if metadata is None:
        metadata = etree.Element(f"{_OPF}metadata", nsmap={"dc": NS["dc"]})
        opf.insert(0, metadata)

    ident = _unique_identifier(opf)
    if ident is None:
        ident = _new(metadata, "identifier")
        ident.set("id", opf.get("unique-identifier") or "pub-id")
        opf.set("unique-identifier", ident.get("id"))
    _set_text(ident, identifier)
    _set_ncx_uid(package, identifier)
    if pub.authors:
        _set_people(metadata, "creator", pub.authors, "aut", epub3)
    if pub.translator:
        _set_people(metadata, "contributor", [pub.translator], "trl", epub3)
    for name, value in (
        ("publisher", pub.publisher),
        ("date", str(pub.year) if pub.year else None),
        ("description", pub.description),
        ("rights", pub.rights or None),
    ):
        if value is not None:
            el = metadata.find(f"{_DC}{name}")
            _set_text(el if el is not None else _new(metadata, name), value)
        elif name in CLEARED_WHEN_EMPTY:  # the import fills these in: empty means cleared
            _remove_all(metadata, name)
    if cover is not None:
        _set_cover(package, opf, metadata, cover, epub3)
    path = package.opf_path
    package.files[path] = serialize_document(ContentDocument(opf, "xml"), package.files[path], path)
    return write_package(package.files, clean_epub)


def _set_ncx_uid(package: EpubPackage, identifier: str) -> None:
    """Keep the NCX's ``dtb:uid`` the same as the package identifier (EPUBCheck NCX-001)."""
    for item in package.manifest.values():
        original = package.files.get(item.path)
        if item.media_type != "application/x-dtbncx+xml" or original is None:
            continue
        root = parse_xml_root(original, item.path)
        uids = [m for m in root.iterfind(f"{_NCX}head/{_NCX}meta") if m.get("name") == "dtb:uid"]
        if uids and any(m.get("content") != identifier for m in uids):
            for m in uids:
                m.set("content", identifier)
            doc = ContentDocument(root, "xml")
            package.files[item.path] = serialize_document(doc, original, item.path)


def _remove_all(metadata: etree._Element, name: str) -> None:
    """Remove every ``dc:<name>``, and the metas that refine them."""
    for el in metadata.findall(f"{_DC}{name}"):
        ident = el.get("id")
        if ident:
            for meta in metadata.findall(f"{_OPF}meta"):
                if meta.get("refines") == f"#{ident}":
                    metadata.remove(meta)
        metadata.remove(el)


def _unique_identifier(opf: etree._Element) -> etree._Element | None:
    wanted = opf.get("unique-identifier")
    found = opf.findall(f"{_OPF}metadata/{_DC}identifier")
    return next((el for el in found if el.get("id") == wanted), found[0] if found else None)


def _new(metadata: etree._Element, name: str) -> etree._Element:
    """A new ``dc:`` element after the last one of its kind, else at the end."""
    el = etree.Element(f"{_DC}{name}", nsmap={"dc": NS["dc"]})
    same = metadata.findall(f"{_DC}{name}")
    if same:
        same[-1].addnext(el)
        el.tail = same[-1].tail
    else:
        metadata.append(el)
    return el


def _set_text(el: etree._Element, text: str) -> None:
    for child in list(el):
        el.remove(child)
    el.text = text


def _roles(metadata: etree._Element, el: etree._Element) -> list[etree._Element]:
    """The EPUB 3 ``meta refines`` elements about ``el``."""
    ident = el.get("id")
    if not ident:
        return []
    return [m for m in metadata.findall(f"{_OPF}meta") if m.get("refines") == f"#{ident}"]


def _role_of(metadata: etree._Element, el: etree._Element) -> str | None:
    role = el.get(_OPF_ROLE)
    for meta in _roles(metadata, el):
        if meta.get("property") == "role":
            role = (meta.text or "").strip()
    return role


def _set_people(
    metadata: etree._Element, name: str, people: list[str], role: str, epub3: bool
) -> None:
    """Replace the ``dc:creator`` (all of them) or the translators among the
    ``dc:contributor`` elements with ``people``, where the first of them stood."""
    old = [
        el
        for el in metadata.findall(f"{_DC}{name}")
        if name == "creator" or _role_of(metadata, el) == role
    ]
    anchor = old[0] if old else None
    taken = {el.get("id") for el in metadata.iter() if isinstance(el.tag, str)} - {None}
    new_els = []
    for n, person in enumerate(people, 1):
        el = etree.Element(f"{_DC}{name}", nsmap={"dc": NS["dc"]})
        el.text = person
        ident = f"jerome-{role}-{n}"
        while ident in taken:
            ident += "x"
        el.set("id", ident)
        if not epub3:
            el.set(_OPF_ROLE, role)
        new_els.append(el)
    if anchor is not None:
        for el in new_els:
            anchor.addprevious(el)
            el.tail = anchor.tail
    else:
        last = metadata.findall(f"{_DC}*")
        for el in new_els:
            if last:
                last[-1].addnext(el)
            else:
                metadata.append(el)
            last = [el]
    for el in old:
        for meta in _roles(metadata, el):
            metadata.remove(meta)
        metadata.remove(el)
    if epub3:
        for el in reversed(new_els):
            meta = etree.Element(f"{_OPF}meta", refines=f"#{el.get('id')}", property="role")
            meta.set("scheme", "marc:relators")
            meta.text = role
            el.addnext(meta)
            meta.tail = el.tail


def _set_cover(
    package: EpubPackage,
    opf: etree._Element,
    metadata: etree._Element,
    cover: tuple[bytes, str],
    epub3: bool,
) -> None:
    data, media_type = cover
    manifest = opf.find(f"{_OPF}manifest")
    items = {el.get("id"): el for el in manifest.findall(f"{_OPF}item")}
    old = items.get(package.cover_item or "")
    if old is not None and old.get("media-type") == media_type:
        package.files[package.manifest[old.get("id")].path] = data
        return
    item_id, n = "jerome-cover", 1
    while item_id in items:
        n += 1
        item_id = f"jerome-cover-{n}"
    base = posixpath.dirname(package.opf_path)
    href = f"jerome-cover.{_EXT.get(media_type, 'img')}"
    while posixpath.join(base, href) in package.files:
        href = "x" + href
    full = posixpath.join(base, href)
    package.files[full] = data
    item = etree.SubElement(manifest, f"{_OPF}item", id=item_id, href=href)
    item.set("media-type", media_type)
    if epub3:
        item.set("properties", "cover-image")
    if old is not None:
        props = [p for p in (old.get("properties") or "").split() if p != "cover-image"]
        if props:
            old.set("properties", " ".join(props))
        else:
            old.attrib.pop("properties", None)
        _repoint_pages(package, package.manifest[old.get("id")].path, full)
    meta = next((m for m in metadata.findall(f"{_OPF}meta") if m.get("name") == "cover"), None)
    if meta is None:
        meta = etree.SubElement(metadata, f"{_OPF}meta", name="cover")
    meta.set("content", item_id)


def _repoint_pages(package: EpubPackage, old_path: str, new_path: str) -> None:
    """Make every content document that shows the old cover image show the new one."""
    for item in package.manifest.values():
        if item.media_type not in ("application/xhtml+xml", "text/html"):
            continue
        original = package.files.get(item.path)
        if original is None or old_path.rpartition("/")[2].encode() not in original:
            continue
        doc = parse_content_document(original, item.path)
        changed = False
        for el in doc.root.iter():
            name = local_name(el)
            attrs = ["src"] if name == "img" else [_XLINK_HREF, "href"] if name == "image" else []
            for attr in attrs:
                ref = el.get(attr)
                if ref and not re.match(r"^[a-z]+:", ref):
                    if resolve_href(item.path, ref)[0] == old_path:
                        base = posixpath.dirname(item.path) or "."
                        el.set(attr, posixpath.relpath(new_path, base))
                        changed = True
        if changed:
            package.files[item.path] = serialize_document(doc, original, item.path)
