"""Allow automatic recurring placeholders with manual payment confirmation."""

from alembic import op
import sqlalchemy as sa

revision = "f006"
down_revision = "f005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recurring_transactions", sa.Column(
        "generation_status", sa.String(10), nullable=False, server_default="auto",
    ))
    op.create_check_constraint("ck_recurring_generation_status", "recurring_transactions",
                               "generation_status IN ('auto', 'pending')")


def downgrade():
    op.drop_constraint("ck_recurring_generation_status", "recurring_transactions", type_="check")
    op.drop_column("recurring_transactions", "generation_status")
