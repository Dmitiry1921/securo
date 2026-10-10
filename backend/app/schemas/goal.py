import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Optional

from app.schemas.recurring_transaction import RecurringFrequency

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GoalCreate(BaseModel):
    name: str
    target_amount: Decimal
    current_amount: Decimal = Decimal("0")
    currency: str = "USD"
    target_date: Optional[_Date] = None
    repeat_frequency: Optional[RecurringFrequency] = None
    tracking_type: str = "manual"
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    metadata_json: Optional[Any] = None

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: str) -> str:
        if v not in ("manual", "account", "asset", "asset_group", "net_worth", "pockets"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, net_worth, or pockets")
        return v


class GoalUpdate(BaseModel):
    name: Optional[str] = None
    target_amount: Optional[Decimal] = None
    current_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    target_date: Optional[_Date] = None
    repeat_frequency: Optional[RecurringFrequency] = None
    tracking_type: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None
    metadata_json: Optional[Any] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("active", "completed", "paused", "archived"):
            raise ValueError("status must be active, completed, paused, or archived")
        return v

    @field_validator("tracking_type")
    @classmethod
    def validate_tracking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("manual", "account", "asset", "asset_group", "net_worth", "pockets"):
            raise ValueError("tracking_type must be manual, account, asset, asset_group, net_worth, or pockets")
        return v


class GoalCompletionRead(BaseModel):
    id: uuid.UUID
    cycle_number: int
    target_amount: Decimal
    saved_amount: Decimal
    currency: str
    target_date: Optional[_Date]
    completed_on: _Date
    spent_amount: Optional[Decimal] = None
    spent_currency: Optional[str] = None
    spent_on: Optional[_Date] = None
    transaction_id: Optional[uuid.UUID]
    model_config = ConfigDict(from_attributes=True)


class GoalPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pocket_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    date: _Date
    category_id: Optional[uuid.UUID] = None


class GoalComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle_number: int = Field(ge=1)
    payment: Optional[GoalPayment] = None
    transaction_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def one_payment(self):
        if self.payment and self.transaction_id:
            raise ValueError("Choose a new payment OR an existing transaction")
        return self


class GoalRestart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle_number: int = Field(ge=1)


class GoalRead(BaseModel):
    repeat_frequency: Optional[RecurringFrequency] = None
    cycle_number: int = 1
    next_target_date: Optional[_Date] = None
    last_completion: Optional[GoalCompletionRead] = None
    valuation_available: bool = True
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    currency: str
    target_amount_primary: Optional[Decimal] = None
    current_amount_primary: Optional[Decimal] = None
    target_date: Optional[_Date] = None
    tracking_type: str
    account_id: Optional[uuid.UUID] = None
    asset_id: Optional[uuid.UUID] = None
    asset_group_id: Optional[uuid.UUID] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    position: int
    metadata_json: Optional[Any] = None
    created_at: datetime
    updated_at: datetime

    # Computed fields
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None  # ahead, on_track, behind, overdue, achieved

    # Linked account/asset info
    account_name: Optional[str] = None
    asset_name: Optional[str] = None
    asset_group_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class GoalSummary(BaseModel):
    valuation_available: bool = True
    id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    currency: str
    target_date: Optional[_Date] = None
    status: str
    icon: Optional[str] = None
    color: Optional[str] = None
    percentage: float = 0
    monthly_contribution: Optional[float] = None
    on_track: Optional[str] = None
