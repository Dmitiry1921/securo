"""MCP and UI share transfer scheduling and manual confirmation services."""
import uuid
from datetime import date as Date
from app.schemas.transfer_schedule import TransferScheduleCreate, TransferScheduleRead, TransferConfirm
from app.services import transfer_schedule_service as service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


@tool(name="list_transfer_schedules", description="List recurring internal transfer plans. Amount is fixed in the native currency of either endpoint; the other amount uses an estimated exchange rate. Transfers never count as income or expense.", parameters={"type": "object", "properties": {}, "additionalProperties": False}, tags=["read", "transactions"])
async def list_transfer_schedules(*, session, ctx):
    ws = await resolve_tool_workspace(session, ctx)
    return [TransferScheduleRead.model_validate(p).model_dump(mode="json") for p in await service.list_schedules(session, ws.id)]


@tool(name="propose_transfer_schedule", description=_PROPOSAL_PREFACE + "Create or replace a recurring transfer plan. frequency=days with interval=N means every N days; frequency=months with interval=N and day_of_month means every N calendar months on that day (clamped in shorter months). Source/destination pockets are optional. Occurrences become Pending and require manual confirmation; no bank transfer is initiated. To pause a plan, replace it with is_active=false. Existing transactions remain unchanged.", parameters={"type": "object", "properties": {"schedule_id": {"type": "string", "format": "uuid"}, "data": TransferScheduleCreate.model_json_schema(), "apply": _APPLY_FIELD}, "required": ["data"], "additionalProperties": False}, is_proposal=True, tags=["propose", "transactions"])
async def propose_transfer_schedule(*, session, ctx, data, schedule_id=None, apply=False):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    payload = TransferScheduleCreate.model_validate(data)
    preview = {"kind": "transfer_schedule", "proposed": payload.model_dump(mode="json"), "schedule_id": schedule_id}
    if _can_apply(ctx, apply):
        plan = await service.save_schedule(session, ws.id, ctx.user_id, payload, uuid.UUID(schedule_id) if schedule_id else None)
        return {**preview, "applied": True, "result": TransferScheduleRead.model_validate(plan).model_dump(mode="json")}
    return preview


@tool(name="propose_transfer_occurrence", description=_PROPOSAL_PREFACE + "Materialize one scheduled occurrence as two linked Pending transactions, including future dates. Retrying the same schedule/date returns the existing pair, even after confirmation or a change to its payment date. No bank money moves.", parameters={"type": "object", "properties": {"schedule_id": {"type": "string", "format": "uuid"}, "date": {"type": "string", "format": "date"}, "apply": _APPLY_FIELD}, "required": ["schedule_id", "date"], "additionalProperties": False}, is_proposal=True, tags=["propose", "transactions"])
async def propose_transfer_occurrence(*, session, ctx, schedule_id, date: str, apply=False):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    on = Date.fromisoformat(date)
    preview = {"kind": "transfer_occurrence", "schedule_id": schedule_id, "date": date}
    if _can_apply(ctx, apply):
        pair = await service.materialize(session, ws.id, uuid.UUID(schedule_id), on)
        return {**preview, "applied": True, "transfer_pair_id": str(pair[0].transfer_pair_id), "transaction_ids": [str(tx.id) for tx in pair]}
    return preview


@tool(name="propose_confirm_transfer", description=_PROPOSAL_PREFACE + "Confirm an existing Pending pair atomically using the actual source amount, actual destination_amount and actual date. Both legs become Posted. Retrying identical confirmation is safe. This records a transfer that already happened; it never sends money.", parameters={"type": "object", "properties": {"transfer_pair_id": {"type": "string", "format": "uuid"}, "data": TransferConfirm.model_json_schema(), "apply": _APPLY_FIELD}, "required": ["transfer_pair_id", "data"], "additionalProperties": False}, is_proposal=True, tags=["propose", "transactions"])
async def propose_confirm_transfer(*, session, ctx, transfer_pair_id, data, apply=False):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    payload = TransferConfirm.model_validate(data)
    preview = {"kind": "confirm_transfer", "transfer_pair_id": transfer_pair_id, "proposed": payload.model_dump(mode="json")}
    if _can_apply(ctx, apply):
        pair = await service.confirm(session, ws.id, ctx.user_id, uuid.UUID(transfer_pair_id), payload)
        return {**preview, "applied": True, "transaction_ids": [str(tx.id) for tx in pair]}
    return preview


@tool(name="list_pending_transfers", description="List both native legs of Pending internal transfers, with pair IDs for manual confirmation. These reserve money but are not completed bank movements.", parameters={"type": "object", "properties": {}, "additionalProperties": False}, tags=["read", "transactions"])
async def list_pending_transfers(*, session, ctx):
    ws = await resolve_tool_workspace(session, ctx)
    return await service.pending_pairs(session, ws.id)
