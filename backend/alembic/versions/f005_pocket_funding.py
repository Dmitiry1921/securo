"""Prorated transfers, funding goals and bounded income allocation rules."""

from alembic import op
import sqlalchemy as sa

revision = "f005"
down_revision = "f004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "transfer_schedules",
        sa.Column("amount_mode", sa.String(20), nullable=False, server_default="fixed"),
    )
    op.add_column("transfer_schedules", sa.Column("cycle_day", sa.Integer()))
    op.add_column("transfer_schedules", sa.Column("weekday", sa.Integer()))
    op.add_column("transfer_schedules", sa.Column("initial_cycle_amount", sa.Numeric(15, 2)))
    op.add_column(
        "goals",
        sa.Column(
            "pocket_balance_basis", sa.String(10), nullable=False, server_default="available"
        ),
    )
    op.create_table(
        "pocket_income_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.CheckConstraint(
            "percentage > 0 AND percentage <= 100", name="ck_pocket_income_percentage"
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_pocket_income_dates"
        ),
    )
    op.create_index("ix_pocket_income_rules_account_id", "pocket_income_rules", ["account_id"])


def downgrade():
    op.drop_table("pocket_income_rules")
    op.drop_column("goals", "pocket_balance_basis")
    for column in ("initial_cycle_amount", "weekday", "cycle_day", "amount_mode"):
        op.drop_column("transfer_schedules", column)
