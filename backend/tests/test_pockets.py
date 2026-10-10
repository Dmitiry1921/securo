"""Reproducible pocket accounting and HTTP/MCP authorization checks."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.app_clock import app_today
from app.models.pocket import Pocket, PocketMovement
from app.models.transaction import Transaction
from app.models.workspace import WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.tools.pockets import list_pockets, propose_pocket


async def setup_account(client, headers, *, currency="USD"):
    response = await client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Pocket test",
            "type": "checking",
            "currency": currency,
            "balance": "1000",
        },
    )
    assert response.status_code == 201, response.text
    account_id = response.json()["id"]
    response = await client.post(
        "/api/pockets", headers=headers, json={"account_id": account_id, "name": "Home"}
    )
    assert response.status_code == 201, response.text
    return account_id, response.json()["items"][0]["id"]


async def fund(client, headers, account_id, pocket_id, amount="600", key=None):
    return await client.post(
        "/api/pockets/movements",
        headers=headers,
        json={
            "account_id": account_id,
            "to_pocket_id": pocket_id,
            "amount": amount,
            "idempotency_key": key or str(uuid.uuid4()),
        },
    )


async def state(client, headers, account_id):
    response = await client.get("/api/pockets", headers=headers, params={"account_id": account_id})
    assert response.status_code == 200, response.text
    return response.json()


async def test_allocation_idempotency_and_limits(client, auth_headers):
    account_id, pocket_id = await setup_account(client, auth_headers)
    key = str(uuid.uuid4())
    for _ in range(2):
        response = await fund(client, auth_headers, account_id, pocket_id, key=key)
        assert response.status_code == 200, response.text
        assert Decimal(response.json()["balance"]) == 1000
        assert Decimal(response.json()["unallocated"]) == 400
        assert Decimal(response.json()["items"][0]["balance"]) == 600
    assert (
        await fund(client, auth_headers, account_id, pocket_id, amount="601", key=key)
    ).status_code == 400
    assert (
        await fund(client, auth_headers, account_id, pocket_id, amount="401")
    ).status_code == 400
    assert (
        await client.patch(
            f"/api/pockets/{pocket_id}", headers=auth_headers, json={"archived": True}
        )
    ).status_code == 400


async def test_transaction_lifecycle(client, auth_headers):
    account_id, pocket_id = await setup_account(client, auth_headers)
    await fund(client, auth_headers, account_id, pocket_id)
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account_id,
            "pocket_id": pocket_id,
            "description": "Groceries",
            "amount": "100",
            "date": app_today().isoformat(),
            "type": "debit",
            "status": "pending",
        },
    )
    assert response.status_code == 201, response.text
    tx_id = response.json()["id"]
    data = await state(client, auth_headers, account_id)
    assert Decimal(data["items"][0]["balance"]) == 600
    assert Decimal(data["items"][0]["available"]) == 500
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"status": "posted"}
    )
    assert response.status_code == 200, response.text
    data = await state(client, auth_headers, account_id)
    assert Decimal(data["items"][0]["balance"]) == 500
    assert Decimal(data["items"][0]["reserved"]) == 0
    assert Decimal(data["unallocated"]) == 400
    for changes, expected in [
        ({"amount": "150"}, 450),
        ({"type": "credit"}, 750),
        ({"is_ignored": True}, 600),
    ]:
        response = await client.patch(
            f"/api/transactions/{tx_id}", headers=auth_headers, json=changes
        )
        assert response.status_code == 200, response.text
        assert (
            Decimal((await state(client, auth_headers, account_id))["items"][0]["balance"])
            == expected
        )
    assert (
        await client.delete(f"/api/transactions/{tx_id}", headers=auth_headers)
    ).status_code == 204
    assert Decimal((await state(client, auth_headers, account_id))["items"][0]["balance"]) == 600


async def test_goal_and_transfer(client, auth_headers):
    source, source_pocket = await setup_account(client, auth_headers)
    dest, dest_pocket = await setup_account(client, auth_headers)
    await fund(client, auth_headers, source, source_pocket)
    response = await client.post(
        "/api/goals",
        headers=auth_headers,
        json={
            "name": "Home",
            "target_amount": "2000",
            "currency": "USD",
            "tracking_type": "pockets",
        },
    )
    assert response.status_code == 201, response.text
    goal_id = response.json()["id"]
    assert Decimal(response.json()["current_amount"]) == 0  # unlinked pockets aren't counted
    for pocket_id in (source_pocket, dest_pocket):
        assert (
            await client.patch(
                f"/api/pockets/{pocket_id}", headers=auth_headers, json={"goal_id": goal_id}
            )
        ).status_code == 200
    response = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": source,
            "to_account_id": dest,
            "from_pocket_id": source_pocket,
            "to_pocket_id": dest_pocket,
            "amount": "200",
            "date": app_today().isoformat(),
            "description": "Weekly food",
        },
    )
    assert response.status_code == 201, response.text
    assert Decimal((await state(client, auth_headers, source))["items"][0]["balance"]) == 400
    assert Decimal((await state(client, auth_headers, dest))["items"][0]["balance"]) == 200
    goal = (await client.get(f"/api/goals/{goal_id}", headers=auth_headers)).json()
    assert Decimal(goal["current_amount"]) == 600
    assert goal["percentage"] == 30
    assert (
        await client.patch(
            f"/api/goals/{goal_id}", headers=auth_headers, json={"tracking_type": "manual"}
        )
    ).status_code == 400
    assert (await client.delete(f"/api/goals/{goal_id}", headers=auth_headers)).status_code == 204
    assert Decimal((await state(client, auth_headers, source))["items"][0]["balance"]) == 400


async def test_assignment_scope_history_and_currency(client, auth_headers, session):
    account_id, pocket_id = await setup_account(client, auth_headers)
    other, _ = await setup_account(client, auth_headers)
    for changes in (
        {"account_id": other},
        {"currency": "IDR"},
        {"date": (app_today() - timedelta(days=1)).isoformat()},
    ):
        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": account_id,
                "pocket_id": pocket_id,
                "description": "Invalid",
                "amount": "1",
                "date": app_today().isoformat(),
                "type": "debit",
                **changes,
            },
        )
        assert response.status_code == 400, response.text
    assert not (
        await session.scalars(select(Transaction).where(Transaction.description == "Invalid"))
    ).all()


async def test_missing_fx_and_provider_changes(client, auth_headers, session):
    account_id, pocket_id = await setup_account(client, auth_headers, currency="IDR")
    await fund(client, auth_headers, account_id, pocket_id)
    goal = (
        await client.post(
            "/api/goals",
            headers=auth_headers,
            json={
                "name": "House",
                "target_amount": "1000",
                "currency": "ZZZ",
                "tracking_type": "pockets",
            },
        )
    ).json()
    await client.patch(
        f"/api/pockets/{pocket_id}", headers=auth_headers, json={"goal_id": goal["id"]}
    )
    response = await client.get(f"/api/goals/{goal['id']}", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["valuation_available"] is False
    # Sync can change authoritative data independently of manual validation.
    from app.models.account import Account

    account = await session.get(Account, uuid.UUID(account_id))
    session.add(
        Transaction(
            user_id=account.user_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            pocket_id=uuid.UUID(pocket_id),
            description="Provider edit",
            amount=10,
            currency="EUR",
            date=app_today(),
            effective_date=app_today(),
            type="debit",
            source="sync",
            status="posted",
        )
    )
    await session.commit()
    data = await state(client, auth_headers, account_id)
    assert data["items"][0]["needs_review"] is True


async def test_mcp_preview_apply_and_permissions(
    client, auth_headers, session, test_user, test_workspace, viewer_auth_headers
):
    account_id, pocket_id = await setup_account(client, auth_headers)
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    payload = {
        "account_id": account_id,
        "to_pocket_id": pocket_id,
        "amount": "100",
        "idempotency_key": str(uuid.uuid4()),
    }
    preview = await propose_pocket(session=session, ctx=ctx, operation="move", data=payload)
    assert preview["kind"] == "pocket"
    assert not (await session.scalars(select(PocketMovement))).all()
    result = await propose_pocket(
        session=session, ctx=ctx, operation="move", data=payload, apply=True
    )
    assert result["applied"] is True
    read = await list_pockets(session=session, ctx=ctx, account_id=account_id)
    assert Decimal(read["items"][0]["balance"]) == 100
    assert (await fund(client, viewer_auth_headers, account_id, pocket_id)).status_code == 403
    viewer = (
        await session.scalars(select(WorkspaceMember).where(WorkspaceMember.role == "viewer"))
    ).one()
    with pytest.raises(HTTPException) as error:
        await propose_pocket(
            session=session,
            ctx=CallContext(user_id=viewer.user_id, workspace_id=test_workspace.id, external=True),
            operation="move",
            data=payload,
            apply=True,
        )
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        await list_pockets(
            session=session,
            ctx=CallContext(user_id=test_user.id, workspace_id=uuid.uuid4()),
            account_id=account_id,
        )
    internal = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    preview = await propose_pocket(
        session=session,
        ctx=internal,
        operation="create",
        data={"account_id": account_id, "name": "Preview"},
        apply=True,
    )
    assert "applied" not in preview
    assert not (await session.scalars(select(Pocket).where(Pocket.name == "Preview"))).all()


async def test_internal_move_release_and_export(client, auth_headers, session, test_user, test_workspace):
    account_id, source = await setup_account(client, auth_headers)
    await fund(client, auth_headers, account_id, source)
    response = await client.post("/api/pockets", headers=auth_headers, json={"account_id": account_id, "name": "Travel"})
    destination = next(p["id"] for p in response.json()["items"] if p["name"] == "Travel")
    for from_id, to_id, amount in [(source, destination, "200"), (source, None, "400")]:
        response = await client.post("/api/pockets/movements", headers=auth_headers, json={
            "account_id": account_id, "from_pocket_id": from_id, "to_pocket_id": to_id,
            "amount": amount, "idempotency_key": str(uuid.uuid4()),
        })
        assert response.status_code == 200, response.text
    data = await state(client, auth_headers, account_id)
    assert Decimal(data["balance"]) == 1000
    assert Decimal(data["unallocated"]) == 800
    assert (await client.patch(f"/api/pockets/{source}", headers=auth_headers, json={"archived": True})).status_code == 200
    from app.api.export import _collect
    from app.core.workspace_context import resolve_workspace
    ctx = await resolve_workspace(str(test_workspace.id), test_user, session)
    files = await _collect(ctx, session)
    assert len(files["pockets.json"]) == 2
    assert len(files["pocket_movements.json"]) == 3
    assert len(files["transactions.json"]) == 1  # only the existing opening balance
    assert "goals.json" in files


async def test_unallocated_pending_and_archive_pending_credit(client, auth_headers):
    account_id, pocket_id = await setup_account(client, auth_headers)
    base = {"account_id": account_id, "description": "Pending", "amount": "800", "type": "debit", "date": app_today().isoformat(), "status": "pending"}
    assert (await client.post("/api/transactions", headers=auth_headers, json=base)).status_code == 201
    data = await state(client, auth_headers, account_id)
    assert Decimal(data["unallocated_available"]) == 200
    assert (await fund(client, auth_headers, account_id, pocket_id, amount="201")).status_code == 400
    assert (await client.post("/api/transactions", headers=auth_headers, json={**base, "type": "credit", "pocket_id": pocket_id})).status_code == 201
    assert (await client.patch(f"/api/pockets/{pocket_id}", headers=auth_headers, json={"archived": True})).status_code == 400


async def test_cross_currency_mcp_transfer_and_goal(client, auth_headers, session, test_user, test_workspace):
    from app.models.fx_rate import FxRate
    from mcp_server.tools.pockets import propose_transfer
    from mcp_server.tools.proposals import propose_create_goal
    source, source_pocket = await setup_account(client, auth_headers)
    dest, dest_pocket = await setup_account(client, auth_headers, currency="IDR")
    await fund(client, auth_headers, source, source_pocket)
    session.add(FxRate(base_currency="USD", quote_currency="IDR", date=app_today(), rate=100, source="test"))
    await session.commit()
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    goal = await propose_create_goal(session=session, ctx=ctx, name="House", target_amount=1000, currency="USD", tracking_type="pockets", apply=True)
    for pocket_id in (source_pocket, dest_pocket):
        await client.patch(f"/api/pockets/{pocket_id}", headers=auth_headers, json={"goal_id": goal["id"]})
    payload = {"from_account_id": source, "to_account_id": dest,
        "from_pocket_id": source_pocket, "to_pocket_id": dest_pocket,
        "amount": "100", "date": app_today().isoformat(), "description": "FX transfer"}
    with pytest.raises(ValueError, match="destination amount"):
        await propose_transfer(session=session, ctx=ctx, data=payload, apply=True)
    assert not (await session.scalars(select(Transaction).where(Transaction.description == "FX transfer"))).all()
    result = await propose_transfer(session=session, ctx=ctx, data={**payload, "destination_amount": "10000"}, apply=True)
    assert result["applied"]
    assert Decimal((await state(client, auth_headers, source))["items"][0]["balance"]) == 500
    assert Decimal((await state(client, auth_headers, dest))["items"][0]["balance"]) == 10000
    actual = (await client.get(f"/api/goals/{goal['id']}", headers=auth_headers)).json()
    assert actual["valuation_available"]
    assert Decimal(actual["current_amount"]) == 600
