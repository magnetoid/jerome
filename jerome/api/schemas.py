from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jerome.types import Variant


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
