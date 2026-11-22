"""mcp: decision channel, Janus activity log and single-use transfer links

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_decisions_channel", "decisions", type_="check")
    op.create_check_constraint(
        "ck_decisions_channel",
        "decisions",
        "channel IN ('api', 'word', 'system', 'web', 'auto', 'mcp')",
    )
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("book_id", sa.Integer(), sa.ForeignKey("books.id", ondelete="SET NULL")),
        sa.Column("chapter_id", sa.Integer()),
        sa.Column("job_id", sa.Integer()),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("before", JSONB()),
        sa.Column("after", JSONB()),
        sa.CheckConstraint("channel IN ('mcp')", name="ck_agent_actions_channel"),
    )
    op.create_index("ix_agent_actions_at", "agent_actions", ["at"])
    op.create_index("ix_agent_actions_book_id", "agent_actions", ["book_id"])
    op.create_table(
        "transfer_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("filename", sa.String()),
        sa.Column("media_type", sa.String()),
        sa.Column("size", sa.Integer()),
        sa.Column("path", sa.String()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('upload', 'download')", name="ck_transfer_links_kind"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if (
        conn.execute(sa.text("SELECT 1 FROM decisions WHERE channel = 'mcp' LIMIT 1")).first()
        or conn.execute(sa.text("SELECT 1 FROM agent_actions LIMIT 1")).first()
    ):
        raise RuntimeError("cannot downgrade 0006: Janus (mcp) data exists")
    op.drop_table("transfer_links")
    op.drop_index("ix_agent_actions_book_id", table_name="agent_actions")
    op.drop_index("ix_agent_actions_at", table_name="agent_actions")
    op.drop_table("agent_actions")
    op.drop_constraint("ck_decisions_channel", "decisions", type_="check")
    op.create_check_constraint(
        "ck_decisions_channel",
        "decisions",
        "channel IN ('api', 'word', 'system', 'web', 'auto')",
    )
