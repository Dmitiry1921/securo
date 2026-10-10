"""Pockets use the HTTP domain service and the same workspace access checks."""

from app.schemas.pocket import PocketCreate, PocketMove, PocketUpdate, PocketIncomeRuleCreate, PocketIncomeRuleStop
from app.schemas.transaction import TransferCreate, TransactionUpdate
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services import pocket_service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


@tool(
    name="list_pockets",
    description="List account pockets, native balances, pending reservations and unallocated funds. Pockets are part of the account balance, not additional assets.",
    parameters={
        "type": "object",
        "properties": {"account_id": {"type": "string", "format": "uuid"}},
        "required": ["account_id"],
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def list_pockets(*, session, ctx, account_id: str):
    import uuid

    workspace = await resolve_tool_workspace(session, ctx)
    return (
        await pocket_service.get_account_pockets(session, workspace.id, uuid.UUID(account_id))
    ).model_dump(mode="json")


@tool(
    name="propose_pocket",
    description=_PROPOSAL_PREFACE
    + "Create/update pockets or move money inside ONE account. income_rule allocates a percentage of posted real income to a pocket from a start date; excludes Pending/future/opening balances/internal transfers. Supply a stable UUID id for retry safety. stop_income_rule closes a rule's interval using rule_id and data.end_date, preserving past allocations. Rules are immutable; to change a percentage, close the old rule then create a new one starting the following day. These actions do not move bank money. A goal must have tracking_type=pockets. Reuse the same idempotency_key when retrying a movement.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["create", "update", "move", "income_rule", "stop_income_rule"]},
            "pocket_id": {"type": "string", "format": "uuid"},
            "rule_id": {"type": "string", "format": "uuid"},
            "data": {
                "type": "object",
                "description": "create: account_id, name, optional goal_id. update: name, goal_id, archived. move: account_id, from_pocket_id, to_pocket_id, amount, idempotency_key (UUID). income_rule: id (UUID), account_id, pocket_id, percentage (0 < p <= 100), start_date, optional end_date. stop_income_rule: end_date (inclusive; not in the past).",
            },
            "apply": _APPLY_FIELD,
        },
        "required": ["operation", "data"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "accounts"],
)
async def propose_pocket(
    *, session, ctx, operation: str, data: dict, pocket_id: str | None = None, rule_id: str | None = None, apply: bool = False
):
    import uuid

    workspace = await resolve_tool_workspace(session, ctx, write=True)
    schemas = {"create": PocketCreate, "update": PocketUpdate, "move": PocketMove,
               "income_rule": PocketIncomeRuleCreate, "stop_income_rule": PocketIncomeRuleStop}
    if operation not in schemas:
        raise ValueError("Unknown pocket operation")
    payload = schemas[operation].model_validate(data)
    target_id = rule_id if operation == "stop_income_rule" else pocket_id
    target = uuid.UUID(target_id) if target_id else None
    if operation in ("update", "stop_income_rule") and target is None:
        raise ValueError("pocket_id / rule_id is required")
    preview = {
        "kind": "pocket",
        "proposed": payload.model_dump(mode="json", exclude_unset=True),
        "target": {"id": target_id, "operation": operation},
    }
    if not _can_apply(ctx, apply):
        return preview
    if isinstance(payload, PocketCreate):
        result = await pocket_service.create_pocket(session, workspace.id, ctx.user_id, payload)
    elif isinstance(payload, PocketUpdate):
        result = await pocket_service.update_pocket(session, workspace.id, target, payload)
    elif isinstance(payload, PocketIncomeRuleCreate):
        result = await pocket_service.create_income_rule(session, workspace.id, payload)
    elif isinstance(payload, PocketIncomeRuleStop):
        result = await pocket_service.stop_income_rule(session, workspace.id, target, payload.end_date)
    else:
        result = await pocket_service.move(session, workspace.id, ctx.user_id, payload)
    return {**preview, "applied": True, "result": result.model_dump(mode="json")}


@tool(
    name="propose_assign_pocket",
    description=_PROPOSAL_PREFACE
    + "Assign an existing transaction to a pocket of its account, or clear the assignment with null. Categories and budgets remain unchanged.",
    parameters={
        "type": "object",
        "properties": {
            "transaction_id": {"type": "string", "format": "uuid"},
            "pocket_id": {"type": ["string", "null"], "format": "uuid"},
            "apply": _APPLY_FIELD,
        },
        "required": ["transaction_id", "pocket_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "transactions"],
)
async def propose_assign_pocket(
    *, session, ctx, transaction_id: str, pocket_id: str | None, apply: bool = False
):
    import uuid

    workspace = await resolve_tool_workspace(session, ctx, write=True)
    tx_id = uuid.UUID(transaction_id)
    tx = await transaction_service.get_transaction(session, tx_id, workspace.id)
    if not tx:
        raise ValueError("Transaction not found")
    payload = TransactionUpdate(pocket_id=uuid.UUID(pocket_id) if pocket_id else None)
    await pocket_service.validate_transaction(
        session,
        Transaction(
            workspace_id=workspace.id,
            account_id=tx.account_id,
            pocket_id=payload.pocket_id,
            currency=tx.currency,
            date=tx.date,
        ),
    )
    preview = {
        "kind": "assign_pocket",
        "target": {"id": transaction_id},
        "proposed": payload.model_dump(mode="json", exclude_unset=True),
    }
    if _can_apply(ctx, apply):
        await transaction_service.update_transaction(
            session, tx_id, workspace.id, ctx.user_id, payload
        )
        return {**preview, "applied": True, "id": transaction_id}
    return preview


@tool(
    name="propose_transfer",
    description=_PROPOSAL_PREFACE
    + "Record a bank transfer with optional source/destination pockets. Set status=pending to plan a transfer without changing actual balances. Cross-currency pocket transfers require the actual destination_amount. This creates both transaction legs atomically; it does not initiate a transfer at a bank.",
    parameters={
        "type": "object",
        "properties": {"data": TransferCreate.model_json_schema(), "apply": _APPLY_FIELD},
        "required": ["data"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "transactions"],
)
async def propose_transfer(*, session, ctx, data: dict, apply: bool = False):
    workspace = await resolve_tool_workspace(session, ctx, write=True)
    payload = TransferCreate.model_validate(data)
    for account_id in (payload.from_account_id, payload.to_account_id):
        await pocket_service.account(session, workspace.id, account_id)
    preview = {
        "kind": "create_transfer",
        "proposed": payload.model_dump(mode="json", exclude_unset=True),
    }
    if _can_apply(ctx, apply):
        debit, credit = await transaction_service.create_transfer(
            session, workspace.id, ctx.user_id, payload
        )
        return {**preview, "applied": True, "debit_id": str(debit.id), "credit_id": str(credit.id)}
    return preview
