from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from jerome.types import Category, Role, Severity, Source, Status, Variant


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    format: str
    local_only: bool
    original_filename: str | None
    created_at: datetime
    work: Literal["proofread", "translate"]
    source_book_id: int | None
    translated_title: str | None


class PublicationOut(BaseModel):
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


class PublicationIn(BaseModel):
    """Fields to change; a field sent as null is cleared, a field left out is kept."""

    model_config = ConfigDict(extra="forbid")

    authors: list[str] | None = None
    translator: str | None = None
    publisher: str | None = None
    isbn_epub: str | None = None
    isbn_pdf: str | None = None
    year: int | None = None
    rights: str | None = None
    description: str | None = None
    trim_size: str | None = None


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
    chapter_id: int
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
    agreed: bool
    disputed: bool
    alternative: str | None
    rule: str | None
    auto_applied: bool
    decided_by: str | None
    decided_channel: str | None
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


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    active: bool
    role: Role
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreateIn(BaseModel):
    email: NonEmptyStr
    name: NonEmptyStr
    role: Role | None = None

    @field_validator("email")
    @classmethod
    def _looks_like_email(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("email must contain @")
        return value


class UserPatchIn(BaseModel):
    name: NonEmptyStr | None = None
    active: bool | None = None
    role: Role | None = None
    reset_password: bool = False


class UserWithPasswordOut(BaseModel):
    user: UserOut
    temporary_password: str | None


class AutoApplyBody(BaseModel):
    enabled: bool
    categories: list[Category]
    min_severity: Severity
    house_rules: bool


class AgentActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor: str
    channel: str
    tool: str
    book_id: int | None
    chapter_id: int | None
    job_id: int | None
    summary: str
    before: Any
    after: Any


class ActivityItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["decision", "agent"]
    id: int
    at: datetime
    actor: str
    channel: str
    book_id: int | None
    book_title: str | None
    chapter_id: int | None
    chapter_title: str | None
    summary: str
    action: str | None
    suggestion_id: int | None
    original: str | None
    replacement: str | None
    tool: str | None
    job_id: int | None
    before: Any
    after: Any
    revertable: bool
    revert_blocked: str | None


class SettingOut(BaseModel):
    key: str
    value: Any
    source: Literal["default", "environment", "app"]
    updated_by: str | None
    updated_at: datetime | None
    unreadable: bool


class SecretSettingOut(BaseModel):
    """A secret is reported as set or not; its value is never part of any response."""

    key: str
    is_set: bool
    source: Literal["default", "environment", "app"]
    updated_by: str | None
    updated_at: datetime | None
    unreadable: bool


class SystemSettingsOut(BaseModel):
    database_url: str
    data_dir: str
    web_dir: str | None
    trusted_proxies: list[str]
    cookie_secure: bool
    secret_storage: Literal["ready", "missing", "invalid"]


class SettingsOut(BaseModel):
    settings: list[SettingOut]
    secrets: list[SecretSettingOut]
    system: SystemSettingsOut


class SettingPutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any  # required; JSON null is allowed and means clear


class SettingsAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor: str
    key: str
    action: str
    before: Any
    after: Any


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreateIn(BaseModel):
    name: str


class ApiKeyCreatedOut(BaseModel):
    key: ApiKeyOut
    token: str
