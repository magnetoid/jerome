"""learned words: words LanguageTool must not mark again, in every book of a language

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learned_words",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("word", sa.String(), nullable=False),
        sa.Column("rule", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "book_id", sa.Integer(), sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("language IN ('en', 'de')", name="ck_learned_words_language"),
    )
    op.create_index("uq_learned_words", "learned_words", ["language", "word", "rule"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_learned_words", table_name="learned_words")
    op.drop_table("learned_words")
