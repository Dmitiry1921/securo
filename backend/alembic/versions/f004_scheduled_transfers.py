"""Pending, recurring native transfers."""
from alembic import op
import sqlalchemy as sa

revision = "f004"
down_revision = "f003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("transfer_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id", ondelete="SET NULL")),
        sa.Column("to_pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id", ondelete="SET NULL")),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("notes", sa.String(1000)),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("day_of_month", sa.Integer()),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint("interval > 0 AND interval <= 3650", name="ck_transfer_interval"),
        sa.CheckConstraint("frequency IN ('days', 'months')", name="ck_transfer_frequency"),
        sa.CheckConstraint("amount > 0", name="ck_transfer_amount"),
        sa.CheckConstraint("from_account_id <> to_account_id", name="ck_transfer_accounts"),
        sa.CheckConstraint("day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31", name="ck_transfer_day"),
    )
    op.create_index("ix_transfer_schedules_workspace_id", "transfer_schedules", ["workspace_id"])
    op.add_column("transactions", sa.Column("transfer_schedule_id", sa.Uuid(), sa.ForeignKey("transfer_schedules.id", ondelete="SET NULL")))
    op.add_column("transactions", sa.Column("transfer_occurrence_date", sa.Date()))
    op.create_index("ix_transactions_transfer_schedule_id", "transactions", ["transfer_schedule_id"])
    op.create_unique_constraint("uq_transfer_occurrence_leg", "transactions", ["transfer_schedule_id", "transfer_occurrence_date", "type"])


def downgrade():
    op.drop_constraint("uq_transfer_occurrence_leg", "transactions", type_="unique")
    op.drop_column("transactions", "transfer_occurrence_date")
    op.drop_column("transactions", "transfer_schedule_id")
    op.drop_table("transfer_schedules")
