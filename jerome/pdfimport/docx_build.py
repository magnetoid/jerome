"""Write assembled PDF blocks as a DOCX, which then goes through the ordinary DOCX import."""

import io
import re
import zipfile

from lxml import etree
from PIL import Image

from jerome.documents.docx_xml import NS
from jerome.pdfimport.model import PageBlock, Run

_W = NS["w"]
_R = NS["r"]
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_XML = "http://www.w3.org/XML/1998/namespace"
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
_PKG_REL = NS["pr"]
_WML_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml"

_EMU_PER_PX = 9525  # 96 dpi
# A4 with 1440-twip margins: the text area, in EMU (1 twip = 635 EMU)
_TEXT_WIDTH = (11906 - 2 * 1440) * 635
_TEXT_HEIGHT = (16838 - 2 * 1440) * 635
_FALLBACK_PX = (400, 300)
_MEDIA_EXT = {"image/png": "png", "image/jpeg": "jpg"}


_TO_SPACE = dict.fromkeys(map(ord, "\n\r\x0c"), " ")
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _clean(text: str) -> str:
    """Text XML 1.0 can hold: line breaks and form feeds become spaces, other controls go."""
    return _INVALID_XML.sub("", text.translate(_TO_SPACE))


def _w(tag: str) -> str:
    return f"{{{_W}}}{tag}"


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, _w(tag))
    for key, value in attrs.items():
        el.set(_w(key), value)
    return el


def _xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _text_run(parent: etree._Element, run: Run) -> None:
    r = _sub(parent, "r")
    if run.italic or run.bold:
        rpr = _sub(r, "rPr")
        if run.bold:
            _sub(rpr, "b")
        if run.italic:
            _sub(rpr, "i")
    t = _sub(r, "t")
    t.set(f"{{{_XML}}}space", "preserve")
    t.text = _clean(run.text)


def _add_runs(p: etree._Element, runs: list[Run]) -> None:
    for run in runs:
        if run.note is not None:
            r = _sub(p, "r")
            _sub(_sub(r, "rPr"), "rStyle", val="FootnoteReference")
            _sub(r, "footnoteReference", id=str(run.note))
        elif run.text:
            _text_run(p, run)


def _picture(p: etree._Element, rid: str, n: int, size_px: tuple[int, int]) -> None:
    width, height = (px * _EMU_PER_PX for px in size_px)
    scale = min(1.0, _TEXT_WIDTH / width, _TEXT_HEIGHT / height)
    cx, cy = max(1, round(width * scale)), max(1, round(height * scale))
    r = _sub(p, "r")
    drawing = _sub(r, "drawing")
    inline = etree.SubElement(drawing, f"{{{_WP}}}inline")
    etree.SubElement(inline, f"{{{_WP}}}extent", cx=str(cx), cy=str(cy))
    etree.SubElement(inline, f"{{{_WP}}}docPr", id=str(n), name=f"Picture {n}", descr="")
    graphic = etree.SubElement(inline, f"{{{_A}}}graphic")
    data = etree.SubElement(graphic, f"{{{_A}}}graphicData", uri=_PIC)
    pic = etree.SubElement(data, f"{{{_PIC}}}pic")
    nv = etree.SubElement(pic, f"{{{_PIC}}}nvPicPr")
    etree.SubElement(nv, f"{{{_PIC}}}cNvPr", id=str(n), name=f"image{n}")
    etree.SubElement(nv, f"{{{_PIC}}}cNvPicPr")
    fill = etree.SubElement(pic, f"{{{_PIC}}}blipFill")
    etree.SubElement(fill, f"{{{_A}}}blip").set(f"{{{_R}}}embed", rid)
    stretch = etree.SubElement(fill, f"{{{_A}}}stretch")
    etree.SubElement(stretch, f"{{{_A}}}fillRect")
    sppr = etree.SubElement(pic, f"{{{_PIC}}}spPr")
    xfrm = etree.SubElement(sppr, f"{{{_A}}}xfrm")
    etree.SubElement(xfrm, f"{{{_A}}}off", x="0", y="0")
    etree.SubElement(xfrm, f"{{{_A}}}ext", cx=str(cx), cy=str(cy))
    geom = etree.SubElement(sppr, f"{{{_A}}}prstGeom", prst="rect")
    etree.SubElement(geom, f"{{{_A}}}avLst")


def _pixel_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return _FALLBACK_PX
    return (width, height) if width > 0 and height > 0 else _FALLBACK_PX


def _rels(rels: list[tuple[str, str, str]]) -> bytes:
    root = etree.Element(f"{{{_PKG_REL}}}Relationships", nsmap={None: _PKG_REL})
    for rid, kind, target in rels:
        etree.SubElement(root, f"{{{_PKG_REL}}}Relationship", Id=rid, Type=kind, Target=target)
    return _xml(root)


