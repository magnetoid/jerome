"""DOCX fixtures generated in code, and helpers to inspect DOCX output.

python-docx cannot write footnotes, custom outline levels or text boxes, so every
fixture is a zip of minimal hand-written WordprocessingML parts.
"""

import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from jerome.documents.docx_reader import read_docx
from jerome.documents.docx_xml import NOTE_PARTS, NS, iter_paragraphs, qn, read_package
from jerome.documents.model import ParsedBlock

_XMLNS = " ".join(
    f'xmlns:{prefix}="{uri}"'
    for prefix, uri in {
        "w": NS["w"],
        "r": NS["r"],
        "m": NS["m"],
        "mc": NS["mc"],
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        "v": "urn:schemas-microsoft-com:vml",
    }.items()
)
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
_WML_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml"
_SECT_PR = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" '
    'w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
)
_NOTE_SEPARATORS = (
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:type="continuationSeparator" w:id="0">'
    "<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
)
_NOTE_MARK = '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>'


# --- package --------------------------------------------------------------------------


def package_docx(
    body: str,
    *,
    footnotes: str | None = None,
    styles: str = "",
    links: dict[str, str] | None = None,
    footnote_links: dict[str, str] | None = None,
    title: str | None = None,
    language: str = "en-US",
    creator: str | None = "jerome tests",
) -> bytes:
    """Zip a minimal DOCX around raw XML.

    ``body`` is the content of ``w:body`` (a ``w:sectPr`` is appended), ``footnotes`` the
    normal ``w:footnote`` elements (separators are added), ``styles`` extra ``w:style``
    elements, and ``links``/``footnote_links`` map relationship ids to hyperlink URLs.
    """
    doc_rels = [("rId1", f"{_OFFICE_REL}/styles", "styles.xml", False)]
    overrides = {
        "/word/document.xml": f"{_WML_CT}.document.main+xml",
        "/word/styles.xml": f"{_WML_CT}.styles+xml",
        "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    }
    if footnotes is not None:
        doc_rels.append(("rId2", f"{_OFFICE_REL}/footnotes", "footnotes.xml", False))
        overrides["/word/footnotes.xml"] = f"{_WML_CT}.footnotes+xml"
    doc_rels += _hyperlink_rels(links or {})

    parts = {
        "[Content_Types].xml": _content_types(overrides),
        "_rels/.rels": _rels(
            [
                ("rId1", f"{_OFFICE_REL}/officeDocument", "word/document.xml", False),
                ("rId2", _CORE_REL, "docProps/core.xml", False),
            ]
        ),
        "docProps/core.xml": _core(title, creator),
        "word/document.xml": (
            f"{_DECL}<w:document {_XMLNS}><w:body>{body}{_SECT_PR}</w:body></w:document>"
        ),
        "word/_rels/document.xml.rels": _rels(doc_rels),
        "word/styles.xml": _styles(language, styles),
    }
    if footnotes is not None:
        parts["word/footnotes.xml"] = (
            f"{_DECL}<w:footnotes {_XMLNS}>{_NOTE_SEPARATORS}{footnotes}</w:footnotes>"
        )
        if footnote_links:
            parts["word/_rels/footnotes.xml.rels"] = _rels(_hyperlink_rels(footnote_links))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, xml in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, xml.encode("utf-8"))
    return buf.getvalue()


def _content_types(overrides: dict[str, str]) -> str:
    items = "".join(
        f'<Override PartName="{name}" ContentType="{ct}"/>' for name, ct in overrides.items()
    )
    return (
        f'{_DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>{items}</Types>'
    )


def _hyperlink_rels(links: dict[str, str]) -> list[tuple[str, str, str, bool]]:
    return [(rid, f"{_OFFICE_REL}/hyperlink", url, True) for rid, url in links.items()]


def _rels(rels: list[tuple[str, str, str, bool]]) -> str:
    items = "".join(
        f'<Relationship Id="{rid}" Type="{kind}" Target={quoteattr(target)}'
        + (' TargetMode="External"' if external else "")
        + "/>"
        for rid, kind, target, external in rels
    )
    return f'{_DECL}<Relationships xmlns="{NS["pr"]}">{items}</Relationships>'


