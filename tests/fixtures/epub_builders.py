"""EPUB fixtures generated in code, and helpers to inspect EPUB output.

Paragraph mini-markup mirrors ``make_docx``: ``**bold**``, ``*italic*``, ``[text](url)``,
and ``[^n]`` (a footnote reference, an ``epub:type="noteref"`` link).
"""

import io
import re
import zipfile
from collections.abc import Callable
from typing import Literal
from xml.sax.saxutils import escape, quoteattr

from jerome.documents.epub_reader import read_epub
from jerome.documents.model import Mark, ParsedBlock, ParsedBook
from tests.fixtures.images import make_png

_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'
_XHTML_NS = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
_MARKUP = re.compile(
    r"\*\*(?P<bold>.+?)\*\*|\*(?P<italic>.+?)\*|\[\^(?P<note>\d+)\]"
    r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)"
)


def _inline(text: str, cluttered: bool, fmt: tuple[str, ...] = ()) -> str:
    out: list[str] = []
    pos = 0
    for m in _MARKUP.finditer(text):
        out.append(_leaf(text[pos : m.start()], cluttered, fmt))
        if m["bold"] is not None:
            out.append(_inline(m["bold"], cluttered, (*fmt, "b")))
        elif m["italic"] is not None:
            out.append(_inline(m["italic"], cluttered, (*fmt, "i")))
        elif m["note"] is not None:
            n = m["note"]
            out.append(f'<a epub:type="noteref" href="notes.xhtml#n{n}">{n}</a>')
        else:
            label = _inline(m["label"], cluttered, fmt)
            out.append(f"<a href={quoteattr(m['url'])}>{label}</a>")
        pos = m.end()
    out.append(_leaf(text[pos:], cluttered, fmt))
    return "".join(out)


def _wrap(text: str, fmt: tuple[str, ...]) -> str:
    return "".join(f"<{t}>" for t in fmt) + escape(text) + "".join(f"</{t}>" for t in reversed(fmt))


