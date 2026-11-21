import io
import zipfile
from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from jerome.documents.docx_reader import read_docx
from jerome.documents.epub_reader import read_epub
from jerome.documents.model import BlockKind, ParsedBook
from jerome.documents.segments import split_sentences
from jerome.errors import NotFound, UnsupportedDocumentError
from jerome.orm import Block, Book, Chapter, Segment, StyleSheetRow
from jerome.storage import Storage
from jerome.types import DEFAULT_QUOTES, BlockText, StyleSheet, Variant

_UNSUPPORTED = "Only DOCX and EPUB are supported"


def _detect_format(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in (".docx", ".epub") or not data.startswith(b"PK\x03\x04"):
        raise UnsupportedDocumentError(_UNSUPPORTED)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "word/document.xml" in names:
                detected = "docx"
            elif "mimetype" in names and z.read("mimetype").strip() == b"application/epub+zip":
                detected = "epub"
            else:
                raise UnsupportedDocumentError(_UNSUPPORTED)
    except zipfile.BadZipFile:
        # let the format's own reader report the damaged archive
        return ext[1:]
    if ext[1:] != detected:
        raise UnsupportedDocumentError(_UNSUPPORTED)
    return detected


def import_book(
    session: Session,
    storage: Storage,
    *,
    filename: str,
    data: bytes,
    variant: Variant,
    title: str | None = None,
    local_only: bool = False,
) -> Book:
    fmt = _detect_format(filename, data)
    parsed: ParsedBook = read_docx(data) if fmt == "docx" else read_epub(data)
    book = Book(
        title=title or parsed.title or Path(filename).stem,
        format=fmt,
        local_only=local_only,
        original_filename=filename,
    )
    style = StyleSheet(variant, DEFAULT_QUOTES[variant], {}, [])
    book.style_sheet = StyleSheetRow(
        variant=variant, quote_style=style.quote_style, house_spellings={}, ignore_words=[]
    )
    session.add(book)
    session.flush()

    chapter_ids = _insert_chapters(session, book.id, parsed)
    _insert_blocks(session, book.id, parsed, chapter_ids, style)
    storage.save_original(book.id, filename, data)
    session.expire(book)
    return book


def _insert_chapters(session: Session, book_id: int, parsed: ParsedBook) -> list[int]:
    if not parsed.chapters:
        return []
    rows = [
        {
            "book_id": book_id,
            "ordinal": i,
            "title": c.title,
            "proofread": c.proofread,
            "source_ref": c.source_ref,
        }
        for i, c in enumerate(parsed.chapters)
    ]
    return list(session.scalars(insert(Chapter).returning(Chapter.id), rows))


def _insert_blocks(
    session: Session,
    book_id: int,
    parsed: ParsedBook,
    chapter_ids: list[int],
    style: StyleSheet,
) -> None:
    rows = []
    for chapter_id, chapter in zip(chapter_ids, parsed.chapters, strict=True):
        for i, b in enumerate(chapter.blocks):
            rows.append(
                {
                    "chapter_id": chapter_id,
                    "book_id": book_id,
                    "ordinal": i,
                    "kind": b.kind.value,
                    "source_ref": b.key,
                    "text": b.text,
                    "marks": [
                        {"start": m.start, "end": m.end, "kind": m.kind.value, "href": m.href}
                        for m in b.marks
                    ],
                    "hash": b.hash,
                }
            )
    if not rows:
        return
    block_ids = list(session.scalars(insert(Block).returning(Block.id), rows))
    segments = []
    for block_id, row in zip(block_ids, rows, strict=True):
        text = row["text"]
        if row["kind"] == BlockKind.HEADING.value:
            spans = [(0, len(text))] if text else []
        else:
            spans = split_sentences(text, style.language)
        segments.extend(
            {"block_id": block_id, "ordinal": n, "start": s, "end": e}
            for n, (s, e) in enumerate(spans)
        )
    if segments:
        session.execute(insert(Segment), segments)


def get_book(session: Session, book_id: int) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise NotFound(f"book {book_id}")
    return book


def list_books(session: Session) -> list[Book]:
    return list(session.scalars(select(Book).order_by(Book.id)))


def list_chapters(session: Session, book_id: int) -> list[Chapter]:
    get_book(session, book_id)
    return list(
        session.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.ordinal))
    )


def set_chapter_proofread(session: Session, chapter_id: int, proofread: bool) -> Chapter:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise NotFound(f"chapter {chapter_id}")
    chapter.proofread = proofread
    session.flush()
    return chapter


def delete_book(session: Session, storage: Storage, book_id: int) -> None:
    book = get_book(session, book_id)
    session.delete(book)
    session.flush()
    storage.delete_book(book_id)


def block_texts(session: Session, book_id: int, chapter_id: int | None = None) -> list[BlockText]:
    stmt = (
        select(Block.id, Block.text)
        .join(Chapter, Block.chapter_id == Chapter.id)
        .where(Block.book_id == book_id)
        .order_by(Chapter.ordinal, Block.ordinal)
    )
    if chapter_id is not None:
        stmt = stmt.where(Block.chapter_id == chapter_id)
    return [BlockText(id=i, text=t) for i, t in session.execute(stmt)]
