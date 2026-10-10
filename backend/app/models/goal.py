import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.asset import Asset
    from app.models.asset_group import AssetGroup
    from app.models.user import User


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    current_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    initial_amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    target_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    current_amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    repeat_frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    repeat_anchor_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cycle_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    cycle_started_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    tracking_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual, account, asset, asset_group, net_worth
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    asset_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, completed, paused, archived
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()
    account: Mapped[Optional["Account"]] = relationship()
    asset: Mapped[Optional["Asset"]] = relationship()
    asset_group: Mapped[Optional["AssetGroup"]] = relationship()


class GoalCompletion(Base):
    """Immutable snapshot of a manually completed cycle; spending stays in the ledger."""
    __tablename__ = "goal_completions"
    __table_args__ = (UniqueConstraint("goal_id", "cycle_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    cycle_number: Mapped[int] = mapped_column(Integer)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    saved_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3))
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completed_on: Mapped[date] = mapped_column(Date)
    spent_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    spent_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    spent_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
