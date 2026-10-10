"""Goal cycles must preserve ledger balances and never duplicate a payment."""

from decimal import Decimal
from datetime import date

from tests.test_pockets import setup_account, fund, state
from app.models.goal import Goal
from app.services.goal_service import _next_target_date


async def setup_goal(client, headers):
    account, pocket = await setup_account(client, headers)
    assert (await fund(client, headers, account, pocket, amount="600")).status_code == 200
    response = await client.post(
        "/api/goals",
        headers=headers,
        json={
            "name": "School",
            "target_amount": 500,
            "currency": "USD",
            "tracking_type": "pockets",
            "repeat_frequency": "quarterly",
            "target_date": "2026-11-01",
        },
    )
    assert response.status_code == 201, response.text
    goal = response.json()["id"]
    assert (
        await client.patch(f"/api/pockets/{pocket}", headers=headers, json={"goal_id": goal})
    ).status_code == 200
    return account, pocket, goal


async def test_payment_completion_restart_and_retry(client, auth_headers):
    account, pocket, goal = await setup_goal(client, auth_headers)
    from app.core.app_clock import app_today

    data = {
        "cycle_number": 1,
        "payment": {"pocket_id": pocket, "amount": 500, "date": str(app_today())},
    }
    response = await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert Decimal(response.json()["current_amount"]) == 600
    after = await state(client, auth_headers, account)
    assert Decimal(after["balance"]) == 500
    assert Decimal(after["items"][0]["balance"]) == 100
    assert (
        await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json=data)
    ).status_code == 400
    response = await client.post(
        f"/api/goals/{goal}/restart", headers=auth_headers, json={"cycle_number": 1}
    )
    assert response.status_code == 200, response.text
    assert response.json()["cycle_number"] == 2
    assert response.json()["target_date"] == "2027-02-01"
    assert Decimal(response.json()["current_amount"]) == 100
    assert (
        await client.post(
            f"/api/goals/{goal}/restart", headers=auth_headers, json={"cycle_number": 1}
        )
    ).status_code == 400
    assert (
        await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json=data)
    ).status_code == 400
    history = (await client.get(f"/api/goals/{goal}/history", headers=auth_headers)).json()
    assert len(history) == 1
    assert history[0]["transaction_id"]
    import io
    import json
    import zipfile

    backup = await client.get("/api/export/backup", headers=auth_headers)
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        exported = json.loads(archive.read("goal_completions.json"))
    assert exported[0]["transaction_id"] == history[0]["transaction_id"]
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 500


async def test_insufficient_funds_and_foreign_pocket_leave_cycle_untouched(client, auth_headers):
    account, pocket, goal = await setup_goal(client, auth_headers)
    from app.core.app_clock import app_today

    for amount in (601, -1):
        response = await client.post(
            f"/api/goals/{goal}/complete",
            headers=auth_headers,
            json={
                "cycle_number": 1,
                "payment": {"pocket_id": pocket, "amount": amount, "date": str(app_today())},
            },
        )
        assert response.status_code in (400, 422)
    _, other_pocket = await setup_account(client, auth_headers)
    response = await client.post(
        f"/api/goals/{goal}/complete",
        headers=auth_headers,
        json={
            "cycle_number": 1,
            "payment": {"pocket_id": other_pocket, "amount": 1, "date": str(app_today())},
        },
    )
    assert response.status_code == 400
    assert (await client.get(f"/api/goals/{goal}", headers=auth_headers)).json()[
        "status"
    ] == "active"
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 1000
    assert (await client.get(f"/api/goals/{goal}/history", headers=auth_headers)).json() == []


async def test_existing_expense_is_not_charged_twice(client, auth_headers):
    account, pocket, goal = await setup_goal(client, auth_headers)
    from app.core.app_clock import app_today

    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "School",
            "amount": 500,
            "currency": "USD",
            "type": "debit",
            "date": str(app_today()),
            "account_id": account,
            "pocket_id": pocket,
        },
    )
    assert response.status_code == 201, response.text
    tx = response.json()["id"]
    response = await client.post(
        f"/api/goals/{goal}/complete",
        headers=auth_headers,
        json={"cycle_number": 1, "transaction_id": tx},
    )
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["current_amount"]) == 600
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 500
    funding = (await client.get(f"/api/goals/{goal}/funding", headers=auth_headers)).json()
    assert funding["payments"] == []


