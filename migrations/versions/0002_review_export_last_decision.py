"""review export last decision

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_exports", sa.Column("last_decision_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_exports", "last_decision_id")