def _core(title: str | None, creator: str | None) -> str:
    title_xml = f"<dc:title>{escape(title)}</dc:title>" if title is not None else ""
    creator_xml = f"<dc:creator>{escape(creator)}</dc:creator>" if creator is not None else ""
    return (
        f'{_DECL}<cp:coreProperties xmlns:cp="{NS["cp"]}" xmlns:dc="{NS["dc"]}">'
        f"{title_xml}{creator_xml}</cp:coreProperties>"
    )


def _styles(language: str, extra: str) -> str:
    return (
        f"{_DECL}<w:styles {_XMLNS}>"
        f'<w:docDefaults><w:rPrDefault><w:rPr><w:lang w:val="{language}"/></w:rPr>'
        "</w:rPrDefault><w:pPrDefault/></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style>'
        + "".join(
            f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:outlineLvl w:val="{n - 1}"/></w:pPr>'
            "<w:rPr><w:b/></w:rPr></w:style>"
            for n in (1, 2)
        )
        + '<w:style w:type="paragraph" w:styleId="FootnoteText"><w:name w:val="footnote text"/>'
        '<w:basedOn w:val="Normal"/></w:style>'
        '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
        '<w:name w:val="Default Paragraph Font"/></w:style>'
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
        '<w:style w:type="character" w:styleId="FootnoteReference">'
        '<w:name w:val="footnote reference"/>'
        '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
        f"{extra}</w:styles>"
    )


# --- mini-markup ----------------------------------------------------------------------

_HEADINGS = (("## ", "Heading2"), ("# ", "Heading1"))
_CUSTOM_STYLE = re.compile(r"@(\w+): (.*)", re.S)
_TABLE_ROW = re.compile(r"\|.*\|", re.S)
_MARKUP = re.compile(
    r"\*\*(?P<bold>.+?)\*\*|\*(?P<italic>.+?)\*|\[\^(?P<note>\d+)\]"
    r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)"
)


class _Links:
    """Hyperlink relationships of one part, numbered from ``first_id``."""

    def __init__(self, first_id: int):
        self.first_id = first_id
        self.targets: dict[str, str] = {}

    def add(self, url: str) -> str:
        rid = f"rId{self.first_id + len(self.targets)}"
        self.targets[rid] = url
        return rid


def make_docx(
    lines: list[str],
    footnotes: dict[int, str] | None = None,
    styles: dict[str, int] | None = None,
) -> bytes:
    """Build a DOCX from mini-markup lines, one paragraph (or table row) per line.

    ``# `` / ``## `` start Heading 1 / Heading 2; ``@Style: text`` uses a custom paragraph
    style whose outline level comes from ``styles``; ``|a|b|`` is a table row (consecutive
    rows form one table). Inline: ``**bold**``, ``*italic*``, ``[text](url)``, ``[^n]``
    (footnote reference) and ``\\t`` (tab). ``footnotes`` maps ids to note text, which uses
    the same inline markup.
    """
    links = _Links(first_id=3)  # rId1 is styles, rId2 footnotes
    body = _body(lines, links)

    notes_xml = None
    note_links = _Links(first_id=1)
    if footnotes:
        notes_xml = "".join(
            f'<w:footnote w:id="{n}"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>'
            f"{_NOTE_MARK}{_inline(' ' + text, note_links)}</w:p></w:footnote>"
            for n, text in sorted(footnotes.items())
        )

    custom = {m[1]: None for line in lines if (m := _CUSTOM_STYLE.fullmatch(line))}
    custom.update(styles or {})
    styles_xml = "".join(
        f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{name}">'
        f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
        + (f'<w:pPr><w:outlineLvl w:val="{level}"/></w:pPr>' if level is not None else "")
        + "</w:style>"
        for name, level in custom.items()
    )
    return package_docx(
        body,
        footnotes=notes_xml,
        styles=styles_xml,
        links=links.targets,
        footnote_links=note_links.targets,
    )


