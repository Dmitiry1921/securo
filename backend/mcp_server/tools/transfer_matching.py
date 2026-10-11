"""Expose the UI's existing transfer reconciliation through MCP."""

import uuid

from sqlalchemy import select

from app.models.transaction import Transaction
from app.services import account_service, transaction_service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


async def _transactions(session, workspace_id, ids, *, lock):
    query = select(Transaction).where(
        Transaction.workspace_id == workspace_id, Transaction.id.in_(ids),
    ).order_by(Transaction.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = list((await session.scalars(query)).all())
    if len(rows) != len(ids):
        raise ValueError("Transaction not found")
    if any(tx.is_ignored or tx.source == "opening_balance" for tx in rows):
        raise ValueError("Opening balances and ignored transactions cannot be reconciled")
    return rows


def _pair_result(rows):
    return {
        "transfer_pair_id": str(rows[0].transfer_pair_id),
        "transaction_ids": [str(tx.id) for tx in rows],
    }


@tool(
    name="propose_link_transfer",
    description=_PROPOSAL_PREFACE + (
        "Link TWO existing transactions in different accounts as a native transfer. "
        "Requires one debit and one credit. Preserves amounts, dates, individual posting "
        "statuses, categories and pockets; creates no rows and changes no balances. "
        "Supports cross-currency and Pending/Posted bank settlement differences. "
        "Retrying the same pair is safe. Use this instead of creating a second transfer "
        "when both bank statement legs already exist."
    ),
    parameters={
        "type": "object", "properties": {
            "transaction_ids": {"type": "array", "minItems": 2, "maxItems": 2,
                                "uniqueItems": True,
                                "items": {"type": "string", "format": "uuid"}},
            "apply": _APPLY_FIELD,
        }, "required": ["transaction_ids"], "additionalProperties": False,
    },
    is_proposal=True, tags=["propose", "transactions"],
)
async def propose_link_transfer(*, session, ctx, transaction_ids, apply=False):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    ids = [uuid.UUID(value) for value in transaction_ids]
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError("Exactly two different transactions are required")
    applying = _can_apply(ctx, apply)
    rows = await _transactions(session, ws.id, ids, lock=applying)
    if rows[0].account_id == rows[1].account_id or {tx.type for tx in rows} != {"debit", "credit"}:
        raise ValueError("Select one debit and one credit in different accounts")
    linked = rows[0].transfer_pair_id is not None and (
        rows[0].transfer_pair_id == rows[1].transfer_pair_id
    )
    if not linked and any(tx.transfer_pair_id for tx in rows):
        raise ValueError("Transaction is already linked to another transfer")
    preview = {
        "kind": "link_transfer", "transaction_ids": transaction_ids,
        "proposed": {"transaction_ids": transaction_ids},
        "apply_endpoint": "POST /api/transactions/link-transfer",
        "already_linked": linked, "applied": False,
    }
    if applying:
        if not linked:
            rows = await transaction_service.link_existing_as_transfer(session, ws.id, ids)
        return {**preview, "applied": True, **_pair_result(rows)}
    return preview


@tool(
    name="propose_transfer_counterpart",
    description=_PROPOSAL_PREFACE + (
        "Create ONLY the missing leg of an existing bank transaction in another manual "
        "account, linking both as a native transfer. The existing leg is never duplicated. "
        "Same-currency only: date, amount and Pending/Posted status are preserved. "
        "For cross-currency statements, import the actual counterpart then use "
        "propose_link_transfer. First check list_transactions for an existing counterpart. "
        "Retrying with the same transaction and account returns the existing pair. "
        "Use propose_assign_pocket afterward to assign each leg to its account's pocket."
    ),
    parameters={
        "type": "object", "properties": {
            "transaction_id": {"type": "string", "format": "uuid"},
            "counterpart_account_id": {"type": "string", "format": "uuid"},
            "apply": _APPLY_FIELD,
        }, "required": ["transaction_id", "counterpart_account_id"],
        "additionalProperties": False,
    },
    is_proposal=True, tags=["propose", "transactions"],
)
async def propose_transfer_counterpart(
    *, session, ctx, transaction_id, counterpart_account_id, apply=False,
):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    applying = _can_apply(ctx, apply)
    tx_id, account_id = uuid.UUID(transaction_id), uuid.UUID(counterpart_account_id)
    anchor, = await _transactions(session, ws.id, [tx_id], lock=applying)
    account = await account_service.get_account(session, account_id, ws.id)
    if account is None or account.is_closed or account.connection_id is not None:
        raise ValueError("Select an open manual account in this workspace")
    if account_id == anchor.account_id:
        raise ValueError("Counterpart must be in a different account")
    if account.currency != anchor.currency:
        raise ValueError("Import the actual cross-currency leg and use propose_link_transfer")
    pair = await transaction_service.get_transfer_pair(session, ws.id, tx_id)
    if anchor.transfer_pair_id and (pair is None or pair.account_id != account_id):
        raise ValueError("Transaction is already linked to another account")
    if pair is None:
        existing = await session.scalar(select(Transaction.id).where(
            Transaction.workspace_id == ws.id, Transaction.account_id == account_id,
            Transaction.type == ("credit" if anchor.type == "debit" else "debit"),
            Transaction.date == anchor.date, Transaction.amount == anchor.amount,
            Transaction.currency == anchor.currency, Transaction.is_ignored == False,
        ).limit(1))
        if existing:
            raise ValueError("A counterpart already exists; use propose_link_transfer")
    preview = {
        "kind": "transfer_counterpart", "transaction_id": transaction_id,
        "counterpart_account_id": counterpart_account_id,
        "amount": str(anchor.amount), "currency": account.currency,
        "date": anchor.date.isoformat(), "status": anchor.status,
        "type": "credit" if anchor.type == "debit" else "debit",
        "already_linked": pair is not None, "applied": False,
        "proposed": {"to_account_id": counterpart_account_id},
        "apply_endpoint": f"POST /api/transactions/{transaction_id}/create-counterpart",
    }
    if applying:
        rows = (anchor, pair) if pair else await transaction_service.create_transfer_counterpart(
            session, ws.id, ctx.user_id, tx_id, account_id,
        )
        return {**preview, "applied": True, **_pair_result(rows)}
    return preview
