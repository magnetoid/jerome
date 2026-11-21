"""web interface: users, sessions, auto-apply, suggestion flags

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

AUTO_APPLY_DEFAULT = (
    '{"enabled": false, "categories": ["punctuation", "typo"], '
    '"min_severity": "high", "house_rules": true}'
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.add_column(
        "books",
        sa.Column(
            "auto_apply",
            postgresql.JSONB(),
            server_default=sa.text(f"'{AUTO_APPLY_DEFAULT}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "suggestions",
        sa.Column("agreed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "suggestions",
        sa.Column("disputed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("suggestions", sa.Column("alternative", sa.Text(), nullable=True))
    op.add_column("suggestions", sa.Column("rule", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("suggestions", "rule")
    op.drop_column("suggestions", "alternative")
    op.drop_column("suggestions", "disputed")
    op.drop_column("suggestions", "agreed")
    op.drop_column("books", "auto_apply")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("users")
