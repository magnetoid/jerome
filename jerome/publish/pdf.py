"""The finished PDF: the shared form as one HTML document, rendered by WeasyPrint.

Rendering runs in a ``spawn`` child process with a time limit and (on Linux) a memory cap.
Its URL fetcher serves only files inside the render's temporary directory: the HTML, the
images and the fonts. Every other URL is refused and reported, never fetched.
"""

import multiprocessing
import sys
import tempfile
import traceback
from importlib.resources import files
from pathlib import Path
from urllib import request

from lxml import etree

from jerome.core.publication import Publication
from jerome.publish import FONT_DIR
from jerome.publish.model import (
    NOTE_CLASS,
    PubBook,
    PublishError,
    chapter_labels,
    word,
)

_TRIM = {  # CSS size, page width, page height, image max height (the text block, about)
    "6x9in": ("6in 9in", "6in", "9in", "7.2in"),
    "5.5x8.5in": ("5.5in 8.5in", "5.5in", "8.5in", "6.7in"),
    "5x8in": ("5in 8in", "5in", "8in", "6.2in"),
    "a5": ("148mm 210mm", "148mm", "210mm", "165mm"),
}
_COVER_EXT = {"image/jpeg": "jpg", "image/png": "png"}
TIMEOUT_MESSAGE = "Rendering the PDF took longer than 15 minutes"


def pdf_html(pub_book: PubBook, pub: Publication, cover: tuple[bytes, str] | None) -> str:
    """The whole document: cover, title page, copyright page, the untitled opening text,
    the contents, then the chapters."""
    html = etree.Element("html", lang=pub.language)
    head = etree.SubElement(html, "head")
    etree.SubElement(head, "meta", charset="utf-8")
    etree.SubElement(head, "title").text = pub.title
    if pub.authors:
        etree.SubElement(head, "meta", name="author", content=", ".join(pub.authors))
    if pub.description:
        etree.SubElement(head, "meta", name="description", content=pub.description)
    etree.SubElement(head, "style").text = _css(pub.trim_size)
    body = etree.SubElement(html, "body")

    if cover is not None:
        section = etree.SubElement(body, "section", {"class": "cover"})
        ext = _COVER_EXT.get(cover[1], "jpg")
        etree.SubElement(section, "img", src=f"images/cover.{ext}", alt="")
    _title_page(body, pub)
    _copyright_page(body, pub)

    labels = chapter_labels(pub_book)
    first_toc = next((i for i, c in enumerate(pub_book.chapters) if c.in_toc), None)
    for i, chapter in enumerate(pub_book.chapters):
        if i == first_toc:
            _contents(body, pub, pub_book, labels)
        section = etree.SubElement(body, "section", id=f"ch-{i + 1}")
        section.set("class", "chapter" if chapter.in_toc else "front")
        if chapter.in_toc:
            section.set("data-toc", "")
            section.set("data-title", labels[i])
            section.set("data-book", pub.title)
        fragment = etree.fromstring(f"<div>{chapter.html}</div>".encode())
        section.text = fragment.text
        for child in list(fragment):
            section.append(child)
    if first_toc is None:
        _contents(body, pub, pub_book, labels)
    for span in body.iter("span"):
        if span.get("class") == NOTE_CLASS:
            span.set("class", "footnote")
    return "<!DOCTYPE html>\n" + etree.tostring(html, encoding="unicode", method="html")


def _css(trim_size: str) -> str:
    size, width, height, img_height = _TRIM.get(trim_size, _TRIM["6x9in"])
    css = files("jerome.publish").joinpath("print.css").read_text(encoding="utf-8")
    for token, value in (
        ("__SIZE__", size),
        ("__WIDTH__", width),
        ("__HEIGHT__", height),
        ("__IMG_HEIGHT__", img_height),
    ):
        css = css.replace(token, value)
    return css


def _title_page(body: etree._Element, pub: Publication) -> None:
    section = etree.SubElement(body, "section", {"class": "titlepage"})
    etree.SubElement(section, "h1", {"class": "book-title"}).text = pub.title
    if pub.authors:
        etree.SubElement(section, "p", {"class": "author"}).text = ", ".join(pub.authors)
    if pub.translator:
        text = word(pub.language, "translated_by", name=pub.translator)
        etree.SubElement(section, "p", {"class": "translator"}).text = text
    if pub.publisher:
        etree.SubElement(section, "p", {"class": "publisher"}).text = pub.publisher


