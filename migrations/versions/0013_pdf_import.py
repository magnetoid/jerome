"""pdf import: a book can be importing or failed; import tasks carry a page range

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_OLD_KIND = "kind IN ('chapter', 'consistency', 'publish')"
_NEW_KIND = (
    "kind IN ('chapter', 'consistency', 'publish', 'import_scan', 'import_pages', 'import_build')"
)
_STATUS = "import_status IN ('importing', 'failed')"


def upgrade() -> None:
    op.add_column("books", sa.Column("import_status", sa.String(), nullable=True))
    op.add_column("books", sa.Column("import_error", sa.Text(), nullable=True))
    op.add_column("books", sa.Column("import_report", JSONB(), nullable=True))
    op.create_check_constraint("ck_books_import_status", "books", _STATUS)
    op.add_column("job_tasks", sa.Column("pages", JSONB(), nullable=True))
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("ck_job_tasks_kind", "job_tasks", _NEW_KIND)


def downgrade() -> None:
    """Drop the import columns and task kinds.

    It fails while any import task exists (the old kind check does not allow them): delete
    import jobs first."""
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("ck_job_tasks_kind", "job_tasks", _OLD_KIND)
    op.drop_column("job_tasks", "pages")
    op.drop_constraint("ck_books_import_status", "books", type_="check")
    for name in ("import_report", "import_error", "import_status"):
        op.drop_column("books", name)