def _body(lines: list[str], links: _Links) -> str:
    out: list[str] = []
    rows: list[str] = []
    for line in lines:
        if _TABLE_ROW.fullmatch(line):
            rows.append(line)
            continue
        if rows:
            out.append(_table(rows, links))
            rows = []
        out.append(_paragraph(line, links))
    if rows:
        out.append(_table(rows, links))
        out.append("<w:p/>")  # Word always ends the body with a paragraph
    return "".join(out)


def _paragraph(line: str, links: _Links) -> str:
    style = None
    for prefix, style_id in _HEADINGS:
        if line.startswith(prefix):
            style, line = style_id, line[len(prefix) :]
            break
    else:
        if m := _CUSTOM_STYLE.fullmatch(line):
            style, line = m[1], m[2]
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_inline(line, links)}</w:p>"


def _table(rows: list[str], links: _Links) -> str:
    cells = [row[1:-1].split("|") for row in rows]
    grid = '<w:gridCol w:w="2000"/>' * max(len(r) for r in cells)
    trs = "".join(
        "<w:tr>"
        + "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>'
            f"<w:p>{_inline(cell, links)}</w:p></w:tc>"
            for cell in row
        )
        + "</w:tr>"
        for row in cells
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{trs}</w:tbl>"
    )


def _inline(text: str, links: _Links, fmt: tuple[str, ...] = ()) -> str:
    out: list[str] = []
    pos = 0
    for m in _MARKUP.finditer(text):
        out.append(_run(text[pos : m.start()], fmt))
        if m["bold"] is not None:
            out.append(_inline(m["bold"], links, (*fmt, "b")))
        elif m["italic"] is not None:
            out.append(_inline(m["italic"], links, (*fmt, "i")))
        elif m["note"] is not None:
            out.append(
                '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
                f'<w:footnoteReference w:id="{m["note"]}"/></w:r>'
            )
        else:
            rid = links.add(m["url"])
            label = _inline(m["label"], links, (*fmt, "link"))
            out.append(f'<w:hyperlink r:id="{rid}" w:history="1">{label}</w:hyperlink>')
        pos = m.end()
    out.append(_run(text[pos:], fmt))
    return "".join(out)


def _run(text: str, fmt: tuple[str, ...]) -> str:
    if not text:
        return ""
    props = (
        ('<w:rStyle w:val="Hyperlink"/>' if "link" in fmt else "")
        + ("<w:b/>" if "b" in fmt else "")
        + ("<w:i/>" if "i" in fmt else "")
    )
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    content = "<w:tab/>".join(
        f'<w:t xml:space="preserve">{escape(part)}</w:t>' if part else ""
        for part in text.split("\t")
    )
    return f"<w:r>{rpr}{content}</w:r>"


# --- special fixtures -----------------------------------------------------------------


def make_fragmented_docx() -> bytes:
    """One paragraph, "The quick brown fox.", stored the way Word does after editing.

    "quick" is split over four runs with ``w:proofErr``, a bookmark and an empty run between
    them; "brown" is bold.
    """
    body = (
        '<w:p w:rsidR="00A10B11" w:rsidRDefault="00A10B11">'
        '<w:r w:rsidRPr="00C2D301"><w:t xml:space="preserve">The </w:t></w:r>'
        '<w:proofErr w:type="spellStart"/>'
        '<w:r w:rsidRPr="00C2D302"><w:t>q</w:t></w:r>'
        '<w:bookmarkStart w:id="0" w:name="_GoBack"/>'
        '<w:r w:rsidRPr="00C2D303"><w:t>u</w:t></w:r>'
        '<w:bookmarkEnd w:id="0"/>'
        '<w:r w:rsidRPr="00C2D304"><w:rPr><w:lang w:val="en-GB"/></w:rPr><w:t/></w:r>'
        '<w:r w:rsidRPr="00C2D304"><w:t>i</w:t></w:r>'
        '<w:r w:rsidRPr="00C2D305"><w:t>ck</w:t></w:r>'
        '<w:proofErr w:type="spellEnd"/>'
        '<w:r><w:t xml:space="preserve"> </w:t></w:r>'
        '<w:r w:rsidRPr="00C2D306"><w:rPr><w:b/></w:rPr><w:t>brown</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> fox.</w:t></w:r>'
        "</w:p>"
    )
    return package_docx(body)


