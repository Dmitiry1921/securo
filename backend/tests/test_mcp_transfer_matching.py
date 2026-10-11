"""Reconciliation uses native pairs without duplicating imported bank rows."""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.models.workspace import WorkspaceMember
from app.schemas.account import AccountCreate
from app.schemas.pocket import PocketCreate, PocketMove
from app.services import account_service, pocket_service
from mcp_server.auth import CallContext
from mcp_server.tools import transfer_matching as tools
from mcp_server.tools.pockets import propose_assign_pocket


@pytest_asyncio.fixture
async def transfer(session, test_user, test_workspace):
    accounts = [await account_service.create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name=name, type="checking", currency="IDR", balance=1000),
    ) for name in ("Sender", "Receiver")]
    credit = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=accounts[1].id,
        amount=Decimal("100"), currency="IDR", date=app_today(),
        type="credit", status="posted", description="Imported transfer", source="import",
    )
    session.add(credit)
    await session.commit()
    return accounts, credit, CallContext(
        user_id=test_user.id, workspace_id=test_workspace.id, external=True,
    )


async def counterpart(session, transfer, **kwargs):
    accounts, credit, ctx = transfer
    return await tools.propose_transfer_counterpart(
        session=session, ctx=ctx, transaction_id=str(credit.id),
        counterpart_account_id=str(accounts[0].id), **kwargs,
    )


@pytest.mark.asyncio
async def test_counterpart_preview_apply_retry_and_pockets(session, transfer):
    accounts, credit, ctx = transfer
    state = await pocket_service.create_pocket(
        session, ctx.workspace_id, ctx.user_id,
        PocketCreate(account_id=accounts[0].id, name="Food"),
    )
    pocket_id = state.items[0].id
    await pocket_service.move(session, ctx.workspace_id, ctx.user_id, PocketMove(
        account_id=accounts[0].id, to_pocket_id=pocket_id,
        amount=Decimal("1000"), idempotency_key=uuid.uuid4(),
    ))
    assert (await counterpart(session, transfer))["applied"] is False
    assert await session.scalar(select(func.count(Transaction.id))) == 3
    result = await counterpart(session, transfer, apply=True)
    assert result["applied"] is True
    debit_id = next(value for value in result["transaction_ids"] if value != str(credit.id))
    debit = await session.get(Transaction, uuid.UUID(debit_id))
    assert debit.transfer_pair_id == credit.transfer_pair_id
    assert (debit.amount, debit.type, debit.status) == (Decimal("100"), "debit", "posted")
    await propose_assign_pocket(
        session=session, ctx=ctx, transaction_id=debit_id, pocket_id=str(pocket_id), apply=True,
    )
    pockets = await pocket_service.get_account_pockets(session, ctx.workspace_id, accounts[0].id)
    assert pockets.items[0].balance == Decimal("900")
    assert (await counterpart(session, transfer, apply=True))["transfer_pair_id"] == result["transfer_pair_id"]
    assert await session.scalar(select(func.count(Transaction.id))) == 4


@pytest.mark.asyncio
async def test_counterpart_keeps_pending_status(session, transfer):
    transfer[1].status = "pending"
    await session.commit()
    result = await counterpart(session, transfer, apply=True)
    statuses = list((await session.scalars(select(Transaction.status).where(
        Transaction.transfer_pair_id == uuid.UUID(result["transfer_pair_id"]),
    ))).all())
    assert statuses == ["pending", "pending"]


@pytest.mark.asyncio
async def test_link_existing_preserves_balances_and_distinct_statuses(session, transfer):
    accounts, credit, ctx = transfer
    debit = Transaction(
        user_id=ctx.user_id, workspace_id=ctx.workspace_id, account_id=accounts[0].id,
        amount=Decimal("100"), currency="IDR", date=app_today(),
        type="debit", status="pending", description="Bank settlement", source="import",
    )
    session.add(debit)
    await session.commit()
    with pytest.raises(ValueError, match="already exists"):
        await counterpart(session, transfer, apply=True)
    args = dict(session=session, ctx=ctx, transaction_ids=[str(debit.id), str(credit.id)])
    balances_before = [
        (await account_service.get_account_summary(session, account.id, ctx.workspace_id))["current_balance"]
        for account in accounts
    ]
    assert (await tools.propose_link_transfer(**args))["applied"] is False
    assert debit.transfer_pair_id is None and credit.transfer_pair_id is None
    result = await tools.propose_link_transfer(**args, apply=True)
    assert debit.transfer_pair_id == credit.transfer_pair_id
    assert (debit.status, credit.status) == ("pending", "posted")
    assert (await tools.propose_link_transfer(**args, apply=True))["transfer_pair_id"] == result["transfer_pair_id"]
    assert await session.scalar(select(func.count(Transaction.id))) == 4
    assert balances_before == [
        (await account_service.get_account_summary(session, account.id, ctx.workspace_id))["current_balance"]
        for account in accounts
    ]


@pytest.mark.asyncio
async def test_internal_client_cannot_apply(session, transfer):
    transfer[2].external = False
    assert (await counterpart(session, transfer, apply=True))["applied"] is False
    assert await session.scalar(select(func.count(Transaction.id))) == 3


@pytest.mark.asyncio
async def test_viewer_cannot_apply(session, transfer):
    member = await session.scalar(select(WorkspaceMember))
    member.role = "viewer"
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await counterpart(session, transfer, apply=True)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cross_workspace_transaction_rejected(session, transfer):
    with pytest.raises(ValueError, match="Transaction not found"):
        await tools.propose_link_transfer(
            session=session, ctx=transfer[2],
            transaction_ids=[str(transfer[1].id), str(uuid.uuid4())], apply=True,
        )


@pytest.mark.asyncio
async def test_cross_currency_counterpart_requires_actual_bank_leg(session, transfer):
    transfer[0][0].currency = "USD"
    await session.commit()
    with pytest.raises(ValueError, match="cross-currency"):
        await counterpart(session, transfer, apply=True)
