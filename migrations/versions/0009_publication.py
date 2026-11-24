"""publication: per-book details for the finished files; publish job tasks

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_OLD_KIND = "kind IN ('chapter', 'consistency')"
_NEW_KIND = "kind IN ('chapter', 'consistency', 'publish')"
_TRIM = "trim_size IN ('6x9in', '5.5x8.5in', '5x8in', 'a5')"
_TEXT_COLUMNS = ("translator", "publisher", "isbn_epub", "isbn_pdf", "rights", "cover_type")


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("authors", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    for name in _TEXT_COLUMNS:
        op.add_column("books", sa.Column(name, sa.String(), nullable=True))
    op.add_column("books", sa.Column("year", sa.Integer(), nullable=True))
    op.add_column("books", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "books", sa.Column("trim_size", sa.String(), server_default="6x9in", nullable=False)
    )
    op.create_check_constraint("ck_books_trim_size", "books", _TRIM)
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("ck_job_tasks_kind", "job_tasks", _NEW_KIND)


def downgrade() -> None:
    """Drop the publication columns and the publish task kind.

    It fails while any publish task exists (the old kind check does not allow them): delete
    publish jobs first. Covers stay in storage until their book is deleted."""
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("ck_job_tasks_kind", "job_tasks", _OLD_KIND)
    op.drop_constraint("ck_books_trim_size", "books", type_="check")
    for name in ("authors", *_TEXT_COLUMNS, "year", "description", "trim_size"):
        op.drop_column("books", name)
