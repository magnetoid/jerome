"""lt options: LanguageTool's replacements and rule kept with a mark; three AI ratings

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_OLD = "ai_verdict IN ('correct', 'not_an_error', 'better')"
_NEW = "ai_verdict IN ('error', 'maybe_error', 'not_an_error')"


def upgrade() -> None:
    op.add_column("suggestions", sa.Column("options", postgresql.JSONB(), nullable=True))
    op.add_column("suggestions", sa.Column("lt_rule", sa.String(), nullable=True))
    op.drop_constraint("ck_suggestions_ai_verdict", "suggestions", type_="check")
    # "LanguageTool is right" and "a better fix" are both definite errors now, with the fix
    op.execute(
        "UPDATE suggestions SET ai_verdict = 'error', ai_replacement = replacement "
        "WHERE ai_verdict = 'correct'"
    )
    op.execute("UPDATE suggestions SET ai_verdict = 'error' WHERE ai_verdict = 'better'")
    op.create_check_constraint("ck_suggestions_ai_verdict", "suggestions", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_suggestions_ai_verdict", "suggestions", type_="check")
    op.execute(
        "UPDATE suggestions SET ai_verdict = 'correct', ai_replacement = NULL "
        "WHERE ai_verdict IN ('error', 'maybe_error') AND ai_replacement = replacement"
    )
    op.execute(
        "UPDATE suggestions SET ai_verdict = 'better' WHERE ai_verdict IN ('error', 'maybe_error')"
    )
    op.create_check_constraint("ck_suggestions_ai_verdict", "suggestions", _OLD)
    op.drop_column("suggestions", "lt_rule")
    op.drop_column("suggestions", "options")
