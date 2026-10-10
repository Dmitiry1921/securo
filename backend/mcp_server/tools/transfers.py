"""MCP and UI share transfer scheduling and manual confirmation services."""
import uuid
from datetime import date as Date
from app.schemas.transfer_schedule import TransferScheduleCreate, TransferScheduleRead, TransferConfirm
from app.services import transfer_schedule_service as service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


@tool(name="list_transfer_schedules", description="List recurring internal transfer plans. fixed mode uses an amount per transfer. prorated_weekly splits a monthly amount by calendar days between cycle_day anchors, transferring on weekday (0=Monday) and the cycle start. Other currency uses an estimated exchange rate. Transfers never count as income or expense.", parameters={"type": "object", "properties": {}, "additionalProperties": False}, tags=["read", "transactions"])
async def list_transfer_schedules(*, session, ctx):
    ws = await resolve_tool_workspace(session, ctx)
    return [TransferScheduleRead.model_validate(p).model_dump(mode="json") for p in await service.list_schedules(session, ws.id)]


@tool(name="propose_transfer_schedule", description=_PROPOSAL_PREFACE + "Create or replace a recurring transfer plan. days/interval=N means every N days; months/interval=N/day_of_month means every N calendar months on that day. amount_mode=prorated_weekly requires frequency=days, interval=7, cycle_day (monthly funding day) and weekday (0=Monday); amount is then the MONTHLY envelope, split until the next weekday or cycle boundary. Optional initial_cycle_amount splits a first-cycle remainder from start_date until the next cycle start, then uses amount for full cycles. It includes short transfers on cycle start; rounding sums to the exact envelope per complete cycle. Pockets are optional. Occurrences become Pending and require manual confirmation; no bank money moves. To pause, replace with is_active=false. Existing transactions remain unchanged.", parameters={"type": "object", "properties": {"schedule_id": {"type": "string", "format": "uuid"}, "data": TransferScheduleCreate.model_json_schema(), "apply": _APPLY_FIELD}, "required": ["data"], "additionalProperties": False}, is_proposal=True, tags=["propose", "transactions"])
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


@tool(name="propose_delete_pending_transfer", description=_PROPOSAL_PREFACE + "Remove both legs of a one-off Pending internal transfer. Read list_pending_transfers first. Posted or scheduled pairs are rejected; edit or pause a recurring plan instead. Retrying a completed deletion is safe. No bank money moves.", parameters={"type": "object", "properties": {"transfer_pair_id": {"type": "string", "format": "uuid"}, "apply": _APPLY_FIELD}, "required": ["transfer_pair_id"], "additionalProperties": False}, is_proposal=True, tags=["propose", "transactions"])
async def propose_delete_pending_transfer(*, session, ctx, transfer_pair_id, apply=False):
    ws = await resolve_tool_workspace(session, ctx, write=True)
    applying = _can_apply(ctx, apply)
    ids = await service.delete_pending_pair(session, ws.id, uuid.UUID(transfer_pair_id), apply=applying)
    return {"kind": "delete_transfer", "target": {"id": transfer_pair_id}, "affected": [{"id": key} for key in ids], "affected_count": len(ids), "applied": applying}
