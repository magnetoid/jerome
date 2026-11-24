"""A book's publication details: what the finished EPUB and PDF say about the book.

The details live on the book row. A cover image is kept in storage; ``cover_type`` on the
row says it is there and what it is."""

import io
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from jerome.core.books import get_book
from jerome.core.translation import ensure_not_source
from jerome.errors import JeromeError
from jerome.orm import TRIM_SIZES, Book
from jerome.storage import Storage

MAX_COVER_PIXELS = 50_000_000  # a larger image is refused before it is decoded
MIN_COVER_SIDE = 1400  # px on the long side; the big shops ask for at least this
MAX_TEXT = 500  # characters in a one-line field
MAX_DESCRIPTION = 10_000
_TEXT_FIELDS = ("translator", "publisher", "rights")
_EXT = {"image/jpeg": "jpg", "image/png": "png"}
_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}


class InvalidPublication(JeromeError):
    """A publication detail or cover that cannot be used (HTTP 422)."""


@dataclass
class Publication:
    title: str
    language: str
    authors: list[str]
    translator: str | None
    publisher: str | None
    isbn_epub: str | None
    isbn_pdf: str | None
    year: int | None
    rights: str
    description: str | None
    trim_size: str
    has_cover: bool


def isbn13(value: str) -> str:
    """The ISBN as given (hyphens and spaces kept, outer spaces dropped), if it is a valid
    ISBN-13."""
    text = value.strip()
    digits = re.sub(r"[\s-]", "", text)
    valid = len(digits) == 13 and digits.isdigit()
    if valid:
        total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
        valid = (10 - total % 10) % 10 == int(digits[12])
    if not valid:
        raise InvalidPublication(f"ISBN {value!r} is not a valid ISBN-13")
    return text


def image_size(data: bytes) -> tuple[str, int, int] | None:
    """(media type, width, height) of a PNG or JPEG that decodes whole; None for anything
    else: another format, a file cut short or corrupt, or more than MAX_COVER_PIXELS."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            kind, (width, height) = probe.format, probe.size
            if kind not in _FORMATS or not width or not height:
                return None
            if width * height > MAX_COVER_PIXELS:  # refused before it is decoded
                return None
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()  # verify() does not decode the pixels; load() does
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    return _FORMATS[kind], width, height


def default_rights(year: int | None, authors: list[str]) -> str:
    return f"© {year} {', '.join(authors)}".rstrip() if year else ""


def publication_of(session: Session, book: Book) -> Publication:
    style = book.style_sheet
    return Publication(
        title=book.translated_title or book.title,
        language=style.variant if style is not None else "en-US",
        authors=list(book.authors or []),
        translator=book.translator,
        publisher=book.publisher,
        isbn_epub=book.isbn_epub,
        isbn_pdf=book.isbn_pdf,
        year=book.year,
        rights=book.rights or default_rights(book.year, list(book.authors or [])),
        description=book.description,
        trim_size=book.trim_size,
        has_cover=book.cover_type is not None,
    )


def get_publication(session: Session, book_id: int) -> Publication:
    return publication_of(session, _book(session, book_id))


def _book(session: Session, book_id: int) -> Book:
    """The book, refusing a hidden source book as every other book route does."""
    book = get_book(session, book_id)
    ensure_not_source(session, book_id)
    return book


def _text(name: str, value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()) if name != "description" else str(value).strip()
    if len(text) > limit:
        raise InvalidPublication(f"The {name} is longer than {limit} characters")
    return text or None


def update_publication(session: Session, book_id: int, values: dict[str, Any]) -> Publication:
    """Set the given fields; a field set to None is cleared. Unknown fields are refused."""
    book = _book(session, book_id)
    for name, value in values.items():
        if name == "authors":
            names = [_text("author", a) for a in (value or [])]
            book.authors = [n for n in names if n]
        elif name in ("isbn_epub", "isbn_pdf"):
            text = _text(name, value)
            setattr(book, name, isbn13(text) if text else None)
        elif name == "year":
            if value is not None and not (isinstance(value, int) and 1000 <= value <= 2999):
                raise InvalidPublication(f"The year {value!r} is not a four-digit year")
            book.year = value
        elif name == "trim_size":
            if value not in TRIM_SIZES:
                raise InvalidPublication(
                    f"The trim size {value!r} is not one of {', '.join(TRIM_SIZES)}"
                )
            book.trim_size = value
        elif name == "description":
            book.description = _text(name, value, MAX_DESCRIPTION)
        elif name in _TEXT_FIELDS:
            setattr(book, name, _text(name, value))
        else:
            raise InvalidPublication(f"Unknown publication field {name!r}")
    session.flush()
    return publication_of(session, book)


def set_cover(session: Session, storage: Storage, book_id: int, data: bytes) -> Publication:
    """Store a JPEG or PNG cover at least MIN_COVER_SIDE px on its long side. A refused
    image leaves the stored cover as it was."""
    book = _book(session, book_id)
    size = image_size(data)
    if size is None:
        raise InvalidPublication("The cover must be a JPEG or PNG image")
    media_type, width, height = size
    if max(width, height) < MIN_COVER_SIDE:
        raise InvalidPublication(
            f"The cover is {max(width, height)} px on its long side; "
            f"shops need at least {MIN_COVER_SIDE}"
        )
    storage.save_cover(book.id, data, _EXT[media_type])
    book.cover_type = media_type
    session.flush()
    return publication_of(session, book)


def delete_cover(session: Session, storage: Storage, book_id: int) -> Publication:
    book = _book(session, book_id)
    storage.delete_cover(book.id)
    book.cover_type = None
    session.flush()
    return publication_of(session, book)


def read_cover(storage: Storage, book: Book) -> tuple[bytes, str] | None:
    """The cover as (data, media type), or None."""
    data = storage.read_cover(book.id) if book.cover_type else None
    return (data, book.cover_type) if data and book.cover_type else None
