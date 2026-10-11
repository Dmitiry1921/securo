import uuid
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.schemas.transfer_schedule import TransferScheduleCreate, TransferScheduleRead, TransferConfirm, TransferPendingUpdate
from app.services import transfer_schedule_service as service

router = APIRouter(prefix="/api/transfer-schedules", tags=["transfers"])


@router.get("", response_model=list[TransferScheduleRead])
async def list_schedules(ctx: WorkspaceContext = Depends(current_workspace), session: AsyncSession = Depends(get_async_session)):
    return await service.list_schedules(session, ctx.workspace.id)


@router.post("", response_model=TransferScheduleRead, status_code=201)
async def create(data: TransferScheduleCreate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        return await service.save_schedule(session, ctx.workspace.id, ctx.user_id, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.put("/{schedule_id}", response_model=TransferScheduleRead)
async def update(schedule_id: uuid.UUID, data: TransferScheduleCreate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        return await service.save_schedule(session, ctx.workspace.id, ctx.user_id, data, schedule_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class OccurrenceRequest(BaseModel):
    date: date


@router.post("/{schedule_id}/materialize")
async def materialize(schedule_id: uuid.UUID, data: OccurrenceRequest, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        pair = await service.materialize(session, ctx.workspace.id, schedule_id, data.date)
        return {"transaction_ids": [str(tx.id) for tx in pair], "transfer_pair_id": str(pair[0].transfer_pair_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/pairs/{pair_id}/confirm")
async def confirm(pair_id: uuid.UUID, data: TransferConfirm, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        pair = await service.confirm(session, ctx.workspace.id, ctx.user_id, pair_id, data)
        return {"transaction_ids": [str(tx.id) for tx in pair], "transfer_pair_id": str(pair_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/pending")
async def pending(ctx: WorkspaceContext = Depends(current_workspace), session: AsyncSession = Depends(get_async_session)):
    return await service.pending_pairs(session, ctx.workspace.id)


@router.patch("/pairs/{pair_id}")
async def update_pending(pair_id: uuid.UUID, data: TransferPendingUpdate, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        pair = await service.update_pending(session, ctx.workspace.id, ctx.user_id, pair_id, data)
        return {"transaction_ids": [str(tx.id) for tx in pair], "transfer_pair_id": str(pair_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/pairs/{pair_id}")
async def delete_pending_pair(pair_id: uuid.UUID, ctx: WorkspaceContext = Depends(current_writable_workspace), session: AsyncSession = Depends(get_async_session)):
    try:
        return {"transaction_ids": await service.delete_pending_pair(session, ctx.workspace.id, pair_id, apply=True)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
