"""Expose native single-row metadata/status edits without duplicating transactions."""

import uuid
from datetime import date as Date
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.schemas.transaction import TransactionUpdate
from app.services import transaction_service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply
from mcp_server.tools.transfer_matching import _transactions


class TransactionMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["posted", "pending"] | None = None
    category_id: uuid.UUID | None = None
    date: Date | None = None
    notes: str | None = None


@tool(
    name="propose_update_transaction",
    description=_PROPOSAL_PREFACE
    + "Edit an existing transaction's status, category, bank date or notes using the native PATCH service. Does not create another operation or edit amounts. Only the selected transfer leg changes; an already Posted counterpart is not replayed. Set expected_status to guard reconciliation. Confirm Posted only after the bank/user confirms settlement. Posting a Pending debit changes actual balance once; repeated identical edits are safe.",
    parameters={
        "type": "object",
        "properties": {
            "transaction_id": {"type": "string", "format": "uuid"},
            "data": TransactionMetadataUpdate.model_json_schema(),
            "expected_status": {"type": "string", "enum": ["posted", "pending"]},
            "apply": _APPLY_FIELD,
        },
        "required": ["transaction_id", "data"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "transactions"],
)
async def propose_update_transaction(
    *, session, ctx, transaction_id, data, expected_status=None, apply=False
):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    payload = TransactionMetadataUpdate.model_validate(data)
    changes = payload.model_dump(exclude_unset=True)
    if not changes or any(changes.get(k) is None for k in ("status", "date") if k in changes):
        raise ValueError("Supply nonempty edits; status and date cannot be cleared")
    applying = _can_apply(ctx, apply)
    (row,) = await _transactions(session, ws.id, [uuid.UUID(transaction_id)], lock=applying)
    await transaction_service._ensure_category_in_workspace(
        session, ws.id, changes.get("category_id")
    )
    before = {k: getattr(row, k) for k in changes}
    identical = all(before[k] == value for k, value in changes.items())
    if expected_status and row.status != expected_status and not identical:
        raise ValueError("Transaction status changed; inspect it and preview again")
    result = {
        "kind": "update_transaction",
        "target": {"id": transaction_id, "description": row.description},
        "proposed": payload.model_dump(mode="json", exclude_unset=True),
        "before": TransactionMetadataUpdate.model_validate(before).model_dump(
            mode="json", exclude_unset=True
        ),
        "apply_endpoint": f"PATCH /api/transactions/{transaction_id}",
        "already_applied": identical,
        "applied": False,
    }
    if applying:
        if not identical:
            await transaction_service.update_transaction(
                session, row.id, ws.id, ctx.user_id, TransactionUpdate.model_validate(changes)
            )
        result["applied"] = True
    return result
