"""Making a book's finished file, in two steps.

``prepare`` uses the database and is quick: it checks the book may be published, makes the
clean export, turns it into the shared form and checks its text against the reviewed text.
``render`` uses no database and may be slow: it writes the EPUB or renders the PDF. The
publish job runs them apart, so no transaction stays open while a PDF renders; the CLI runs
them one after the other."""

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.exports import EPUB_MEDIA_TYPE, ExportFile, export_book
from jerome.core.publication import Publication, publication_of, read_cover
from jerome.core.translation import ensure_not_source, untranslated_conflict
from jerome.documents.docx_reader import read_docx
from jerome.documents.epub_reader import read_epub
from jerome.documents.epub_xml import read_container
from jerome.errors import Conflict, NotFound
from jerome.orm import Block, Book, Chapter, Job, Suggestion
from jerome.publish.epub_book import book_identifier, write_epub_book
from jerome.publish.epub_patch import epub_identifier, patch_epub
from jerome.publish.from_docx import docx_to_pub
from jerome.publish.from_epub import epub_to_pub
from jerome.publish.model import PubBook, PublishError, PublishTextError  # noqa: F401
from jerome.publish.pdf import render_pdf
from jerome.publish.textcheck import check_text
from jerome.storage import Storage

PublishFormat = Literal["epub-book", "pdf-book"]
PUBLISH_FORMATS: tuple[PublishFormat, ...] = ("epub-book", "pdf-book")
PDF_MEDIA_TYPE = "application/pdf"
MB = 1024 * 1024
IMAGE_LIMIT = 200 * MB  # all of a book's images, cover included
NO_AUTHOR = "Set the author under Publication first"
NO_COVER = "The book has no cover"
EPUBCHECK_TIMEOUT = 300  # seconds
_EXT = {"epub-book": "epub", "pdf-book": "pdf"}


@dataclass
class Prepared:
    fmt: PublishFormat
    book_id: int
    filename_stem: str
    pub: Publication
    pub_book: PubBook | None  # None for the EPUB of an EPUB-born book: its own files are kept
    clean: bytes
    identifier: str
    cover: tuple[bytes, str] | None
    warnings: list[str] = field(default_factory=list)

    @property
    def extension(self) -> str:
        return _EXT[self.fmt]


def check_publishable(session: Session, book_id: int) -> Book:
    """The book, if a finished file of it may be made: not a hidden source, every paragraph
    of a translation translated, an author set. Raises Conflict otherwise."""
    ensure_not_source(session, book_id)
    book = get_book(session, book_id)
    conflict = untranslated_conflict(session, book)
    if conflict is not None:
        raise conflict
    if not [a for a in book.authors or [] if a]:
        raise Conflict(NO_AUTHOR)
    return book


def prepare(session: Session, storage: Storage, book_id: int, fmt: PublishFormat) -> Prepared:
    if fmt not in PUBLISH_FORMATS:
        raise PublishError(f"unknown publish format {fmt!r}")
    book = check_publishable(session, book_id)
    pub = publication_of(session, book)
    clean = export_book(
        session, storage, book_id, "docx-clean" if book.format == "docx" else "epub-clean"
    ).data
    cover = read_cover(storage, book)
    if book.format == "epub" and not book.rights:
        # never the computed "© year authors" over the book's own line (a public-domain
        # notice, say); a line set under Publication always wins
        own = read_container(clean).rights
        if own:
            pub.rights = own
    warnings: list[str] = []
    pub_book: PubBook | None = None
    if book.format == "epub" and fmt == "epub-book":
        if cover is None and read_container(clean).cover_item is None:
            warnings.append(NO_COVER)
    else:
        if cover is None:
            warnings.append(NO_COVER)
        if book.format == "docx":
            pub_book = docx_to_pub(clean, pub.language)
            check_text(pub_book, read_docx(clean))
        else:
            pub_book, documents, notes = epub_to_pub(clean, pub.language)
            check_text(pub_book, read_epub(clean), documents=documents, notes=notes)
        if pub_book.left_out:
            warnings.append(
                "Left out of the finished file (Jerome makes its own front pages and "
                f"contents): {', '.join(pub_book.left_out)}"
            )
    images = sum(len(i.data) for i in pub_book.images.values()) if pub_book else 0
    images += len(cover[0]) if cover else 0
    if images > IMAGE_LIMIT:
        raise PublishError(
            f"The book has {math.ceil(images / MB)} MB of images; "
            f"the limit is {IMAGE_LIMIT // MB} MB"
        )
    warnings += _review_warnings(session, book_id)
    if pub_book is not None:
        warnings += pub_book.warnings
        pub_book.warnings = []  # from here on it collects what rendering adds
    own_epub = book.format == "epub" and book.work != "translate"  # a translation is new
    existing = epub_identifier(clean) if own_epub else None
    return Prepared(
        fmt=fmt,
        book_id=book_id,
        filename_stem=PurePath(book.original_filename or "book").stem or "book",
        pub=pub,
        pub_book=pub_book,
        clean=clean,
        identifier=book_identifier(book_id, pub, existing, created=book.created_at),
        cover=cover,
        warnings=warnings,
    )


