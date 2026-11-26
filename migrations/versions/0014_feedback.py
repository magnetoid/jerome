"""feedback: bug reports, feature requests and ideas sent from the web app

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("page", sa.String(), nullable=True),
        sa.Column("viewport", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("screenshot", sa.LargeBinary(), nullable=True),
        sa.Column("screenshot_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="new", nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('bug', 'feature', 'idea')", name="ck_feedback_kind"),
        sa.CheckConstraint("status IN ('new', 'done')", name="ck_feedback_status"),
    )


def downgrade() -> None:
    op.drop_table("feedback")