async def test_manual_cycle_requires_date_and_resets_progress(client, auth_headers):
    payload = {
        "name": "Manual",
        "target_amount": 100,
        "current_amount": 100,
        "repeat_frequency": "monthly",
    }
    assert (await client.post("/api/goals", headers=auth_headers, json=payload)).status_code == 400
    payload["target_date"] = "2027-01-31"
    response = await client.post("/api/goals", headers=auth_headers, json=payload)
    goal = response.json()["id"]
    assert (
        await client.patch(f"/api/goals/{goal}", headers=auth_headers, json={"status": "completed"})
    ).status_code == 400
    assert (
        await client.post(
            f"/api/goals/{goal}/complete", headers=auth_headers, json={"cycle_number": 1}
        )
    ).status_code == 200
    response = await client.post(
        f"/api/goals/{goal}/restart", headers=auth_headers, json={"cycle_number": 1}
    )
    assert response.json()["target_date"] == "2027-02-28"
    assert Decimal(response.json()["current_amount"]) == 0


def test_month_end_anchor_and_leap_year():
    goal = Goal(target_date=date(2027, 1, 31), repeat_frequency="monthly", repeat_anchor_day=31)
    goal.target_date = _next_target_date(goal)
    assert goal.target_date == date(2027, 2, 28)
    assert _next_target_date(goal) == date(2027, 3, 31)


async def test_mcp_lifecycle_preview_and_viewer_permissions(
    client,
    auth_headers,
    session,
    test_user,
    test_workspace,
    viewer_auth_headers,
):
    import pytest
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.workspace import WorkspaceMember
    from mcp_server.auth import CallContext
    from mcp_server.tools.goals import (
        propose_complete_goal,
        propose_restart_goal,
        propose_update_goal,
        get_goal_history,
    )

    _, _, goal = await setup_goal(client, auth_headers)
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    preview = await propose_complete_goal(
        session=session, ctx=ctx, goal_id=goal, data={"cycle_number": 1}
    )
    assert "applied" not in preview
    assert (await get_goal_history(session=session, ctx=ctx, goal_id=goal))["total"] == 0
    changed = await propose_update_goal(
        session=session, ctx=ctx, goal_id=goal, data={"repeat_frequency": "monthly"}, apply=True
    )
    assert changed["result"]["repeat_frequency"] == "monthly"
    applied = await propose_complete_goal(
        session=session, ctx=ctx, goal_id=goal, data={"cycle_number": 1}, apply=True
    )
    assert applied["result"]["status"] == "completed"
    restarted = await propose_restart_goal(
        session=session, ctx=ctx, goal_id=goal, data={"cycle_number": 1}, apply=True
    )
    assert restarted["result"]["cycle_number"] == 2
    assert (
        await client.post(
            f"/api/goals/{goal}/complete", headers=viewer_auth_headers, json={"cycle_number": 2}
        )
    ).status_code == 403
    viewer = (
        await session.scalars(select(WorkspaceMember).where(WorkspaceMember.role == "viewer"))
    ).one()
    with pytest.raises(HTTPException) as error:
        await propose_complete_goal(
            session=session,
            ctx=CallContext(user_id=viewer.user_id, workspace_id=test_workspace.id, external=True),
            goal_id=goal,
            data={"cycle_number": 2},
            apply=True,
        )
    assert error.value.status_code == 403


