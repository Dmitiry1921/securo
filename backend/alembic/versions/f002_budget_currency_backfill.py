"""Correct the currency of budgets created before currency input was honored.

Revision ID: f002
Revises: f001
"""

from alembic import op

revision = "f002"
down_revision = "f001"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier create paths saved amounts in the user's display currency but
    # left the column at its old BRL default. Those rows have no conversion.
    op.execute("""
        UPDATE budgets AS b
        SET currency = COALESCE(NULLIF(u.preferences->>'currency_display', ''), 'USD'),
            amount_primary = b.amount
        FROM users AS u
        WHERE b.user_id = u.id AND b.amount_primary IS NULL
    """)


def downgrade():
    pass
