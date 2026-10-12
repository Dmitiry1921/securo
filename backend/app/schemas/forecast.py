import uuid
from datetime import date as Date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpenseForecastRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    account_id: uuid.UUID
    actual_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    pocket_id: uuid.UUID | None = None
    budget_id: uuid.UUID | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    cycle_day: int = Field(default=1, ge=1, le=31)
    distribution: Literal["daily", "weekly", "period_start"] = "daily"
    mode: Literal["budget", "payments_only"] = "budget"
    credit_kind: Literal["income", "refund", "reimbursement", "loan_principal", "other"] = "refund"
    weekday: int = Field(default=0, ge=0, le=6)
    start_date: Date
    end_date: Date | None = None
    enabled: bool = True
    history_from: Date | None = None
    history_through: Date | None = None
    reviewed_history: bool = False

    @model_validator(mode="after")
    def check_source(self):
        if self.mode == "budget" and self.budget_id is None and self.amount is None:
            raise ValueError("Provide budget_id or amount")
        if not self.category_ids and self.pocket_id is None:
            raise ValueError("Choose categories or a pocket to match actual spending")
        if len(set(self.category_ids)) != len(self.category_ids):
            raise ValueError("Duplicate categories")
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        if self.reviewed_history and (
            not self.history_from
            or not self.history_through
            or self.history_from > self.history_through
        ):
            raise ValueError("Reviewed history requires an ordered coverage range")
        return self


class ForecastScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    excluded_recurring_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    income_delay_days: int = Field(default=0, ge=0, le=365)
    income_multiplier: Decimal = Field(default=Decimal(1), ge=0, le=10)
    expense_multiplier: Decimal = Field(default=Decimal(1), ge=0, le=10)
    # Native currency -> reporting currency, e.g. IDR -> USD. No invented rates.
    fx_rates: dict[str, Decimal] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def valid_rates(self):
        for currency, rate in self.fx_rates.items():
            if (
                len(currency) != 3
                or not currency.isalpha()
                or currency != currency.upper()
                or rate <= 0
                or not rate.is_finite()
            ):
                raise ValueError("FX rates require an uppercase currency and positive finite value")
        return self


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    months: int = Field(default=12, ge=1, le=24)
    end_date: Date | None = None
    scenario_id: uuid.UUID | None = None
    scenario: ForecastScenario | None = None
    account_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def one_scenario(self):
        if self.scenario_id and self.scenario:
            raise ValueError("Choose a saved scenario or an inline scenario")
        return self


class ForecastWarning(BaseModel):
    code: str
    date: Date | None = None
    account_id: str | None = None
    goal_id: str | None = None
    flow_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    detail: str


class GoalForecast(BaseModel):
    goal_id: str
    name: str
    currency: str
    deadline: Date | None
    target: Decimal
    current: Decimal | None
    projected: Decimal | None
    shortfall: Decimal | None
    achieved_date: Date | None
    status: Literal["funded", "shortfall", "unknown"]
    reason: str | None = None
    history: list[dict] = Field(default_factory=list)
    cycles: list[dict] = Field(default_factory=list)


class FundingForecast(BaseModel):
    start_date: Date
    end_date: Date
    currency: str
    scenario: ForecastScenario | None = None
    rates: list[dict]
    opening_accounts: list[dict]
    timeline: list[dict]
    events: list[dict]
    goals: list[GoalForecast]
    warnings: list[ForecastWarning]
    budget_periods: list[dict]
    history_recommendations: list[dict] = Field(default_factory=list)
    # Both numbers are unknown if any required currency is unvalued.
    ending_balance: Decimal | None
    free_funds: Decimal | None