async def test_failed_expense_rolls_back_and_other_workspace_cannot_complete(
    client, auth_headers, session, test_workspace
):
    import uuid
    from unittest.mock import patch
    from app.services import goal_service
    from app.schemas.goal import GoalComplete
    from app.core.app_clock import app_today
    import pytest

    account, pocket, goal = await setup_goal(client, auth_headers)
    data = {
        "cycle_number": 1,
        "payment": {"pocket_id": pocket, "amount": 500, "date": str(app_today())},
    }
    with patch(
        "app.services.transaction_service.reconciliation_service.match_incoming",
        side_effect=ValueError("Payment failed"),
    ):
        response = await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json=data)
    assert response.status_code == 400
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 1000
    assert (await client.get(f"/api/goals/{goal}", headers=auth_headers)).json()[
        "status"
    ] == "active"
    with pytest.raises(ValueError, match="Goal not found"):
        await goal_service.complete_goal(
            session, uuid.uuid4(), uuid.uuid4(), uuid.UUID(goal), GoalComplete(cycle_number=1)
        )


async def test_goal_currency_does_not_change_native_payment_amount(client, auth_headers, session):
    from app.core.app_clock import app_today
    from app.models.fx_rate import FxRate

    account, pocket, goal = await setup_goal(client, auth_headers)
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="IDR",
            date=app_today(),
            rate=Decimal("10000"),
            source="test",
        )
    )
    await session.commit()
    response = await client.patch(
        f"/api/goals/{goal}",
        headers=auth_headers,
        json={"currency": "IDR", "target_amount": 5000000},
    )
    assert response.status_code == 200
    response = await client.post(
        f"/api/goals/{goal}/complete",
        headers=auth_headers,
        json={
            "cycle_number": 1,
            "payment": {"pocket_id": pocket, "amount": 500, "date": str(app_today())},
        },
    )
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["current_amount"]) == 6000000
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 500
    response = await client.post(
        f"/api/goals/{goal}/restart", headers=auth_headers, json={"cycle_number": 1}
    )
    assert Decimal(response.json()["current_amount"]) == 1000000


async def test_missing_fx_cannot_complete_or_spend(client, auth_headers):
    from app.core.app_clock import app_today

    account, pocket, goal = await setup_goal(client, auth_headers)
    response = await client.patch(
        f"/api/goals/{goal}", headers=auth_headers, json={"currency": "JPY"}
    )
    assert response.status_code == 200
    response = await client.post(
        f"/api/goals/{goal}/complete",
        headers=auth_headers,
        json={
            "cycle_number": 1,
            "payment": {"pocket_id": pocket, "amount": 500, "date": str(app_today())},
        },
    )
    assert response.status_code == 400
    assert Decimal((await state(client, auth_headers, account))["balance"]) == 1000


async def test_editing_name_does_not_lose_month_end_anchor(client, auth_headers):
    response = await client.post(
        "/api/goals",
        headers=auth_headers,
        json={
            "name": "Monthly",
            "target_amount": 100,
            "repeat_frequency": "monthly",
            "target_date": "2027-01-31",
        },
    )
    goal = response.json()["id"]
    await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json={"cycle_number": 1})
    await client.post(f"/api/goals/{goal}/restart", headers=auth_headers, json={"cycle_number": 1})
    response = await client.patch(
        f"/api/goals/{goal}",
        headers=auth_headers,
        json={"name": "Renamed", "target_date": "2027-02-28"},
    )
    assert response.json()["next_target_date"] == "2027-03-31"


async def test_completed_snapshot_keeps_currency_when_goal_is_edited(client, auth_headers, session):
    from app.core.app_clock import app_today
    from app.models.fx_rate import FxRate

    _, _, goal = await setup_goal(client, auth_headers)
    await client.post(f"/api/goals/{goal}/complete", headers=auth_headers, json={"cycle_number": 1})
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="IDR",
            date=app_today(),
            rate=Decimal("10000"),
            source="test",
        )
    )
    await session.commit()
    response = await client.patch(
        f"/api/goals/{goal}",
        headers=auth_headers,
        json={"currency": "IDR", "target_amount": 5000000},
    )
    assert Decimal(response.json()["current_amount"]) == 6000000
    assert response.json()["last_completion"]["currency"] == "USD"
    assert Decimal(response.json()["last_completion"]["saved_amount"]) == 600