def _content_types(extensions: set[str]) -> bytes:
    ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    root = etree.Element(f"{{{ns}}}Types", nsmap={None: ns})
    defaults = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml",
        "png": "image/png",
        "jpg": "image/jpeg",
    }
    for ext, ct in defaults.items():
        if ext in ("rels", "xml") or ext in extensions:
            etree.SubElement(root, f"{{{ns}}}Default", Extension=ext, ContentType=ct)
    overrides = {
        "/word/document.xml": f"{_WML_CT}.document.main+xml",
        "/word/styles.xml": f"{_WML_CT}.styles+xml",
        "/word/footnotes.xml": f"{_WML_CT}.footnotes+xml",
        "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    }
    for part, ct in overrides.items():
        etree.SubElement(root, f"{{{ns}}}Override", PartName=part, ContentType=ct)
    return _xml(root)


def _core(title: str, language: str) -> bytes:
    cp, dc = NS["cp"], NS["dc"]
    root = etree.Element(f"{{{cp}}}coreProperties", nsmap={"cp": cp, "dc": dc})
    etree.SubElement(root, f"{{{dc}}}title").text = _clean(title)
    etree.SubElement(root, f"{{{dc}}}language").text = _clean(language)
    return _xml(root)


def _styles(language: str) -> bytes:
    root = etree.Element(_w("styles"), nsmap={"w": _W})
    defaults = _sub(root, "docDefaults")
    _sub(_sub(_sub(defaults, "rPrDefault"), "rPr"), "lang", val=_clean(language))
    _sub(defaults, "pPrDefault")

    def style(kind: str, style_id: str, name: str, *, default: bool = False) -> etree._Element:
        attrs = {"type": kind, "styleId": style_id}
        if default:
            attrs["default"] = "1"
        el = _sub(root, "style", **attrs)
        _sub(el, "name", val=name)
        return el

    normal = style("paragraph", "Normal", "Normal", default=True)
    _sub(normal, "qFormat")
    for n in (1, 2, 3):
        el = style("paragraph", f"Heading{n}", f"heading {n}")
        _sub(el, "basedOn", val="Normal")
        _sub(el, "next", val="Normal")
        _sub(el, "qFormat")
        ppr = _sub(el, "pPr")
        _sub(ppr, "keepNext")
        _sub(ppr, "outlineLvl", val=str(n - 1))
        _sub(_sub(el, "rPr"), "b")
    note = style("paragraph", "FootnoteText", "footnote text")
    _sub(note, "basedOn", val="Normal")
    style("character", "DefaultParagraphFont", "Default Paragraph Font", default=True)
    ref = style("character", "FootnoteReference", "footnote reference")
    _sub(_sub(ref, "rPr"), "vertAlign", val="superscript")
    return _xml(root)


def build_docx(blocks: list[PageBlock], *, title: str, language: str) -> bytes:
    """The blocks as a DOCX: headings, italic/bold runs, footnotes and embedded images."""
    doc = etree.Element(_w("document"), nsmap={"w": _W, "r": _R})
    body = _sub(doc, "body")
    notes = etree.Element(_w("footnotes"), nsmap={"w": _W, "r": _R})
    for kind, note_id, text in (
        ("separator", "-1", "separator"),
        ("continuationSeparator", "0", "continuationSeparator"),
    ):
        fn = _sub(notes, "footnote", type=kind, id=note_id)
        _sub(_sub(_sub(fn, "p"), "r"), text)

    media: dict[str, bytes] = {}
    rels = [
        ("rId1", f"{_OFFICE_REL}/styles", "styles.xml"),
        ("rId2", f"{_OFFICE_REL}/footnotes", "footnotes.xml"),
    ]
    for block in blocks:
        if block.kind == "image":
            ext = _MEDIA_EXT.get(block.image_type or "")
            if block.image is None or ext is None:
                continue
            n = len(media) + 1
            target = f"media/image{n}.{ext}"
            media[f"word/{target}"] = block.image
            rid = f"rIdImg{n}"
            rels.append((rid, f"{_OFFICE_REL}/image", target))
            _picture(_sub(body, "p"), rid, n, _pixel_size(block.image))
        elif block.kind == "footnote":
            fn = _sub(notes, "footnote", id=str(block.note))
            p = _sub(fn, "p")
            _sub(_sub(p, "pPr"), "pStyle", val="FootnoteText")
            r = _sub(p, "r")
            _sub(_sub(r, "rPr"), "rStyle", val="FootnoteReference")
            _sub(r, "footnoteRef")
            _add_runs(p, [Run(" ")] + block.runs)
        else:
            p = _sub(body, "p")
            if block.kind == "heading":
                _sub(_sub(p, "pPr"), "pStyle", val=f"Heading{min(max(block.level, 1), 3)}")
            _add_runs(p, block.runs)
    sect = _sub(body, "sectPr")
    _sub(sect, "pgSz", w="11906", h="16838")
    _sub(
        sect,
        "pgMar",
        top="1440",
        right="1440",
        bottom="1440",
        left="1440",
        header="708",
        footer="708",
        gutter="0",
    )

    parts = {
        "[Content_Types].xml": _content_types({name.rsplit(".", 1)[1] for name in media}),
        "_rels/.rels": _rels(
            [
                ("rId1", f"{_OFFICE_REL}/officeDocument", "word/document.xml"),
                ("rId2", _CORE_REL, "docProps/core.xml"),
            ]
        ),
        "docProps/core.xml": _core(title, language),
        "word/document.xml": _xml(doc),
        "word/_rels/document.xml.rels": _rels(rels),
        "word/styles.xml": _styles(language),
        "word/footnotes.xml": _xml(notes),
        **media,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(
                zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED
            )
    return buf.getvalue()
