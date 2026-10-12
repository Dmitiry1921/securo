"""Planning rules and scenarios; no changes to financial balances."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f007"
down_revision = "f006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_settings_workspace_id", "forecast_settings", ["workspace_id"])


def downgrade():
    op.drop_table("forecast_settings")
