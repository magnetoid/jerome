from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from jerome.types import Category, Role, Severity, Source, Status

JOB_STATUSES = ("queued", "running", "paused_cost_cap", "cancelled", "failed", "completed")
ACTIVE_JOB_STATUSES = ("queued", "running", "paused_cost_cap")
TASK_STATUSES = ("queued", "running", "done", "failed")
CHANNELS = ("api", "word", "system", "web", "auto", "mcp")


def _values(vocab: Iterable[str]) -> tuple[str, ...]:
    return tuple(str(v.value) if isinstance(v, Enum) else v for v in vocab)


def _in(column: str, vocab: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in _values(vocab))})"


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


AUTO_APPLY_DEFAULT = (
    '{"enabled": false, "categories": ["punctuation", "typo"], '
    '"min_severity": "high", "house_rules": true}'
)


def _empty_obj() -> Any:
    return mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (CheckConstraint(_in("format", ("docx", "epub")), name="ck_books_format"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    format: Mapped[str] = mapped_column(String)
    local_only: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    original_filename: Mapped[str | None] = mapped_column(String)
    auto_apply: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text(f"'{AUTO_APPLY_DEFAULT}'::jsonb")
    )
    created_at: Mapped[datetime] = _ts()

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Chapter.ordinal",
    )
    style_sheet: Mapped["StyleSheetRow | None"] = relationship(
        back_populates="book", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class StyleSheetRow(Base):
    __tablename__ = "style_sheets"

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    variant: Mapped[str] = mapped_column(String)
    quote_style: Mapped[str] = mapped_column(String)
    house_spellings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb")
    )
    ignore_words: Mapped[list[Any]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))

    book: Mapped[Book] = relationship(back_populates="style_sheet")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    proofread: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    source_ref: Mapped[str | None] = mapped_column(String)

    book: Mapped[Book] = relationship(back_populates="chapters")
    blocks: Mapped[list["Block"]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Block.ordinal",
    )


class Block(Base):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    source_ref: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    marks: Mapped[list[Any]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))
    hash: Mapped[str] = mapped_column(String)

    chapter: Mapped[Chapter] = relationship(back_populates="blocks")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="block",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Segment.ordinal",
    )


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)

    block: Mapped[Block] = relationship(back_populates="segments")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_jobs_status"),
        Index(
            "uq_jobs_one_active_per_book",
            "book_id",
            unique=True,
            postgresql_where=sql_text(_in("status", ACTIVE_JOB_STATUSES)),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, server_default="queued")
    passes: Mapped[list[Any]] = mapped_column(JSONB)
    chapter_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    max_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cost_actual: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default=sql_text("0"))
    estimate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any]] = _empty_obj()
    callback_url: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list["JobTask"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JobTask.id",
    )


class JobTask(Base):
    __tablename__ = "job_tasks"
    __table_args__ = (
        CheckConstraint(_in("kind", ("chapter", "consistency")), name="ck_job_tasks_kind"),
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_job_tasks_status"),
        Index("ix_job_tasks_status_not_before", "status", "not_before"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, server_default="queued")
    chunks_done: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="tasks")


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(_in("status", Status), name="ck_suggestions_status"),
        CheckConstraint(_in("category", Category), name="ck_suggestions_category"),
        CheckConstraint(_in("severity", Severity), name="ck_suggestions_severity"),
        CheckConstraint(_in("source", Source), name="ck_suggestions_source"),
        Index("ix_suggestions_block_id_status", "block_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    original: Mapped[str] = mapped_column(Text)
    replacement: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, server_default="open")
    final_text: Mapped[str | None] = mapped_column(Text)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("suggestions.id", ondelete="SET NULL")
    )
    model: Mapped[str | None] = mapped_column(String)
    agreed: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    disputed: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    alternative: Mapped[str | None] = mapped_column(Text)
    rule: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(_in("channel", CHANNELS), name="ck_decisions_channel"),
        CheckConstraint(_in("previous_status", Status), name="ck_decisions_previous_status"),
        CheckConstraint(_in("new_status", Status), name="ck_decisions_new_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        ForeignKey("suggestions.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String)
    previous_status: Mapped[str] = mapped_column(String)
    new_status: Mapped[str] = mapped_column(String)
    final_text: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    review_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_imports.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _ts()


class ReviewExport(Base):
    __tablename__ = "review_exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    suggestion_statuses: Mapped[dict[str, Any]] = mapped_column(JSONB)
    block_ids: Mapped[list[Any]] = mapped_column(JSONB)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_decision_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()


class ReviewImport(Base):
    __tablename__ = "review_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_export_id: Mapped[int] = mapped_column(
        ForeignKey("review_exports.id", ondelete="CASCADE"), index=True
    )
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    key_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", get_args(Role)), name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    role: Mapped[str] = mapped_column(String, server_default=sql_text("'member'"))
    created_at: Mapped[datetime] = _ts()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = _ts()


class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (
        Index("ix_model_usage_api_key_name_created_at", "api_key_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    api_key_name: Mapped[str | None] = mapped_column(String)
    pass_name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default=sql_text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default=sql_text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, server_default=sql_text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, server_default=sql_text("0"))
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = _ts()


class AgentAction(Base):
    """What the owner's AI agent (Janus) did through MCP: one row per tool call."""

    __tablename__ = "agent_actions"
    __table_args__ = (CheckConstraint("channel IN ('mcp')", name="ck_agent_actions_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    tool: Mapped[str] = mapped_column(String)
    book_id: Mapped[int | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL"), index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TransferLink(Base):
    """A single-use link for moving a file in or out without it passing through the agent."""

    __tablename__ = "transfer_links"
    __table_args__ = (
        CheckConstraint("kind IN ('upload', 'download')", name="ck_transfer_links_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    filename: Mapped[str | None] = mapped_column(String)
    media_type: Mapped[str | None] = mapped_column(String)
    size: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str | None] = mapped_column(String)  # relative to data_dir
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