def make_tracked_docx() -> bytes:
    """A paragraph with an unresolved tracked insertion."""
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Some </w:t></w:r>'
        '<w:ins w:id="1" w:author="Editor" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t xml:space="preserve">inserted </w:t></w:r></w:ins>'
        "<w:r><w:t>text.</w:t></w:r></w:p>"
    )
    return package_docx(body)


def make_textbox_docx() -> bytes:
    """A paragraph anchoring a text box (DrawingML with a VML fallback), then a paragraph."""
    box = "<w:txbxContent><w:p><w:r><w:t>Inside the textbox.</w:t></w:r></w:p></w:txbxContent>"
    drawing = (
        '<w:drawing><wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" '
        'relativeHeight="251659264" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        '<wp:extent cx="1828800" cy="457200"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:wrapSquare wrapText="bothSides"/><wp:docPr id="1" name="Text Box 1"/>'
        "<wp:cNvGraphicFramePr/><a:graphic>"
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="1828800" cy="457200"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
        f"</a:prstGeom></wps:spPr><wps:txbx>{box}</wps:txbx><wps:bodyPr/></wps:wsp>"
        "</a:graphicData></a:graphic></wp:anchor></w:drawing>"
    )
    pict = (
        '<w:pict><v:shape id="Text Box 1" type="#_x0000_t202" style="width:144pt;height:36pt">'
        f"<v:textbox>{box}</v:textbox></v:shape></w:pict>"
    )
    body = (
        "<w:p><w:r><w:t>Body text with a box.</w:t></w:r><w:r><mc:AlternateContent>"
        f'<mc:Choice Requires="wps">{drawing}</mc:Choice><mc:Fallback>{pict}</mc:Fallback>'
        "</mc:AlternateContent></w:r></w:p>"
        "<w:p><w:r><w:t>After the box.</w:t></w:r></w:p>"
    )
    return package_docx(body)


# --- inspection helpers ---------------------------------------------------------------

_REVISIONS = tuple(qn(f"w:{tag}") for tag in ("ins", "del", "moveFrom", "moveTo"))


def all_blocks(data: bytes) -> list[ParsedBlock]:
    return [b for c in read_docx(data).chapters for b in c.blocks]


def only_block(data: bytes) -> ParsedBlock:
    blocks = all_blocks(data)
    assert len(blocks) == 1, [b.text for b in blocks]
    return blocks[0]


def part_xml(data: bytes, part: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(part).decode("utf-8")


def para_xml(data: bytes, n: int) -> bytes:
    """The serialized n-th body paragraph, i.e. the ``w:p`` of block key word/document.xml#n."""
    key = f"word/document.xml#{n}"
    for k, p, _ in iter_paragraphs(read_package(data)):
        if k == key:
            return etree.tostring(p)
    raise IndexError(key)


def revision_ids(data: bytes) -> list[str]:
    """``w:id`` of every tracked change in the document, footnotes and endnotes."""
    package = read_package(data)
    ids: list[str] = []
    for part in ("word/document.xml", *NOTE_PARTS):
        if part in package:
            ids += [el.get(qn("w:id")) for el in etree.fromstring(package[part]).iter(*_REVISIONS)]
    return ids


def custom_props(data: bytes) -> dict[str, str]:
    package = read_package(data)
    if "docProps/custom.xml" not in package:
        return {}
    root = etree.fromstring(package["docProps/custom.xml"])
    return {prop.get("name"): "".join(prop.itertext()) for prop in root.iterfind("op:property", NS)}
