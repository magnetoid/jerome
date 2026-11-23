"""app settings: admin-edited runtime settings and their audit trail

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", JSONB()),
        sa.Column("secret", sa.LargeBinary()),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "settings_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("before", JSONB()),
        sa.Column("after", JSONB()),
        sa.CheckConstraint(
            "action IN ('set', 'replaced', 'cleared', 'reset', 'key_created', 'key_revoked')",
            name="ck_settings_audit_action",
        ),
    )
    op.create_index("ix_settings_audit_at", "settings_audit", ["at"])


def downgrade() -> None:
    op.drop_index("ix_settings_audit_at", table_name="settings_audit")
    op.drop_table("settings_audit")
    op.drop_table("app_settings")
