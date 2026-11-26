import io
import zipfile
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import insert, or_, select
from sqlalchemy.orm import Session

from jerome.core.auto_apply import DEFAULT_POLICY, AutoApplyPolicy, policy_to_json
from jerome.core.translation import ensure_not_source, source_of
from jerome.documents.docx_reader import read_docx
from jerome.documents.epub_reader import read_epub
from jerome.documents.model import ParsedBook
from jerome.documents.segments import block_spans
from jerome.errors import Conflict, NotFound, UnsupportedDocumentError, UnsupportedLanguagePair
from jerome.orm import Block, Book, Chapter, Job, JobTask, Segment, StyleSheetRow
from jerome.storage import Storage
from jerome.types import DEFAULT_QUOTES, BlockText, StyleSheet, Variant

_UNSUPPORTED = "Only DOCX and EPUB are supported"
Work = Literal["proofread", "translate"]


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


_QUOTE_PAIRS = {"“": "“”", "„": "„“", "»": "»«", "«": "«»"}  # opener -> double pair
_SINGLES = {"»«": "›‹", "«»": "‹›", "„“": "‚‘"}
_BEFORE_OPENER = frozenset("([{—–‘‚›‹")
_MIN_QUOTES, _MIN_SHARE = 10, 0.8


def detect_quote_style(texts: Iterable[str], variant: Variant) -> str:
    """The quote style of the book: its dominant double-quote pair, else the variant default.

    Opening characters of the known pairs are counted where they open (at the start, after
    a space or an opening bracket or dash); the pair needs at least 10 openers and 80% of
    them. Straight quotes are not a pair and never set the style.
    """
    default = DEFAULT_QUOTES[variant]
    openers: Counter[str] = Counter()
    for text in texts:
        for i, ch in enumerate(text):
            if ch in _QUOTE_PAIRS and (
                i == 0 or text[i - 1].isspace() or text[i - 1] in _BEFORE_OPENER
            ):
                openers[ch] += 1
    total = sum(openers.values())
    if total < _MIN_QUOTES:
        return default
    opener, n = openers.most_common(1)[0]
    if n < _MIN_SHARE * total:
        return default
    double = _QUOTE_PAIRS[opener]
    if double in (default[0:2], default[2:4]):
        return default
    if double in _SINGLES:
        return double + _SINGLES[double]
    pairs = (default[0:2], default[2:4])  # “” in a variant that does not use it
    return double + next(p for p in pairs if not set(p) & set("“”„«»"))


def import_book(
    session: Session,
    storage: Storage,
    *,
    filename: str,
    data: bytes,
    variant: Variant,
    title: str | None = None,
    local_only: bool = False,
    auto_apply: AutoApplyPolicy = DEFAULT_POLICY,
) -> Book:
    fmt = _detect_format(filename, data)
    parsed: ParsedBook = read_docx(data) if fmt == "docx" else read_epub(data)
    book = Book(
        title=title or parsed.title or Path(filename).stem,
        format=fmt,
        local_only=local_only,
        original_filename=filename,
        auto_apply=policy_to_json(auto_apply),
    )
    _fill(session, storage, book, parsed, variant, data)
    return book


def populate_from_docx(
    session: Session,
    storage: Storage,
    book: Book,
    data: bytes,
    variant: Variant,
    *,
    keep_existing: bool = False,
) -> None:
    """Fill a Book that exists already (its title, ``original_filename`` and options set)
    from DOCX bytes: publication details, style sheet, chapters, blocks, and the original.

    With ``keep_existing`` (a PDF import, whose book the owner could edit while it was
    read), publication details already set stay and only empty ones are filled, and a quote
    style the owner chose (not the variant's default) stays."""
    _fill(session, storage, book, read_docx(data), variant, data, keep_existing=keep_existing)


def _fill(
    session: Session,
    storage: Storage,
    book: Book,
    parsed: ParsedBook,
    variant: Variant,
    data: bytes,
    *,
    keep_existing: bool = False,
) -> None:
    found = {
        "authors": prefill_authors(parsed.authors),
        "publisher": parsed.publisher,
        "rights": (parsed.rights or "")[:500] or None,
        "year": parsed.year if parsed.year and 1000 <= parsed.year <= 2999 else None,
    }
    for name, value in found.items():
        if not (keep_existing and getattr(book, name)):
            setattr(book, name, value)
    quotes = detect_quote_style((b.text for c in parsed.chapters for b in c.blocks), variant)
    sheet = book.style_sheet
    if keep_existing and sheet is not None and sheet.quote_style != DEFAULT_QUOTES[sheet.variant]:
        quotes = sheet.quote_style  # the owner's choice
    style = StyleSheet(variant, quotes, {}, [])
    if sheet is None:
        book.style_sheet = StyleSheetRow(
            variant=variant, quote_style=quotes, house_spellings={}, ignore_words=[]
        )
    else:
        sheet.variant = variant
        sheet.quote_style = quotes
    session.add(book)
    session.flush()

    chapter_ids = _insert_chapters(session, book.id, parsed)
    _insert_blocks(session, book.id, parsed, chapter_ids, style)
    storage.save_original(book.id, book.original_filename or "original.docx", data)
    session.expire(book)


