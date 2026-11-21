"""initial

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("local_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("format IN ('docx', 'epub')", name="ck_books_format"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("proofread", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapters_book_id"), "chapters", ["book_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("passes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chapter_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("max_cost", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column(
            "cost_actual",
            sa.Numeric(precision=12, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("callback_url", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused_cost_cap', 'cancelled', "
            "'failed', 'completed')",
            name="ck_jobs_status",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_jobs_one_active_per_book",
        "jobs",
        ["book_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'paused_cost_cap')"),
    )
    op.create_table(
        "review_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_statuses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("block_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_exports_book_id"), "review_exports", ["book_id"], unique=False)
    op.create_table(
        "style_sheets",
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("quote_style", sa.String(), nullable=False),
        sa.Column(
            "house_spellings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ignore_words",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
    )
    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "marks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("hash", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blocks_book_id"), "blocks", ["book_id"], unique=False)
    op.create_index(op.f("ix_blocks_chapter_id"), "blocks", ["chapter_id"], unique=False)
    op.create_table(
        "job_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("chunks_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('chapter', 'consistency')", name="ck_job_tasks_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_job_tasks_status"
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_tasks_job_id"), "job_tasks", ["job_id"], unique=False)
    op.create_index(
        "ix_job_tasks_status_not_before", "job_tasks", ["status", "not_before"], unique=False
    )
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("api_key_name", sa.String(), nullable=True),
        sa.Column("pass_name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cache_read_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "cache_write_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("cost", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_usage_api_key_name_created_at",
        "model_usage",
        ["api_key_name", "created_at"],
        unique=False,
    )
    op.create_table(
        "review_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_export_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_export_id"], ["review_exports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_imports_book_id"), "review_imports", ["book_id"], unique=False)
    op.create_index(
        op.f("ix_review_imports_review_export_id"),
        "review_imports",
        ["review_export_id"],
        unique=False,
    )
    op.create_table(
        "segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_segments_block_id"), "segments", ["block_id"], unique=False)
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("original", sa.Text(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), server_default="open", nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'accepted', 'rejected', 'edited', 'superseded')",
            name="ck_suggestions_status",
        ),
        sa.CheckConstraint(
            "category IN ('typo', 'grammar', 'punctuation', 'consistency', 'style')",
            name="ck_suggestions_category",
        ),
        sa.CheckConstraint("severity IN ('high', 'medium', 'low')", name="ck_suggestions_severity"),
        sa.CheckConstraint(
            "source IN ('languagetool', 'model', 'consistency', 'editor')",
            name="ck_suggestions_source",
        ),
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by"], ["suggestions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_suggestions_block_id_status", "suggestions", ["block_id", "status"], unique=False
    )
    op.create_index(op.f("ix_suggestions_book_id"), "suggestions", ["book_id"], unique=False)
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suggestion_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("previous_status", sa.String(), nullable=False),
        sa.Column("new_status", sa.String(), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("review_import_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("channel IN ('api', 'word', 'system')", name="ck_decisions_channel"),
        sa.CheckConstraint(
            "previous_status IN ('open', 'accepted', 'rejected', 'edited', 'superseded')",
            name="ck_decisions_previous_status",
        ),
        sa.CheckConstraint(
            "new_status IN ('open', 'accepted', 'rejected', 'edited', 'superseded')",
            name="ck_decisions_new_status",
        ),
        sa.ForeignKeyConstraint(["review_import_id"], ["review_imports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["suggestion_id"], ["suggestions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_decisions_suggestion_id"), "decisions", ["suggestion_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_decisions_suggestion_id"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_suggestions_book_id"), table_name="suggestions")
    op.drop_index("ix_suggestions_block_id_status", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_index(op.f("ix_segments_block_id"), table_name="segments")
    op.drop_table("segments")
    op.drop_index(op.f("ix_review_imports_review_export_id"), table_name="review_imports")
    op.drop_index(op.f("ix_review_imports_book_id"), table_name="review_imports")
    op.drop_table("review_imports")
    op.drop_index("ix_model_usage_api_key_name_created_at", table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index("ix_job_tasks_status_not_before", table_name="job_tasks")
    op.drop_index(op.f("ix_job_tasks_job_id"), table_name="job_tasks")
    op.drop_table("job_tasks")
    op.drop_index(op.f("ix_blocks_chapter_id"), table_name="blocks")
    op.drop_index(op.f("ix_blocks_book_id"), table_name="blocks")
    op.drop_table("blocks")
    op.drop_table("style_sheets")
    op.drop_index(op.f("ix_review_exports_book_id"), table_name="review_exports")
    op.drop_table("review_exports")
    op.drop_index(
        "uq_jobs_one_active_per_book",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running', 'paused_cost_cap')"),
    )
    op.drop_table("jobs")
    op.drop_index(op.f("ix_chapters_book_id"), table_name="chapters")
    op.drop_table("chapters")
    op.drop_table("books")
    op.drop_table("api_keys")
