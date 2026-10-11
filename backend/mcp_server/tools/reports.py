from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.services import dashboard_service, report_service, money_map_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import parse_date, resolve_workspace_id


def _pri_currency(ctx: CallContext) -> str:
    # The user model defaults to USD; reports reach into the DB for the
    # actual primary currency, so the value passed here is just the fallback.
    return "USD"


def _serialize_report(r: Any) -> dict[str, Any]:
    if hasattr(r, "model_dump"):
        return r.model_dump(mode="json")
    return r if isinstance(r, dict) else {"value": str(r)}


@tool(
    name="get_money_map",
    description="Account money routes: income, paired internal transfers, pockets, linked goals and expenses. actual includes confirmed history; plan includes Pending and native recurring forecasts. Allocations and transfers are never counted as external income/expenses. Budget-only categories and missing exchange rates are explicit. This read never creates transactions.",
    parameters={"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["actual", "plan"], "default": "plan"},
        "months": {"type": "integer", "minimum": 1, "maximum": 24, "default": 3},
        "start_date": {"type": "string", "format": "date"},
        "end_date": {"type": "string", "format": "date"},
        "account_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    }, "additionalProperties": False}, tags=["read", "reports"],
)
async def get_money_map(*, session, ctx, mode="plan", months=3, start_date=None,
                        end_date=None, account_ids=None, page=1, limit=50):
    import uuid
    if page < 1 or not 1 <= limit <= 200:
        raise ValueError("Choose a positive page and limit from 1 to 200")
    ws_id = await resolve_workspace_id(session, ctx)
    report = await money_map_service.get_money_map(session, ws_id, ctx.user_id, mode=mode, months=months,
        start_date=parse_date(start_date), end_date=parse_date(end_date),
        account_ids=[uuid.UUID(key) for key in account_ids] if account_ids is not None else None)
    result = report.model_dump(mode="json")
    flows = result["money_map"]["flows"]
    result["flow_total"] = len(flows)
    result["money_map"]["flows"] = flows[(page-1)*limit:page*limit]
    return result


@tool(
    name="get_net_worth",
    description="Net worth time series over the last N months. Use to answer 'how is my net worth trending?'",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 60, "default": 12},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": "monthly"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_net_worth(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 12,
    interval: str = "monthly",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_net_worth_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_income_expenses",
    description="Income vs expenses over the last N months. Use for budgeting questions.",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 60, "default": 12},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": "monthly"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_income_expenses(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 12,
    interval: str = "monthly",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_income_expenses_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_cash_flow",
    description="Forward-looking cash flow projection — current balance plus future bookings and recurring transactions.",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 24, "default": 6},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_cash_flow(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 6,
    interval: str = "daily",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_cash_flow_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_dashboard_snapshot",
    description=(
        "One-call month snapshot: total income/expenses/savings, balances by "
        "currency, top categories. Defaults to the current month."
    ),
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "format": "date", "description": "Any date inside the target month"},
        },
        "additionalProperties": False,
    },
    tags=["read", "dashboard"],
)
async def get_dashboard_snapshot(
    *,
    session: AsyncSession,
    ctx: CallContext,
    month: str | None = None,
) -> dict[str, Any]:
    target = parse_date(month) or app_today().replace(day=1)
    ws_id = await resolve_workspace_id(session, ctx)
    summary = await dashboard_service.get_summary(session, ws_id, ctx.user_id, month=target)
    if hasattr(summary, "model_dump"):
        return summary.model_dump(mode="json")
    return {"value": str(summary)}