def _review_warnings(session: Session, book_id: int) -> list[str]:
    """What is not finished in the review. The owner decides when a book is ready, so these
    warn and never refuse."""
    warnings = []
    open_n = session.scalar(
        select(func.count())
        .select_from(Suggestion)
        .where(Suggestion.book_id == book_id, Suggestion.status == "open")
    )
    if open_n:
        warnings.append(f"{open_n} suggestions are still open")
    with_text = select(Block.id).where(Block.chapter_id == Chapter.id).exists()
    unproofread = session.scalar(
        select(func.count())
        .select_from(Chapter)
        .where(Chapter.book_id == book_id, Chapter.proofread.is_(False), with_text)
    )
    if unproofread:
        warnings.append(f"{unproofread} chapters are not proofread")
    return warnings


def render(prepared: Prepared) -> tuple[bytes, str]:
    """The finished file and its media type. Warnings the writer finds are added to
    ``prepared.warnings``."""
    pub_book = prepared.pub_book
    if prepared.fmt == "pdf-book":
        assert pub_book is not None
        data = render_pdf(pub_book, prepared.pub, prepared.cover)
        prepared.warnings += pub_book.warnings
        return data, PDF_MEDIA_TYPE
    if pub_book is None:
        data = patch_epub(
            prepared.clean, prepared.pub, identifier=prepared.identifier, cover=prepared.cover
        )
    else:
        data = write_epub_book(
            pub_book,
            prepared.pub,
            identifier=prepared.identifier,
            cover=prepared.cover,
            now=datetime.now(UTC),
        )
    prepared.warnings += epubcheck_warnings(data)
    return data, EPUB_MEDIA_TYPE


def epubcheck_warnings(data: bytes) -> list[str]:
    """EPUBCheck's errors as warnings, when it is installed; none otherwise."""
    exe = shutil.which("epubcheck")
    if exe is None:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="jerome-epubcheck-") as tmp:
            book, report = Path(tmp) / "book.epub", Path(tmp) / "report.json"
            book.write_bytes(data)
            subprocess.run(
                [exe, str(book), "--json", str(report)],
                capture_output=True,
                timeout=EPUBCHECK_TIMEOUT,
            )
            messages = json.loads(report.read_text())["messages"]
    except Exception as exc:  # noqa: BLE001 - a missing check must not lose the file
        return [f"EPUBCheck could not check the file ({type(exc).__name__})"]
    return [
        f"EPUBCheck: {m.get('ID')}: {m.get('message')}"
        for m in messages
        if m.get("severity") in ("ERROR", "FATAL")
    ]


def media_type_of(filename: str) -> str:
    return PDF_MEDIA_TYPE if filename.endswith(".pdf") else EPUB_MEDIA_TYPE


def finished_file(session: Session, storage: Storage, job_id: int) -> ExportFile:
    """The file a completed publish job made. NotFound for another kind of job; Conflict
    while the job has not finished."""
    job = session.get(Job, job_id)
    if job is None or job.type != "publish":
        raise NotFound(f"publish job {job_id}")
    name = (job.stats or {}).get("file")
    if job.status != "completed" or not name:
        raise Conflict(f"the job has not finished (it is {job.status})")
    return ExportFile(name, media_type_of(name), storage.read_export(job.book_id, name))
