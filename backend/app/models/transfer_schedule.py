"""A schedule materializes the existing native, paired transfer transactions."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class TransferSchedule(Base):
    __tablename__ = "transfer_schedules"
    __table_args__ = (
        CheckConstraint("interval > 0 AND interval <= 3650", name="ck_transfer_interval"),
        CheckConstraint("frequency IN ('days', 'months')", name="ck_transfer_frequency"),
        CheckConstraint("amount > 0", name="ck_transfer_amount"),
        CheckConstraint("from_account_id <> to_account_id", name="ck_transfer_accounts"),
        CheckConstraint("day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31", name="ck_transfer_day"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    from_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    to_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    from_pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pockets.id", ondelete="SET NULL"))
    to_pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pockets.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(1000))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    amount_mode: Mapped[str] = mapped_column(String(20), default="fixed", server_default="fixed")
    initial_cycle_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cycle_day: Mapped[int | None] = mapped_column(Integer)
    weekday: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    frequency: Mapped[str] = mapped_column(String(10))
    interval: Mapped[int] = mapped_column(Integer)
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