def ensure_imported(book: Book) -> None:
    """Refuse a book whose PDF import has not finished: nothing can run on it yet."""
    if book.import_status == "importing":
        raise Conflict("the book is still importing")
    if book.import_status == "failed":
        raise Conflict(f"the book's import failed: {book.import_error or 'unknown error'}")


def start_pdf_import(
    session: Session,
    storage: Storage,
    *,
    filename: str,
    data: bytes,
    variant: Variant,
    title: str | None,
    local_only: bool,
    auto_apply: AutoApplyPolicy,
    engine: Literal["ai", "tesseract"],
    reocr: bool,
    work: Work,
    target_variant: Variant | None,
    translated_title: str | None,
    actor: str,
) -> Book:
    """Make the visible, still empty Book of a PDF and the import job that fills it.

    The job (Task 7's runner) reads the pages and builds the DOCX; until it is done the
    book is ``importing``. For a translation the book is the translated one: its style
    sheet is the target variant, ``variant`` (the PDF's language) is in the job's stats."""
    if engine == "ai" and local_only:
        raise ValueError("a local-only book cannot be read with AI; use the tesseract engine")
    if work == "translate":
        if target_variant is None:
            raise ValueError("a translation needs a target variant")
        if variant[:2] == target_variant[:2]:
            raise UnsupportedLanguagePair(
                f"cannot translate {variant} to {target_variant}: "
                "source and target must be different languages"
            )
    from jerome.pdfimport.reader import count_pages_isolated  # loaded only when needed

    pages = count_pages_isolated(data)  # UnsupportedDocumentError: unreadable, too long
    stem = Path(filename).stem
    shown = variant if work != "translate" or target_variant is None else target_variant
    book = Book(
        title=title or stem,
        format="docx",
        local_only=local_only,
        original_filename=f"{stem}.docx",
        auto_apply=policy_to_json(auto_apply),
        work=work,
        translated_title=translated_title,
        import_status="importing",
    )
    book.style_sheet = StyleSheetRow(
        variant=shown, quote_style=DEFAULT_QUOTES[shown], house_spellings={}, ignore_words=[]
    )
    session.add(book)
    session.flush()
    storage.save_source_pdf(book.id, data)
    job = Job(
        book_id=book.id,
        type="import",
        status="queued",
        passes=[engine],
        chapter_ids=None,
        max_cost=Decimal(0),
        stats={
            "pages": pages,
            "pages_done": 0,
            "reocr": reocr,
            "variant": variant,
            "target_variant": target_variant,
        },
        created_by=actor,
    )
    job.tasks.append(JobTask(kind="import_scan"))
    session.add(job)
    session.flush()
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
    return list(
        session.scalars(insert(Chapter).returning(Chapter.id, sort_by_parameter_order=True), rows)
    )


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
    block_ids = list(
        session.scalars(insert(Block).returning(Block.id, sort_by_parameter_order=True), rows)
    )
    segments = []
    for block_id, row in zip(block_ids, rows, strict=True):
        spans = block_spans(row["kind"], row["text"], style.language)
        segments.extend(
            {"block_id": block_id, "ordinal": n, "start": s, "end": e}
            for n, (s, e) in enumerate(spans)
        )
    if segments:
        session.execute(insert(Segment), segments)


# Names Word writes as the document's creator when nobody set one
_PLACEHOLDER_CREATORS = frozenset({"microsoft office user", "user", "author", "unknown"})


def prefill_authors(names: list[str]) -> list[str]:
    """Authors read from a file's metadata, without Word's placeholder creators."""
    return [n for n in names if n.strip() and n.strip().lower() not in _PLACEHOLDER_CREATORS]


def get_book(session: Session, book_id: int) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise NotFound(f"book {book_id}")
    return book


def list_books(session: Session) -> list[Book]:
    return list(session.scalars(select(Book).where(Book.is_source.is_(False)).order_by(Book.id)))


def list_chapters(session: Session, book_id: int) -> list[Chapter]:
    get_book(session, book_id)
    return list(
        session.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.ordinal))
    )


def set_chapter_proofread(session: Session, chapter_id: int, proofread: bool) -> Chapter:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise NotFound(f"chapter {chapter_id}")
    ensure_not_source(session, chapter.book_id)
    chapter.proofread = proofread
    session.flush()
    return chapter


def delete_book(session: Session, storage: Storage, book_id: int) -> None:
    book = get_book(session, book_id)
    ensure_not_source(session, book_id)
    source = source_of(session, book)
    session.delete(book)
    if source is not None:
        session.delete(source)
    session.flush()
    storage.delete_book(book_id)
    if source is not None:
        storage.delete_book(source.id)


def block_texts(
    session: Session,
    book_id: int,
    chapter_id: int | None = None,
    *,
    proofreadable_only: bool = False,
) -> list[BlockText]:
    stmt = (
        select(Block.id, Block.text)
        .join(Chapter, Block.chapter_id == Chapter.id)
        .where(Block.book_id == book_id)
        .order_by(Chapter.ordinal, Block.ordinal)
    )
    if chapter_id is not None:
        stmt = stmt.where(Block.chapter_id == chapter_id)
    if proofreadable_only:  # untranslated, flagged and kept blocks hold no text to proofread
        stmt = stmt.where(
            or_(
                Block.translation_status.is_(None),
                Block.translation_status.not_in(("pending", "flagged", "kept")),
            )
        )
    return [BlockText(id=i, text=t) for i, t in session.execute(stmt)]
