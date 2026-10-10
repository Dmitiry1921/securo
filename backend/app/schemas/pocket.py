import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PocketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    goal_id: uuid.UUID | None = None


class PocketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    goal_id: uuid.UUID | None = None
    archived: bool | None = None


class PocketMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: uuid.UUID
    from_pocket_id: uuid.UUID | None = None
    to_pocket_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    idempotency_key: uuid.UUID

    @model_validator(mode="after")
    def endpoints(self):
        if self.from_pocket_id == self.to_pocket_id:
            raise ValueError("Choose different source and destination")
        return self


class PocketRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    goal_id: uuid.UUID | None
    name: str
    currency: str
    start_date: date
    archived: bool
    balance: Decimal
    reserved: Decimal
    available: Decimal
    needs_review: bool


class PocketAccountRead(BaseModel):
    account_id: uuid.UUID
    currency: str
    balance: Decimal
    unallocated: Decimal
    unallocated_available: Decimal
    needs_review: bool
    items: list[PocketRead]
