"""HTTP and MCP share the same settings validation and financial simulation."""

import uuid
from app.schemas.forecast import ForecastRequest
from app.services import forecast_service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


@tool(
    name="list_forecast_settings",
    description="List expense forecast rules or saved what-if scenarios; these are planning settings, not bank transactions.",
    parameters={
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["expense_rule", "scenario"]}},
        "required": ["kind"],
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def list_forecast_settings(*, session, ctx, kind):
    workspace = await resolve_tool_workspace(session, ctx)
    return {
        "items": [
            p.model_dump(mode="json")
            for p in await forecast_service.list_settings(session, workspace.id, kind)
        ]
    }


@tool(
    name="get_funding_forecast",
    description="Read the shared account/pocket/goal funding projection, dated deficits and assumptions. Pending/projected operations never change real balances. Returns null for unknown valuations. Scenarios are read-only.",
    parameters={
        "type": "object",
        "properties": {
            "request": {
                "type": "object",
                "description": "months (1..24), end_date, scenario_id or inline scenario {id,name,excluded_recurring_ids,income_delay_days,income_multiplier,expense_multiplier,fx_rates}, optional account_ids",
            }
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_funding_forecast(*, session, ctx, request=None):
    workspace = await resolve_tool_workspace(session, ctx)
    result = await forecast_service.get_forecast(
        session, workspace.id, ctx.user_id, ForecastRequest.model_validate(request or {})
    )
    return result.model_dump(mode="json")


@tool(
    name="propose_forecast_setting",
    description=_PROPOSAL_PREFACE
    + "Create/update an expense_rule or scenario, or delete one. Supply a stable UUID id for retry safety. Expense rules require name, account_id, currency, start_date, amount or budget_id, category_ids or pocket_id; optional actual_account_ids (several cards share ONE budget), cycle_day (1..31), distribution daily/weekly/period_start, weekday, end_date, enabled, credit_kind income/refund/reimbursement/loan_principal/other. mode=payments_only maps existing category payments to a pocket without generating more spending. history_from/history_through plus reviewed_history enables advisory recommendations only with six complete periods. Scenario requires id,name; optional excluded_recurring_ids, income_delay_days, income_multiplier, expense_multiplier, fx_rates. Never changes real balances or income allocation rules.",
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["expense_rule", "scenario"]},
            "operation": {"type": "string", "enum": ["save", "delete"]},
            "data": {"type": "object"},
            "apply": _APPLY_FIELD,
        },
        "required": ["kind", "operation", "data"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "reports"],
)
async def propose_forecast_setting(*, session, ctx, kind, operation, data, apply=False):
    workspace = await resolve_tool_workspace(session, ctx, write=True)
    if operation == "delete":
        if set(data) != {"id"}:
            raise ValueError("Deletion requires only id")
        await forecast_service.delete_setting(session, workspace.id, kind, uuid.UUID(data["id"]))
        proposed = data
    elif operation == "save":
        payload = await forecast_service.validate_setting(session, workspace.id, kind, data)
        proposed = payload.model_dump(mode="json")
    else:
        raise ValueError("Unknown operation")
    result = {
        "kind": "forecast_setting",
        "target": {"operation": operation, "kind": kind, "id": str(proposed["id"])},
        "proposed": proposed,
        "applied": False,
    }
    if _can_apply(ctx, apply):
        if operation == "delete":
            await forecast_service.delete_setting(
                session, workspace.id, kind, uuid.UUID(data["id"]), apply=True
            )
        else:
            await forecast_service.save_setting(session, workspace.id, kind, data)
        result["applied"] = True
    return result
