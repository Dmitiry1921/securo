"""Account pockets and internal allocation history.

Revision ID: f001
Revises: 096
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f001"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade():
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "pockets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "account_id", uuid, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("goal_id", uuid, sa.ForeignKey("goals.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for column in ("workspace_id", "account_id", "goal_id"):
        op.create_index(f"ix_pockets_{column}", "pockets", [column])
    op.create_table(
        "pocket_movements",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "account_id", uuid, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("from_pocket_id", uuid, sa.ForeignKey("pockets.id", ondelete="CASCADE")),
        sa.Column("to_pocket_id", uuid, sa.ForeignKey("pockets.id", ondelete="CASCADE")),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("idempotency_key", uuid, nullable=False),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount > 0", name="pocket_movement_positive"),
        sa.CheckConstraint(
            "from_pocket_id IS NOT NULL OR to_pocket_id IS NOT NULL",
            name="pocket_movement_endpoint",
        ),
        sa.CheckConstraint("from_pocket_id <> to_pocket_id", name="pocket_movement_distinct"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_pocket_movement_request"),
    )
    for column in ("workspace_id", "account_id"):
        op.create_index(f"ix_pocket_movements_{column}", "pocket_movements", [column])
    op.add_column("transactions", sa.Column("pocket_id", uuid))
    op.create_foreign_key(
        "transactions_pocket_id_fkey",
        "transactions",
        "pockets",
        ["pocket_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_transactions_pocket_id", "transactions", ["pocket_id"])


def downgrade():
    op.drop_index("ix_transactions_pocket_id", "transactions")
    op.drop_constraint("transactions_pocket_id_fkey", "transactions", type_="foreignkey")
    op.drop_column("transactions", "pocket_id")
    op.drop_table("pocket_movements")
    op.drop_table("pockets")
