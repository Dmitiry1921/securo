"""Earmarks inside an account; never additional assets or bank transactions."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class Pocket(Base):
    __tablename__ = "pockets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PocketMovement(Base):
    __tablename__ = "pocket_movements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="pocket_movement_positive"),
        CheckConstraint(
            "from_pocket_id IS NOT NULL OR to_pocket_id IS NOT NULL",
            name="pocket_movement_endpoint",
        ),
        CheckConstraint("from_pocket_id <> to_pocket_id", name="pocket_movement_distinct"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_pocket_movement_request"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    from_pocket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pockets.id", ondelete="CASCADE")
    )
    to_pocket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pockets.id", ondelete="CASCADE")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PocketIncomeRule(Base):
    """A bounded percentage of real income; dates/percentage are immutable except closing the interval."""
    __tablename__ = "pocket_income_rules"
    __table_args__ = (
        CheckConstraint("percentage > 0 AND percentage <= 100", name="ck_pocket_income_percentage"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_pocket_income_dates"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    pocket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pockets.id", ondelete="CASCADE"))
    percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