def _leaf(text: str, cluttered: bool, fmt: tuple[str, ...]) -> str:
    if not text:
        return ""
    if not cluttered:
        return _wrap(text, fmt)
    if fmt:  # InDesign splits a formatted word over adjacent elements
        k = max(1, len(text) // 2)
        return _wrap(text[:k], fmt) + _wrap(text[k:], fmt) if len(text) > 1 else _wrap(text, fmt)
    parts = []
    for token in re.split(r"( )", text):
        if token == " ":
            parts.append(' <span class="CharOverride-2"></span>')
        elif token:
            parts.append(f'<span class="CharOverride-1" xml:lang="en-US">{escape(token)}</span>')
    return "".join(parts)


def _page(body: str, *, body_attrs: str = "", title: str = "Chapter") -> str:
    return (
        f"{_DECL}<html {_XHTML_NS}><head><title>{escape(title)}</title></head>"
        f"<body{body_attrs}>{body}</body></html>"
    )


def make_epub(
    chapters: list[tuple[str, list[str]]],
    *,
    toc: Literal["nav", "ncx", "none"] = "nav",
    front_matter: bool = False,
    split_chapter: bool = False,
    two_in_one_file: bool = False,
    cluttered: bool = False,
    title: str = "Test Book",
    language: str = "en",
    creators: list[str] | None = None,
    publisher: str | None = None,
    date: str | None = None,
    cover: bytes | None = None,
    landmarks: bool = False,
    front_pages: bool = False,
    notes: dict[int, str] | None = None,
    images: bool = False,
) -> bytes:
    """Build an EPUB. Each chapter's title is its first block, an ``h1``.

    ``split_chapter`` puts the second half of every chapter's paragraphs in a second file
    the TOC does not list; ``two_in_one_file`` puts all chapters in one file, the TOC
    pointing at ``#c<n>`` anchors; ``front_matter`` adds a titlepage document first
    (``epub:type="titlepage"``, not in the TOC).

    ``cover`` adds a PNG cover image item (``cover-image``, ``<meta name="cover">``) and a
    plain ``cover.xhtml`` showing it, first in the spine. ``landmarks`` adds a landmarks nav
    (cover and toc) and puts ``nav.xhtml`` in the spine. ``front_pages`` adds a copyright
    page known only from the EPUB 2 guide, and a dedication (``epub:type="dedication"``).
    ``notes`` maps footnote numbers to text: each ``[^n]`` links to an
    ``<aside epub:type="footnote">`` at the end of its chapter, whose number links back.
    ``images`` puts a small PNG picture after each chapter heading.
    """
    files: dict[str, str] = {}  # file name in OEBPS -> xhtml
    binary: dict[str, tuple[bytes, str]] = {}  # file name in OEBPS -> (data, manifest props)
    spine: list[str] = []
    targets: list[str] = []  # TOC href per chapter
    if cover is not None:
        binary["images/cover.png"] = (cover, "cover-image")
        files["cover.xhtml"] = _page('<div><img src="images/cover.png" alt="Cover"/></div>')
        spine.append("cover.xhtml")

    def heading(i: int, name: str) -> str:
        return f'<h1 id="c{i}">{_inline(name, cluttered)}</h1>'

    def paragraphs(lines: list[str]) -> str:
        return "".join(f"<p>{_inline(line, cluttered)}</p>" for line in lines)

    def with_notes(body: str) -> str:
        if notes is None:
            return body
        asides = []
        for n in re.findall(r'href="notes\.xhtml#n(\d+)"', body):
            body = body.replace(f'href="notes.xhtml#n{n}"', f'id="r{n}" href="#n{n}"', 1)
            asides.append(
                f'<aside epub:type="footnote" id="n{n}"><p><a href="#r{n}">{n}</a> '
                f"{_inline(notes[int(n)], cluttered)}</p></aside>"
            )
        return body + "".join(asides)

    def picture(i: int) -> str:
        if not images:
            return ""
        binary[f"images/pic{i}.png"] = (make_png(5, 4), "")
        return f'<p><img src="images/pic{i}.png" alt="Picture {i}"/></p>'

    if front_matter:
        files["titlepage.xhtml"] = _page(
            '<section epub:type="titlepage"><h1>Title Page</h1><p>By Nobody</p></section>'
        )
        spine.append("titlepage.xhtml")
    if front_pages:
        files["copyright.xhtml"] = _page("<p>Copyright 2026 Nobody.</p>")
        files["dedication.xhtml"] = _page(
            '<section epub:type="dedication"><p>For my mother.</p></section>'
        )
        spine += ["copyright.xhtml", "dedication.xhtml"]
    front_count = len(spine)
    if two_in_one_file:
        body = with_notes(
            "".join(
                heading(i, name) + picture(i) + paragraphs(lines)
                for i, (name, lines) in enumerate(chapters, 1)
            )
        )
        files["book.xhtml"] = _page(body)
        spine.append("book.xhtml")
        targets = [f"book.xhtml#c{i}" for i in range(1, len(chapters) + 1)]
    else:
        for i, (name, lines) in enumerate(chapters, 1):
            half = (len(lines) + 1) // 2 if split_chapter else len(lines)
            files[f"ch{i}.xhtml"] = _page(
                with_notes(heading(i, name) + picture(i) + paragraphs(lines[:half]))
            )
            spine.append(f"ch{i}.xhtml")
            targets.append(f"ch{i}.xhtml")
            if split_chapter:
                files[f"ch{i}b.xhtml"] = _page(paragraphs(lines[half:]))
                spine.append(f"ch{i}b.xhtml")

    manifest = [f'<item id="{n}" href="{n}" media-type="application/xhtml+xml"/>' for n in files]
    manifest += [
        f'<item id="{n.replace("/", "-")}" href="{n}" media-type="image/png"'
        + (f' properties="{props}"' if props else "")
        + "/>"
        for n, (_, props) in binary.items()
    ]
    toc_attr = ""
    if toc == "nav":
        items = "".join(
            f"<li><a href={quoteattr(t)}>{escape(name)}</a></li>"
            for t, (name, _) in zip(targets, chapters, strict=True)
        )
        marks = ""
        if landmarks:
            marks = (
                '<nav epub:type="landmarks"><ol>'
                + ('<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>' if cover else "")
                + '<li><a epub:type="toc" href="nav.xhtml">Contents</a></li></ol></nav>'
            )
            spine.insert(front_count, "nav.xhtml")
        files["nav.xhtml"] = _page(f'<nav epub:type="toc"><ol>{items}</ol></nav>{marks}')
        manifest.append(
            '<item id="nav.xhtml" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>'
        )
    elif toc == "ncx":
        points = "".join(
            f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{escape(name)}</text></navLabel>'
            f"<content src={quoteattr(t)}/></navPoint>"
            for i, (t, (name, _)) in enumerate(zip(targets, chapters, strict=True), 1)
        )
        files["toc.ncx"] = (
            f'{_DECL}<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f"<head/><docTitle><text>{escape(title)}</text></docTitle><navMap>{points}</navMap></ncx>"
        )
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        toc_attr = ' toc="ncx"'
    itemrefs = "".join(f'<itemref idref="{n}"/>' for n in spine)
    opf = (
        f'{_DECL}<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">urn:uuid:jerome-test</dc:identifier>'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<dc:language>{language}</dc:language>"
        + "".join(f"<dc:creator>{escape(c)}</dc:creator>" for c in creators or [])
        + (f"<dc:publisher>{escape(publisher)}</dc:publisher>" if publisher else "")
        + (f"<dc:date>{escape(date)}</dc:date>" if date else "")
        + ('<meta name="cover" content="images-cover.png"/>' if cover else "")
        + "</metadata>"
        f"<manifest>{''.join(manifest)}</manifest><spine{toc_attr}>{itemrefs}</spine>"
        + (
            '<guide><reference type="copyright-page" title="Copyright" '
            'href="copyright.xhtml"/></guide>'
            if front_pages
            else ""
        )
        + "</package>"
    )
    container = (
        f'{_DECL}<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    entries = {
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
        **{f"OEBPS/{n}": xml for n, xml in files.items()},
        **{f"OEBPS/{n}": data for n, (data, _) in binary.items()},
    }
    return _zip(entries)


def _zip(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
        zf.writestr(info, b"application/epub+zip")  # stored, first
        for name, xml in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, xml.encode("utf-8") if isinstance(xml, str) else xml)
    return buf.getvalue()


def edit_entry(data: bytes, name: str, fn: Callable[[str], str]) -> bytes:
    """Rebuild the EPUB with ``fn`` applied to the text of entry ``name``."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries: dict[str, str | bytes] = {i.filename: zf.read(i) for i in zf.infolist()}
    del entries["mimetype"]
    entries[name] = fn(entries[name].decode("utf-8"))
    return _zip(entries)


# --- inspection helpers ---------------------------------------------------------------


def _book(data_or_book: bytes | ParsedBook) -> ParsedBook:
    return data_or_book if isinstance(data_or_book, ParsedBook) else read_epub(data_or_book)


def titles(data_or_book: bytes | ParsedBook) -> list[str]:
    return [c.title for c in _book(data_or_book).chapters]


def blocks_of(book: ParsedBook) -> list[tuple[str, list[Mark]]]:
    return [(b.text, b.marks) for c in book.chapters for b in c.blocks]


def block(data: bytes, chapter: int, block: int) -> ParsedBlock:
    return read_epub(data).chapters[chapter].blocks[block]


def block_key(data: bytes, chapter: int, block: int) -> str:
    return read_epub(data).chapters[chapter].blocks[block].key


def zip_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def zip_info(data: bytes, name: str) -> zipfile.ZipInfo:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.getinfo(name)


def _contents(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def unchanged_entries(a: bytes, b: bytes) -> set[str]:
    """Entry names present in both archives with identical bytes."""
    ca, cb = _contents(a), _contents(b)
    return {name for name, content in ca.items() if cb.get(name) == content}


def all_entries_except(data: bytes, name: str) -> set[str]:
    return set(zip_names(data)) - {name}
