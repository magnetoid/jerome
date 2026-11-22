"""user roles: super_admin and member

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('super_admin', 'member')")
    # Everyone who exists today already ran the team: they all start as super admins.
    op.execute("UPDATE users SET role = 'super_admin'")


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
