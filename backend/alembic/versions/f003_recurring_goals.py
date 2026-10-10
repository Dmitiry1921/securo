"""Recurring goals and immutable completion history."""

from alembic import op
import sqlalchemy as sa

revision = "f003"
down_revision = "f002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("goals", sa.Column("repeat_frequency", sa.String(20), nullable=True))
    op.add_column("goals", sa.Column("repeat_anchor_day", sa.Integer(), nullable=True))
    op.add_column(
        "goals", sa.Column("cycle_number", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("goals", sa.Column("cycle_started_on", sa.Date(), nullable=True))
    op.create_table(
        "goal_completions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "goal_id", sa.Uuid(), sa.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("target_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("saved_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=False),
        sa.Column("spent_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("spent_currency", sa.String(3), nullable=True),
        sa.Column("spent_on", sa.Date(), nullable=True),
        sa.Column(
            "transaction_id",
            sa.Uuid(),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.UniqueConstraint("goal_id", "cycle_number"),
    )
    op.create_index("ix_goal_completions_goal_id", "goal_completions", ["goal_id"])


def downgrade():
    op.drop_table("goal_completions")
    for name in ("cycle_started_on", "cycle_number", "repeat_anchor_day", "repeat_frequency"):
        op.drop_column("goals", name)
