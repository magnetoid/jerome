"""ai check: the AI's verdict on a LanguageTool mark, kept with the suggestion

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_VERDICT = "ai_verdict IN ('correct', 'not_an_error', 'better')"


def upgrade() -> None:
    op.add_column("suggestions", sa.Column("ai_verdict", sa.String(), nullable=True))
    op.add_column("suggestions", sa.Column("ai_replacement", sa.Text(), nullable=True))
    op.add_column("suggestions", sa.Column("ai_reason", sa.Text(), nullable=True))
    op.add_column("suggestions", sa.Column("ai_model", sa.String(), nullable=True))
    op.create_check_constraint("ck_suggestions_ai_verdict", "suggestions", _VERDICT)


def downgrade() -> None:
    op.drop_constraint("ck_suggestions_ai_verdict", "suggestions", type_="check")
    for name in ("ai_model", "ai_reason", "ai_replacement", "ai_verdict"):
        op.drop_column("suggestions", name)
