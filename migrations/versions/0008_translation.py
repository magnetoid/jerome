"""translation: translated books link to their source; blocks carry translation state

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_OLD_CATEGORY = "category IN ('typo', 'grammar', 'punctuation', 'consistency', 'style')"
_NEW_CATEGORY = (
    "category IN ('typo', 'grammar', 'punctuation', 'consistency', 'style', 'translation')"
)


def upgrade() -> None:
    op.add_column(
        "books", sa.Column("work", sa.String(), server_default="proofread", nullable=False)
    )
    op.add_column("books", sa.Column("source_book_id", sa.Integer(), nullable=True))
    op.add_column(
        "books",
        sa.Column("is_source", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("books", sa.Column("translated_title", sa.String(), nullable=True))
    op.create_foreign_key(
        "books_source_book_id_fkey",
        "books",
        "books",
        ["source_book_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint("ck_books_work", "books", "work IN ('proofread', 'translate')")

    op.add_column("blocks", sa.Column("source_block_id", sa.Integer(), nullable=True))
    op.add_column("blocks", sa.Column("translation_status", sa.String(), nullable=True))
    op.add_column("blocks", sa.Column("placeholders", JSONB(), nullable=True))
    op.create_foreign_key(
        "blocks_source_block_id_fkey",
        "blocks",
        "blocks",
        ["source_block_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_blocks_source_block_id", "blocks", ["source_block_id"])
    op.create_check_constraint(
        "ck_blocks_translation_status",
        "blocks",
        "translation_status IN ('pending', 'done', 'flagged', 'kept')",
    )

    op.drop_constraint("ck_suggestions_category", "suggestions", type_="check")
    op.create_check_constraint("ck_suggestions_category", "suggestions", _NEW_CATEGORY)


def downgrade() -> None:
    op.execute("DELETE FROM suggestions WHERE category = 'translation'")
    op.drop_constraint("ck_suggestions_category", "suggestions", type_="check")
    op.create_check_constraint("ck_suggestions_category", "suggestions", _OLD_CATEGORY)

    op.drop_constraint("ck_blocks_translation_status", "blocks", type_="check")
    op.drop_index("ix_blocks_source_block_id", table_name="blocks")
    op.drop_constraint("blocks_source_block_id_fkey", "blocks", type_="foreignkey")
    op.drop_column("blocks", "placeholders")
    op.drop_column("blocks", "translation_status")
    op.drop_column("blocks", "source_block_id")

    op.drop_constraint("ck_books_work", "books", type_="check")
    op.drop_constraint("books_source_book_id_fkey", "books", type_="foreignkey")
    op.drop_column("books", "translated_title")
    op.drop_column("books", "is_source")
    op.drop_column("books", "source_book_id")
    op.drop_column("books", "work")
