"""decision channels: web (people in the browser) and auto (AI auto-apply)

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_decisions_channel", "decisions", type_="check")
    op.create_check_constraint(
        "ck_decisions_channel",
        "decisions",
        "channel IN ('api', 'word', 'system', 'web', 'auto')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_decisions_channel", "decisions", type_="check")
    op.create_check_constraint(
        "ck_decisions_channel", "decisions", "channel IN ('api', 'word', 'system')"
    )