def _copyright_page(body: etree._Element, pub: Publication) -> None:
    section = etree.SubElement(body, "section", {"class": "copyright"})
    lines = [pub.rights]
    if pub.year and str(pub.year) not in pub.rights:
        lines.append(str(pub.year))
    if pub.translator:
        lines.append(word(pub.language, "translated_by", name=pub.translator))
    lines += [pub.publisher, f"ISBN {pub.isbn_pdf}" if pub.isbn_pdf else None]
    for line in lines:
        if line:
            etree.SubElement(section, "p").text = line


def _contents(body: etree._Element, pub: Publication, pub_book: PubBook, labels: list[str]):
    nav = etree.SubElement(body, "nav", {"class": "contents"})
    etree.SubElement(nav, "h2").text = word(pub.language, "contents")
    ol = etree.SubElement(nav, "ol")
    for i, chapter in enumerate(pub_book.chapters):
        if chapter.in_toc:
            li = etree.SubElement(ol, "li")
            etree.SubElement(li, "a", href=f"#ch-{i + 1}").text = labels[i]


# --- rendering ------------------------------------------------------------------------


def render_pdf(
    pub_book: PubBook,
    pub: Publication,
    cover: tuple[bytes, str] | None,
    *,
    timeout: float = 900,
    memory: int = 2 * 1024**3,
) -> bytes:
    """Render in a child process. URLs the renderer was refused become warnings on
    ``pub_book``. Raises PublishError on a timeout or any failure of the render."""
    with tempfile.TemporaryDirectory(prefix="jerome-pdf-") as tmp:
        root = Path(tmp)
        (root / "images").mkdir()
        (root / "fonts").mkdir()
        for font in FONT_DIR.glob("*.ttf"):
            (root / "fonts" / font.name).write_bytes(font.read_bytes())
        for image in pub_book.images.values():
            (root / "images" / image.name).write_bytes(image.data)
        if cover is not None:
            (root / "images" / f"cover.{_COVER_EXT.get(cover[1], 'jpg')}").write_bytes(cover[0])
        (root / "book.html").write_text(pdf_html(pub_book, pub, cover), encoding="utf-8")

        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        child = ctx.Process(target=_render_child, args=(str(root), memory, queue), daemon=True)
        child.start()
        child.join(timeout)
        if child.is_alive():
            child.kill()
            child.join()
            raise PublishError(TIMEOUT_MESSAGE)
        try:
            status, detail = queue.get(timeout=5)
        except Exception as exc:
            raise PublishError(
                f"Rendering the PDF failed (the renderer stopped, exit code {child.exitcode})"
            ) from exc
        if status != "ok":
            raise PublishError(f"Rendering the PDF failed: {detail}")
        for url in dict.fromkeys(detail):
            pub_book.warnings.append(f"The PDF left out a resource it may not fetch: {url}")
        return (root / "book.pdf").read_bytes()


def _render_child(directory: str, memory: int, queue) -> None:
    """In the child: cap memory (Linux), render book.html to book.pdf, report back."""
    try:
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        import weasyprint

        root = Path(directory)
        fetcher = local_fetcher(root)
        document = weasyprint.HTML(filename=str(root / "book.html"), url_fetcher=fetcher)
        document.write_pdf(str(root / "book.pdf"))
        queue.put(("ok", fetcher.refused))
    except MemoryError:
        queue.put(("error", "the book needs more than the memory allowed for rendering"))
    except BaseException as exc:  # noqa: BLE001 - everything goes back to the parent
        lines = traceback.format_exception_only(type(exc), exc)
        queue.put(("error", lines[-1].strip() if lines else repr(exc)))


def local_fetcher(root: Path):
    """A WeasyPrint URL fetcher that serves files inside ``root`` only, and records and
    refuses every other URL (in ``.refused``). Imports WeasyPrint, so it needs Pango."""
    return _local_fetcher_class()(root)


_LOCAL_FETCHER = None


def _local_fetcher_class():
    global _LOCAL_FETCHER
    if _LOCAL_FETCHER is None:
        import mimetypes

        from weasyprint.urls import URLFetcher, URLFetcherResponse

        class LocalFetcher(URLFetcher):
            def __init__(self, root: Path):
                super().__init__(allowed_protocols=("file",))
                self.root = Path(root).resolve()
                self.refused: list[str] = []

            def fetch(self, url, headers=None):
                if url.startswith("file:"):
                    raw = url.split("?")[0].split("#")[0].removeprefix("file:")
                    path = Path(request.url2pathname(raw)).resolve()
                    if path.is_relative_to(self.root) and path.is_file():
                        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                        return URLFetcherResponse(url, path.read_bytes(), {"Content-Type": media})
                self.refused.append(url)
                raise ValueError(f"Jerome does not fetch {url}")

        _LOCAL_FETCHER = LocalFetcher
    return _LOCAL_FETCHER
