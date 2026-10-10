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
        self.currency = self.currency.upper()
        return self


class TransferScheduleRead(TransferScheduleCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class TransferConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0)
    destination_amount: Decimal = Field(gt=0)
    date: date
