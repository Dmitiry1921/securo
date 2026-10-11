import uuid
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TransferScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    from_pocket_id: uuid.UUID | None = None
    to_pocket_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    amount_mode: Literal["fixed", "prorated_weekly"] = "fixed"
    initial_cycle_amount: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    cycle_day: int | None = Field(default=None, ge=1, le=31)
    weekday: int | None = Field(default=None, ge=0, le=6)
    currency: str = Field(min_length=3, max_length=3)
    frequency: Literal["days", "months"]
    interval: int = Field(default=1, ge=1, le=3650)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    start_date: date
    end_date: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.from_account_id == self.to_account_id:
            raise ValueError("Choose different source and destination accounts")
        if self.frequency == "days" and self.day_of_month is not None:
            raise ValueError("day_of_month is only supported for monthly intervals")
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.amount_mode == "prorated_weekly":
            if self.frequency != "days" or self.interval != 7 or self.cycle_day is None or self.weekday is None:
                raise ValueError("Prorated weekly plans require a cycle day, weekday and a 7-day interval")
        elif self.cycle_day is not None or self.weekday is not None or self.initial_cycle_amount is not None:
            raise ValueError("Cycle day and weekday are only supported for prorated weekly plans")
        self.currency = self.currency.upper()
        return self


class TransferScheduleRead(TransferScheduleCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class TransferConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    destination_amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    date: date


class TransferPendingUpdate(TransferConfirm):
    notes: str | None = Field(default=None, max_length=1000)
