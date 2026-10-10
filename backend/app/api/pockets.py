import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.pocket import (PocketAccountRead, PocketCreate, PocketMove, PocketUpdate,
    PocketIncomeRuleCreate, PocketIncomeRuleRead, PocketIncomeRuleStop)
from app.services import pocket_service

router = APIRouter(prefix="/api/pockets", tags=["pockets"])


async def _result(operation):
    try:
        return await operation
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/income-rules", response_model=PocketIncomeRuleRead, status_code=201)
async def create_income_rule(data: PocketIncomeRuleCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session)):
    return await _result(pocket_service.create_income_rule(session, ctx.id, data))


@router.patch("/income-rules/{rule_id}", response_model=PocketIncomeRuleRead)
async def stop_income_rule(rule_id: uuid.UUID, data: PocketIncomeRuleStop,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session)):
    return await _result(pocket_service.stop_income_rule(session, ctx.id, rule_id, data.end_date))


@router.get("", response_model=PocketAccountRead)
async def list_pockets(
    account_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await _result(pocket_service.get_account_pockets(session, ctx.id, account_id))


@router.post("", response_model=PocketAccountRead, status_code=201)
async def create_pocket(
    data: PocketCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await _result(pocket_service.create_pocket(session, ctx.id, ctx.user_id, data))


@router.patch("/{pocket_id}", response_model=PocketAccountRead)
async def update_pocket(
    pocket_id: uuid.UUID,
    data: PocketUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await _result(pocket_service.update_pocket(session, ctx.id, pocket_id, data))


@router.post("/movements", response_model=PocketAccountRead)
async def move(
    data: PocketMove,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await _result(pocket_service.move(session, ctx.id, ctx.user_id, data))
