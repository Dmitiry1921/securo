import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.schemas.forecast import (
    ExpenseForecastRule,
    ForecastRequest,
    ForecastScenario,
    FundingForecast,
)
from app.services import forecast_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/rules", response_model=list[ExpenseForecastRule])
async def rules(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.list_settings(session, ctx.id, "expense_rule")


@router.get("/scenarios", response_model=list[ForecastScenario])
async def scenarios(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.list_settings(session, ctx.id, "scenario")


@router.post("/calculate", response_model=FundingForecast)
async def calculate(
    payload: ForecastRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await forecast_service.get_forecast(session, ctx.id, ctx.user_id, payload)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.put("/rules/{setting_id}", response_model=ExpenseForecastRule)
async def save_rule(
    setting_id: uuid.UUID,
    payload: ExpenseForecastRule,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if setting_id != payload.id:
        raise HTTPException(422, detail="Path and payload IDs differ")
    try:
        return await forecast_service.save_setting(
            session, ctx.id, "expense_rule", payload.model_dump()
        )
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.put("/scenarios/{setting_id}", response_model=ForecastScenario)
async def save_scenario(
    setting_id: uuid.UUID,
    payload: ForecastScenario,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if setting_id != payload.id:
        raise HTTPException(422, detail="Path and payload IDs differ")
    try:
        return await forecast_service.save_setting(
            session, ctx.id, "scenario", payload.model_dump()
        )
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error


@router.delete("/{kind}/{setting_id}", status_code=204)
async def remove(
    kind: str,
    setting_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if kind not in ("expense_rule", "scenario"):
        raise HTTPException(422, detail="Unknown setting kind")
    try:
        await forecast_service.delete_setting(session, ctx.id, kind, setting_id, apply=True)
    except ValueError as error:
        raise HTTPException(404, detail=str(error)) from error
