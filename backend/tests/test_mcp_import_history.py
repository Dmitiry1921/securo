from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.models.workspace import WorkspaceMember
from app.schemas.account import AccountCreate
from app.schemas.pocket import PocketCreate, PocketMove
from app.services import account_service, pocket_service
from mcp_server.auth import CallContext
from mcp_server.tools import imports


@pytest_asyncio.fixture
async def history(session, test_user, test_workspace):
    account = await account_service.create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Statement account", type="checking", currency="IDR", balance=100),
    )
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    rows = [
        {"description": "Incoming transfer", "amount": "70.00", "type": "credit",
         "date": str(app_today() - timedelta(days=2)), "external_id": "statement-1"},
        {"description": "Purchase", "amount": "30.00", "type": "debit",
         "date": str(app_today() - timedelta(days=1)), "external_id": "statement-2"},
    ]
    return account, ctx, rows


async def _call(session, history, **kwargs):
    account, ctx, rows = history
    args = dict(session=session, ctx=ctx, account_id=str(account.id),
                expected_balance="100.00", transactions=rows)
    args.update(kwargs)
    return await imports.propose_import_history(**args)


@pytest.mark.asyncio
async def test_history_preview_is_read_only(session, history):
    preview = await _call(session, history)
    assert preview["applied"] is False
    assert await session.scalar(select(func.count(Transaction.id))) == 1
    history[1].external = False
    assert (await _call(session, history, apply=True))["applied"] is False


@pytest.mark.asyncio
async def test_history_uses_explicit_confirmed_closing_balance(session, history):
    result = await _call(session, history, closing_balance="170.00", apply=True)
    assert Decimal(result["balance_after"]) == Decimal("170")
    assert Decimal(result["inferred_opening_balance"]) == Decimal("130")
    assert result["preserves_current_balance"] is False


@pytest.mark.asyncio
async def test_history_preserves_balance_pockets_and_retries(session, history):
    account, ctx, _ = history
    state = await pocket_service.create_pocket(
        session, ctx.workspace_id, ctx.user_id,
        PocketCreate(account_id=account.id, name="Savings"),
    )
    await pocket_service.move(
        session, ctx.workspace_id, ctx.user_id,
        PocketMove(account_id=account.id, to_pocket_id=state.items[0].id,
                   amount=Decimal("25.00"), idempotency_key=uuid.uuid4()),
    )
    result = await _call(session, history, apply=True)
    assert result["imported"] == 2
    assert Decimal(result["balance_after"]) == Decimal("100")
    assert Decimal(result["inferred_opening_balance"]) == Decimal("60")
    pockets = await pocket_service.get_account_pockets(session, ctx.workspace_id, account.id)
    assert pockets.items[0].balance == Decimal("25")
    assert pockets.unallocated == Decimal("75")
    retry = await _call(session, history, apply=True)
    assert (retry["imported"], retry["skipped"]) == (0, 2)
    assert Decimal(retry["balance_after"]) == Decimal("100")
    assert await session.scalar(select(func.count(Transaction.id))) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["stale", "future", "currency", "amount", "category"])
async def test_history_rejects_invalid_input(session, history, invalid):
    kwargs = {}
    row = history[2][0]
    if invalid == "stale":
        kwargs["expected_balance"] = "99"
    elif invalid == "future":
        row["date"] = str(app_today() + timedelta(days=1))
    elif invalid == "currency":
        row["currency"] = "USD"
    elif invalid == "amount":
        row["amount"] = "0.001"
    elif invalid == "category":
        row["category_id"] = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValueError):
        await _call(session, history, apply=True, **kwargs)
    assert await session.scalar(select(func.count(Transaction.id))) == 1


@pytest.mark.asyncio
async def test_history_failure_rolls_back_rows_and_opening(session, history):
    real_balance = imports._balance
    calls = 0

    async def fail_final_check(*args):
        nonlocal calls
        calls += 1
        return Decimal("999") if calls == 3 else await real_balance(*args)

    with patch.object(imports, "_balance", new=fail_final_check), \
            pytest.raises(ValueError, match="rolled back"):
        await _call(session, history, apply=True)
    assert await session.scalar(select(func.count(Transaction.id))) == 1
    opening = await session.scalar(select(Transaction))
    assert opening.amount == Decimal("100")


@pytest.mark.asyncio
async def test_history_viewer_cannot_apply(session, history):
    member = await session.scalar(select(WorkspaceMember))
    member.role = "viewer"
    await session.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await _call(session, history, apply=True)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_history_pocket_changes_roll_back(session, history):
    from types import SimpleNamespace
    before = SimpleNamespace(items=[SimpleNamespace(id="pocket", balance=Decimal("25"))])
    after = SimpleNamespace(items=[SimpleNamespace(id="pocket", balance=Decimal("20"))])
    with patch.object(pocket_service, "get_account_pockets", new=AsyncMock(side_effect=[before, after])), \
            pytest.raises(ValueError, match="pocket allocations"):
        await _call(session, history, apply=True)
    assert await session.scalar(select(func.count(Transaction.id))) == 1
