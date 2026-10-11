from pydantic import BaseModel
from typing import Literal

from app.schemas.pocket import PocketAccountRead, PocketIncomeRuleRead
from app.schemas.goal import GoalRead


class MoneyMapAccount(PocketAccountRead):
    name: str


# Resolve the inherited pocket schema's forward reference in its own namespace.
MoneyMapAccount.model_rebuild(_types_namespace={"PocketIncomeRuleRead": PocketIncomeRuleRead})


class MoneyMapFlow(BaseModel):
    id: str
    date: str
    kind: Literal["income", "expense", "transfer", "allocation", "investment"]
    status: Literal["posted", "pending", "projected"]
    description: str
    from_account_id: str | None = None
    to_account_id: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    from_pocket_id: str | None = None
    to_pocket_id: str | None = None
    amount: float
    currency: str
    amount_primary: float | None = None
    received_amount: float | None = None
    received_currency: str | None = None
    category_id: str | None = None
    category_name: str | None = None


class MoneyMapBudgetGap(BaseModel):
    month: str
    category: str
    amount: float
    currency: str


class MoneyMapData(BaseModel):
    start_date: str
    end_date: str
    mode: Literal["actual", "plan"]
    accounts: list[MoneyMapAccount]
    goals: list[GoalRead]
    flows: list[MoneyMapFlow]
    budget_gaps: list[MoneyMapBudgetGap]
    missing_rates: list[str]
    unpaired_transfers: int = 0


class ReportBreakdown(BaseModel):
    key: str
    label: str
    value: float
    color: str


class ReportSummary(BaseModel):
    primary_value: float
    change_amount: float
    change_percent: float | None
    breakdowns: list[ReportBreakdown]


class ReportCompositionItem(BaseModel):
    key: str
    label: str
    value: float
    color: str
    group: str


class ReportDataPoint(BaseModel):
    date: str
    value: float
    breakdowns: dict[str, float]
    change: float | None = None
    composition: list[ReportCompositionItem] = []


class ReportMeta(BaseModel):
    type: str
    series_keys: list[str]
    currency: str
    interval: str
    forecast_start_date: str | None = None
    baseline_active: bool = False
    baseline_lookback_days: int | None = None


class CategoryTrendItem(BaseModel):
    key: str
    label: str
    color: str
    total: float
    group: str
    series: list[ReportDataPoint]


class ReportResponse(BaseModel):
    summary: ReportSummary
    trend: list[ReportDataPoint]
    meta: ReportMeta
    composition: list[ReportCompositionItem] = []
    category_trend: list[CategoryTrendItem] = []
    money_map: MoneyMapData | None = None
