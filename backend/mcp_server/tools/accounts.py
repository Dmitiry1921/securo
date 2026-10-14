from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import account_service
from app.schemas.account import AccountUpdate
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import (
    num, parse_date, parse_uuid, resolve_workspace_id, resolve_tool_workspace,
)


@tool(
    name="list_accounts",
    description=(
        "List the user's accounts (checking, savings, credit cards, wallets, etc.) "
        "with current balances. Closed accounts are excluded by default."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_closed": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def list_accounts(
    *,
    session: AsyncSession,
    ctx: CallContext,
    include_closed: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await account_service.get_accounts(session, ws_id, include_closed=include_closed)
    # rows is already a list of dicts (per service contract), but normalize keys.
    items: list[dict[str, Any]] = []
    for r in rows:
        items.append({
            "id": str(r.get("id")) if r.get("id") else None,
            "name": r.get("name"),
            "type": r.get("type"),
            "currency": r.get("currency"),
            "balance": num(r.get("current_balance", r.get("balance"))),
            "balance_primary": num(r.get("balance_primary")),
            "is_closed": bool(r.get("is_closed", False)),
            "institution": r.get("institution_name"),
        })
    return {"items": items, "total": len(items)}


@tool(
    name="update_credit_card_terms",
    description=(
        "Update a credit card's confirmed limit, cycle days or minimum payment. "
        "External clients only; use only for a user-authorized update. This does "
        "not change balances, create payments or schedule repayment. "
        "Omit unknown fields; null clears a previously configured value."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "credit_limit": {"type": ["number", "null"], "minimum": 0},
            "statement_close_day": {"type": ["integer", "null"], "minimum": 1, "maximum": 31},
            "payment_due_day": {"type": ["integer", "null"], "minimum": 1, "maximum": 31},
            "minimum_payment": {"type": ["number", "null"], "minimum": 0},
        },
        "required": ["account_id"],
        "additionalProperties": False,
    },
    tags=["write", "accounts"],
)
async def update_credit_card_terms(
    *, session: AsyncSession, ctx: CallContext, account_id: str, **terms,
) -> dict[str, Any]:
    if not ctx.external:
        return {"error": "update_credit_card_terms is available only to external MCP clients"}
    workspace = await resolve_tool_workspace(session, ctx, write=True)
    ws_id = workspace.workspace.id
    account_uuid = parse_uuid(account_id)
    if account_uuid is None:
        return {"error": "account not found"}
    account = await account_service.get_account(session, account_uuid, ws_id)
    if account is None:
        return {"error": "account not found"}
    if account.type != "credit_card":
        return {"error": "account must be a credit card"}
    if not terms or set(terms) - {
        "credit_limit", "statement_close_day", "payment_due_day", "minimum_payment",
    }:
        return {"error": "provide credit card terms only"}
    for key, value in terms.items():
        if value is None:
            continue
        if key in {"statement_close_day", "payment_due_day"}:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
                return {"error": "cycle days must be integers from 1 to 31"}
        else:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0:
                return {"error": "amounts must be finite and nonnegative"}
    updated = await account_service.update_account(
        session, account_uuid, ws_id, AccountUpdate.model_validate(terms),
    )
    return {"updated": True, "id": str(updated.id), "terms": {
        k: num(getattr(updated, k)) for k in terms
    }}


@tool(
    name="get_account_summary",
    description=(
        "Income, expenses, and net for a single account over a date range. "
        "Defaults to the current month if no range is provided."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "from_date": {"type": "string", "format": "date"},
            "to_date": {"type": "string", "format": "date"},
        },
        "required": ["account_id"],
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def get_account_summary(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    account_uuid = parse_uuid(account_id)
    if account_uuid is None:
        return dict(error="account not found")

    summary = await account_service.get_account_summary(
        session,
        account_uuid,
        ws_id,
        date_from=parse_date(from_date),
        date_to=parse_date(to_date),
    )
    if summary is None:
        return {"error": "account not found"}
    return {
        "account_id": str(summary["account_id"]),
        "current_balance": num(summary.get("current_balance")),
        "opening_balance": num(summary.get("opening_balance")),
        "monthly_income": num(summary.get("monthly_income")),
        "monthly_expenses": num(summary.get("monthly_expenses")),
        "projected_income": num(summary.get("projected_income")),
        "projected_expenses": num(summary.get("projected_expenses")),
    }
