import uuid
from decimal import Decimal
import pytest
from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate
from app.services import account_service, transaction_service
from app.services.dashboard_service import _account_balance_at
from mcp_server.auth import CallContext
from mcp_server.tools.transaction_updates import propose_update_transaction


async def test_confirm_one_leg_without_replaying_posted_receipt(session, test_user, test_workspace):
    sender, receiver = [
        await account_service.create_account(
            session,
            test_workspace.id,
            test_user.id,
            AccountCreate(name=n, type="checking", currency="USD", balance=1000),
        )
        for n in ["Sender", "Receiver"]
    ]
    pair = uuid.uuid4()
    debit = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=sender.id,
        amount=Decimal(100),
        currency="USD",
        date=app_today(),
        type="debit",
        status="pending",
        source="manual",
        transfer_pair_id=pair,
        description="Imported transfer",
    )
    credit = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=receiver.id,
        amount=Decimal(100),
        currency="USD",
        date=app_today(),
        type="credit",
        status="posted",
        source="manual",
        transfer_pair_id=pair,
        description="Imported transfer",
    )
    session.add_all([debit, credit])
    await session.commit()
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    args = dict(
        session=session,
        ctx=ctx,
        transaction_id=str(debit.id),
        data={"status": "posted"},
        expected_status="pending",
    )
    before = await _account_balance_at(session, receiver, app_today())
    preview = await propose_update_transaction(**args)
    assert not preview["applied"] and debit.status == "pending"
    result = await propose_update_transaction(**args, apply=True)
    assert result["applied"] and debit.status == "posted"
    assert await _account_balance_at(session, sender, app_today()) == 900
    assert await _account_balance_at(session, receiver, app_today()) == before
    retry = await propose_update_transaction(**args, apply=True)
    assert (
        retry["already_applied"] and await _account_balance_at(session, sender, app_today()) == 900
    )
    assert (
        await transaction_service.get_transfer_pair(session, test_workspace.id, debit.id)
    ).id == credit.id
    with pytest.raises(ValueError):
        await propose_update_transaction(
            **{**args, "data": {"status": "pending"}, "expected_status": "pending"}, apply=True
        )


async def test_preview_validation_scope_and_internal_apply(session, test_user, test_workspace):
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    with pytest.raises(ValueError):
        await propose_update_transaction(
            session=session, ctx=ctx, transaction_id=str(uuid.uuid4()), data={"amount": 10}
        )
    with pytest.raises(ValueError):
        await propose_update_transaction(
            session=session, ctx=ctx, transaction_id=str(uuid.uuid4()), data={"status": None}
        )
    with pytest.raises(ValueError):
        await propose_update_transaction(
            session=session,
            ctx=ctx,
            transaction_id=str(uuid.uuid4()),
            data={"status": "posted"},
            apply=True,
        )
