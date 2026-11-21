from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from jerome.types import Category, Severity, Source, Status, Variant


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    format: str
    local_only: bool
    original_filename: str | None
    created_at: datetime


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ordinal: int
    title: str
    proofread: bool
    source_ref: str | None


class ChapterPatch(BaseModel):
    proofread: bool


class StyleSheetOut(BaseModel):
    variant: Variant
    quote_style: str
    house_spellings: dict[str, list[str]]
    ignore_words: list[str]


class StyleSheetUpdate(BaseModel):
    """Partial update: fields left out (or null) are unchanged."""

    variant: Variant | None = None
    quote_style: str | None = None
    house_spellings: dict[str, list[str]] | None = None
    ignore_words: list[str] | None = None


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    block_id: int
    start: int
    end: int
    original: str
    replacement: str
    category: str
    severity: str
    source: str
    explanation: str
    status: str
    final_text: str | None
    job_id: int | None
    model: str | None
    created_at: datetime


class BlockOut(BaseModel):
    original: str
    current: str
    suggestions: list[SuggestionOut]


class EditBody(BaseModel):
    replacement: str


class SuggestionFilterIn(BaseModel):
    chapter_id: int | None = None
    category: Category | None = None
    severity: Severity | None = None
    min_severity: Severity | None = None
    source: Source | None = None
    status: Status | None = None


class BulkBody(BaseModel):
    book_id: int
    ids: list[int] | None = None
    filter: SuggestionFilterIn | None = None
    action: Literal["accept", "reject"]


class BulkOut(BaseModel):
    decided: int
