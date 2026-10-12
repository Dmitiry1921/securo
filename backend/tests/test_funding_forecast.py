import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from app.schemas.report import MoneyMapFlow
from app.schemas.forecast import ExpenseForecastRule, ForecastScenario
from app.services.forecast_service import cycle_start, simulate


def account(id="a", balance="100", currency="USD", pockets=None):
    return SimpleNamespace(
        account_id=id, currency=currency, balance=Decimal(balance), items=pockets or []
    )


def pocket(id="p", balance="0", goal="g"):
    return SimpleNamespace(id=id, balance=Decimal(balance), goal_id=goal)


def goal(id="g", amount="100", deadline=date(2026, 2, 5)):
    return SimpleNamespace(
        id=id,
        name="Goal",
        currency="USD",
        target_amount=Decimal(amount),
        target_date=deadline,
        repeat_frequency=None,
    )


def flow(id="f", kind="expense", amount=10, source="a", dest=None, on="2026-02-05", **kw):
    return MoneyMapFlow(
        id=id,
        date=on,
        kind=kind,
        status="projected",
        description=id,
        amount=amount,
        currency="USD",
        from_account_id=source,
        to_account_id=dest,
        **kw,
    )


def run(accounts, flows, goals=None, rates=None, start=date(2026, 2, 5), end=date(2026, 2, 5)):
    return simulate(accounts, flows, goals or [], rates or {"USD": Decimal(1)}, start, end)


def test_internal_transfer_preserves_wealth():
    t, e, g, w = run(
        [account("a"), account("b", "0")], [flow(kind="transfer", source="a", dest="b", amount=30)]
    )
    assert t[0]["balance"] == "100.00"
    assert t[0]["accounts"] == {"a": "70.00", "b": "30.00"}


def test_allocation_cannot_fund_two_goals_with_same_money():
    a = account(pockets=[pocket("p", goal="g"), pocket("q", goal="h")])
    t, e, g, w = run(
        [a],
        [
            flow(id="one", kind="allocation", amount=70, source="a", dest="a", to_pocket_id="p"),
            flow(id="two", kind="allocation", amount=70, source="a", dest="a", to_pocket_id="q"),
        ],
        [goal("g", "70"), goal("h", "70")],
    )
    assert t[0]["balance"] == "100.00"
    assert t[0]["pockets"] == {"p": "70.00", "q": "30.00"}
    assert e[1]["shortfall"] == "40.00"
    assert g[1].shortfall == 40


def test_posted_is_not_replayed_and_reading_is_pure():
    a = account()
    f = flow()
    f.status = "posted"
    t, *_ = run([a], [f])
    assert t[0]["balance"] == "100.00" and a.balance == 100


def test_missing_fx_is_unknown_not_zero():
    a = account(currency="IDR", pockets=[pocket(balance="100")])
    t, e, g, w = run([a], [], [goal()], {"USD": Decimal(1), "IDR": None})
    assert t[0]["balance"] is None and g[0].status == "unknown"


def test_pocket_transfer_and_payment_is_one_expense():
    a = account("a", "100", pockets=[pocket("p", "100")])
    b = account("b", "0", pockets=[pocket("q", "0")])
    t, e, g, w = run(
        [a, b],
        [
            flow(
                kind="transfer",
                amount=100,
                source="a",
                dest="b",
                from_pocket_id="p",
                to_pocket_id="q",
            ),
            flow(id="pay", amount=100, source="b", from_pocket_id="q"),
        ],
        [goal()],
    )
    assert t[0]["balance"] == "0.00" and g[0].status == "funded"


def test_unfunded_transfer_does_not_create_funded_goal():
    a = account("a", "0")
    b = account("b", "0", pockets=[pocket()])
    t, e, g, w = run(
        [a, b],
        [flow(kind="transfer", amount=100, source="a", dest="b", to_pocket_id="p")],
        [goal()],
    )
    assert t[0]["balance"] == "0.00" and g[0].status == "shortfall"
    assert g[0].shortfall == 100
    assert t[0]["pockets"]["p"] == "0.00"
    assert any(x.code == "account_deficit" for x in w)


def test_protected_pockets_not_automatically_consumed():
    a = account("a", "100", pockets=[pocket("p", "100")])
    t, e, g, w = run([a], [flow(amount=50)], [goal()])
    assert t[0]["pockets"]["p"] == "100.00" and g[0].status == "unknown"
    assert any(x.code == "protected_funds_required" for x in w)


def test_same_day_income_dependency_visible():
    t, e, g, w = run(
        [account(balance="0")],
        [flow(kind="income", amount=100, source=None, dest="a"), flow(id="pay", amount=50)],
    )
    assert t[0]["balance"] == "50.00" and any(x.code == "same_day_income_dependency" for x in w)


@pytest.mark.parametrize(
    "on,day,expected",
    [
        (date(2024, 2, 29), 31, date(2024, 2, 29)),
        (date(2023, 2, 28), 31, date(2023, 2, 28)),
        (date(2026, 2, 4), 5, date(2026, 1, 5)),
        (date(2026, 2, 5), 5, date(2026, 2, 5)),
    ],
)
def test_cycles(on, day, expected):
    assert cycle_start(on, day) == expected


def test_schema_rejects_missing_source_ambiguous_categories_and_bad_rate():
    base = dict(
        id=uuid.uuid4(),
        name="Food",
        account_id=uuid.uuid4(),
        currency="USD",
        start_date="2026-01-01",
        category_ids=[uuid.uuid4()],
    )
    with pytest.raises(ValueError):
        ExpenseForecastRule.model_validate(base)
    with pytest.raises(ValueError):
        ForecastScenario(id=uuid.uuid4(), name="Bad", fx_rates={"IDR": "0"})


async def test_http_mcp_readonly_and_period_remainder(
    client, auth_headers, test_user, test_workspace, session
):
    from app.core.app_clock import app_today
    from app.models.transaction import Transaction
    from app.models.forecast import ForecastSetting
    from sqlalchemy import select, func
    from mcp_server.auth import CallContext
    from mcp_server.tools.forecast import get_funding_forecast, propose_forecast_setting

    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()
    today = app_today()
    ac = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": "Food", "type": "checking", "currency": "USD", "balance": "1000"},
    )
    assert ac.status_code == 201, ac.text
    aid = ac.json()["id"]
    cat = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Food forecast", "icon": "utensils", "color": "#F59E0B"},
    )
    assert cat.status_code == 201, cat.text
    cid = cat.json()["id"]
    rid = str(uuid.uuid4())
    payload = dict(
        id=rid,
        name="Food forecast",
        account_id=aid,
        amount="300",
        currency="USD",
        category_ids=[cid],
        cycle_day=5,
        start_date=today.isoformat(),
    )
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    preview = await propose_forecast_setting(
        session=session, ctx=ctx, kind="expense_rule", operation="save", data=payload
    )
    assert not preview["applied"]
    assert await session.scalar(select(func.count()).select_from(ForecastSetting)) == 0
    saved = await client.put(f"/api/forecast/rules/{rid}", headers=auth_headers, json=payload)
    assert saved.status_code == 200, saved.text
    again = await client.put(f"/api/forecast/rules/{rid}", headers=auth_headers, json=payload)
    assert again.status_code == 200
    assert await session.scalar(select(func.count()).select_from(ForecastSetting)) == 1
    tx = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": aid,
            "description": "Groceries",
            "amount": "60",
            "currency": "USD",
            "date": today.isoformat(),
            "type": "debit",
            "category_id": cid,
        },
    )
    assert tx.status_code == 201, tx.text
    before = await session.scalar(select(func.count()).select_from(Transaction))
    request = {"months": 2}
    response = await client.post("/api/forecast/calculate", headers=auth_headers, json=request)
    assert response.status_code == 200, response.text
    data = response.json()
    assert Decimal(data["budget_periods"][0]["actual"]) == 60
    assert Decimal(data["budget_periods"][0]["forecast"]) == 240
    assert all(Decimal(e["amount"]) >= 0 for e in data["events"])
    assert before == await session.scalar(select(func.count()).select_from(Transaction))
    mcp = await get_funding_forecast(session=session, ctx=ctx, request=request)
    assert mcp["ending_balance"] == data["ending_balance"]
    assert mcp["events"] == data["events"]
    from mcp_server.tools.reports import get_cash_flow

    cash = await get_cash_flow(session=session, ctx=ctx, months=2, funding=True)
    assert cash["funding"]["events"] == data["events"]
    ui = await client.get(
        "/api/reports/cash-flow", headers=auth_headers, params={"months": 2, "funding": True}
    )
    assert ui.status_code == 200 and ui.json()["funding"]["events"] == data["events"]
    scoped = await client.get(
        "/api/reports/money-map",
        headers=auth_headers,
        params={"mode": "plan", "start_date": today.isoformat(), "end_date": data["end_date"]},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["funding"]["events"] == data["events"]
    from datetime import timedelta

    returned = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": aid,
            "description": "Purchase refund",
            "amount": "10",
            "currency": "USD",
            "date": (today + timedelta(days=1)).isoformat(),
            "type": "credit",
            "status": "pending",
            "category_id": cid,
        },
    )
    assert returned.status_code == 201, returned.text
    mapped = await client.get(
        "/api/reports/money-map", headers=auth_headers, params={"mode": "plan", "months": 2}
    )
    assert mapped.status_code == 200, mapped.text
    projected = mapped.json()
    gross = sum(
        Decimal(str(f["amount_primary"]))
        for f in projected["money_map"]["flows"]
        if f["kind"] == "expense"
    )
    assert Decimal(str(projected["summary"]["primary_value"])) == -gross + 10
    # An overlapping category is rejected rather than silently splitting purchases.
    new_id = str(uuid.uuid4())
    bad = await client.put(
        f"/api/forecast/rules/{new_id}", headers=auth_headers, json={**payload, "id": new_id}
    )
    assert bad.status_code == 422


def test_transfer_settlement_keeps_money_in_transit():
    f = flow(
        kind="transfer",
        source="a",
        dest="b",
        amount=40,
        received_amount=40,
        received_currency="USD",
        received_date="2026-02-07",
        received_status="pending",
    )
    t, e, g, w = run([account("a"), account("b", "0")], [f], end=date(2026, 2, 7))
    assert [p["balance"] for p in t] == ["100.00"] * 3
    assert t[0]["accounts"] == {"a": "60.00", "b": "0.00"}
    assert t[-1]["accounts"] == {"a": "60.00", "b": "40.00"}
    assert t[0]["in_transit"] and not t[-1]["in_transit"]


def test_posted_debit_pending_receipt_not_replayed():
    f = flow(
        kind="transfer",
        source="a",
        dest="b",
        amount=40,
        received_amount=40,
        received_currency="USD",
        received_date="2026-02-07",
        received_status="pending",
    )
    f.date = "2026-02-04"
    f.status = "posted"
    t, e, g, w = run([account("a", "60"), account("b", "0")], [f], end=date(2026, 2, 7))
    assert t[0]["balance"] == "100.00" and t[-1]["balance"] == "100.00"
    assert t[-1]["accounts"]["a"] == "60.00"


@pytest.mark.parametrize(
    "frequency,following",
    [("monthly", date(2026, 3, 5)), ("quarterly", date(2026, 5, 5)), ("yearly", date(2027, 2, 5))],
)
def test_recurring_goal_cycles_and_payment_day_peak(frequency, following):
    g = goal()
    g.repeat_frequency = frequency
    t, e, gs, w = run(
        [account("a", "100", pockets=[pocket("p", "100")])],
        [
            flow(amount=100, from_pocket_id="p"),
            flow(
                id="salary",
                kind="income",
                source=None,
                dest="a",
                amount=100,
                on=following.isoformat(),
            ),
            flow(
                id="allocation",
                kind="allocation",
                source="a",
                dest="a",
                to_pocket_id="p",
                amount=100,
                on=following.isoformat(),
            ),
        ],
        [g],
        end=following,
    )
    assert len(gs[0].cycles) == 2 and all(Decimal(c["shortfall"]) == 0 for c in gs[0].cycles)


async def test_scenarios_permissions_and_missing_rates(
    client, auth_headers, test_user, test_workspace, session, viewer_auth_headers
):
    from app.models.transaction import Transaction
    from sqlalchemy import select, func
    from mcp_server.auth import CallContext
    from mcp_server.tools.forecast import propose_forecast_setting

    uid = str(uuid.uuid4())
    payload = {
        "id": uid,
        "name": "Delayed",
        "income_delay_days": 10,
        "expense_multiplier": "1.2",
        "fx_rates": {},
    }
    r = await client.put(f"/api/forecast/scenarios/{uid}", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text
    denied = await client.put(
        f"/api/forecast/scenarios/{uid}", headers=viewer_auth_headers, json=payload
    )
    assert denied.status_code == 403
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id, external=True)
    internal = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    new_payload = {**payload, "id": str(uuid.uuid4())}
    result = await propose_forecast_setting(
        session=session,
        ctx=internal,
        kind="scenario",
        operation="save",
        data=new_payload,
        apply=True,
    )
    assert not result["applied"]
    counts = await session.scalar(select(func.count()).select_from(Transaction))
    req = {"months": 2, "scenario_id": uid}
    r = await client.post("/api/forecast/calculate", headers=auth_headers, json=req)
    assert r.status_code == 200, r.text
    r2 = await client.post("/api/forecast/calculate", headers=auth_headers, json=req)
    assert r2.json() == r.json()
    assert counts == await session.scalar(select(func.count()).select_from(Transaction))
    bad = await client.post(
        "/api/forecast/calculate",
        headers=auth_headers,
        json={"months": 2, "scenario_id": str(uuid.uuid4())},
    )
    assert bad.status_code == 422
    result = await propose_forecast_setting(
        session=session, ctx=ctx, kind="scenario", operation="delete", data={"id": uid}
    )
    assert not result["applied"]
    result = await propose_forecast_setting(
        session=session, ctx=ctx, kind="scenario", operation="delete", data={"id": uid}, apply=True
    )
    assert result["applied"]


async def test_expense_envelope_leap_weekly_and_horizon():
    from app.services.forecast_service import expense_flows

    aid = uuid.uuid4()
    cid = uuid.uuid4()
    wid = uuid.uuid4()
    r = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=aid,
        amount="290",
        currency="USD",
        category_ids=[cid],
        cycle_day=1,
        start_date=date(2024, 2, 1),
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

    flows, periods, w = await expense_flows(
        Session(), wid, [r], date(2024, 2, 1), date(2024, 2, 29), [], {"USD": Decimal(1)}
    )
    assert len(flows) == 29 and sum(Decimal(str(f.amount)) for f in flows) == 290
    partial, _, _ = await expense_flows(
        Session(), wid, [r], date(2024, 2, 1), date(2024, 2, 10), [], {"USD": Decimal(1)}
    )
    assert sum(Decimal(str(f.amount)) for f in partial) == 100
    r.distribution = "weekly"
    r.weekday = 0
    weekly, _, _ = await expense_flows(
        Session(), wid, [r], date(2024, 2, 1), date(2024, 2, 29), [], {"USD": Decimal(1)}
    )
    assert weekly[0].date == "2024-02-01" and weekly[0].amount == 40
    assert sum(Decimal(str(f.amount)) for f in weekly) == 290


async def test_actual_pending_and_projection_consume_one_budget_envelope():
    from app.services.forecast_service import expense_flows

    aid = uuid.uuid4()
    cid = uuid.uuid4()
    wid = uuid.uuid4()
    r = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=aid,
        amount="100",
        currency="USD",
        category_ids=[cid],
        start_date=date(2026, 2, 1),
    )
    actual = SimpleNamespace(
        account_id=aid,
        pocket_id=None,
        category_id=cid,
        date=date(2026, 2, 2),
        type="debit",
        amount=Decimal(30),
        currency="USD",
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [actual])

    pending = flow(id="pending", amount=20, source=str(aid), on="2026-02-06", category_id=str(cid))
    pending.status = "pending"
    projected = flow(id="native", amount=10, source=str(aid), on="2026-02-07", category_id=str(cid))
    fs, p, w = await expense_flows(
        Session(),
        wid,
        [r],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [pending, projected],
        {"USD": Decimal(1)},
    )
    assert p[0]["actual"] == "30" and p[0]["committed"] == "30.00"
    assert sum(Decimal(str(f.amount)) for f in fs) == 40
    stressed, sp, _ = await expense_flows(
        Session(),
        wid,
        [r],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [pending, projected],
        {"USD": Decimal(1)},
        expense_multiplier=Decimal(2),
    )
    assert Decimal(sp[0]["target"]) == 200 and sum(Decimal(str(f.amount)) for f in stressed) == 140
    r.amount = Decimal(10)
    fs, p, w = await expense_flows(
        Session(),
        wid,
        [r],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [pending, projected],
        {"USD": Decimal(1)},
    )
    assert (
        not fs and Decimal(p[0]["exceeded"]) == 50 and any(x.code == "budget_exceeded" for x in w)
    )


async def test_payment_mapping_does_not_add_a_second_expense():
    from app.services.forecast_service import expense_flows

    aid = uuid.uuid4()
    pid = uuid.uuid4()
    cid = uuid.uuid4()
    r = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Annual rent",
        account_id=aid,
        pocket_id=pid,
        currency="USD",
        category_ids=[cid],
        mode="payments_only",
        start_date=date(2026, 1, 1),
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

    existing = flow(amount=100, source=str(aid), on="2026-02-05", category_id=str(cid))
    fs, p, w = await expense_flows(
        Session(),
        uuid.uuid4(),
        [r],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [existing],
        {"USD": Decimal(1)},
    )
    assert not fs and not p and existing.from_pocket_id == str(pid)


async def test_scenario_re_evaluates_income_phase_and_omits_bonus(client, auth_headers):
    from app.core.app_clock import app_today
    from datetime import timedelta
    from tests.test_pockets import setup_account

    aid, pid = await setup_account(client, auth_headers)
    today = app_today()
    paid = today + timedelta(days=5)
    r = await client.post(
        "/api/recurring-transactions",
        headers=auth_headers,
        json={
            "description": "Bonus",
            "account_id": aid,
            "amount": "500",
            "currency": "USD",
            "type": "credit",
            "frequency": "monthly",
            "day_of_month": paid.day,
            "start_date": paid.isoformat(),
            "auto_generate": False,
        },
    )
    assert r.status_code == 201, r.text
    recurring_id = r.json()["id"]
    for percentage, lo, hi in [
        ("10", today, paid + timedelta(days=2)),
        ("25", paid + timedelta(days=3), None),
    ]:
        p = {
            "id": str(uuid.uuid4()),
            "account_id": aid,
            "pocket_id": pid,
            "percentage": percentage,
            "start_date": str(lo),
        }
        if hi:
            p["end_date"] = str(hi)
        rule = await client.post("/api/pockets/income-rules", headers=auth_headers, json=p)
        assert rule.status_code == 201, rule.text
    base = await client.post("/api/forecast/calculate", headers=auth_headers, json={"months": 2})
    assert base.status_code == 200, base.text
    delayed = await client.post(
        "/api/forecast/calculate",
        headers=auth_headers,
        json={
            "months": 2,
            "scenario": {"id": str(uuid.uuid4()), "name": "Late", "income_delay_days": 10},
        },
    )
    assert delayed.status_code == 200, delayed.text
    source = [e for e in base.json()["events"] if e["kind"] == "allocation"]
    late = [e for e in delayed.json()["events"] if e["kind"] == "allocation"]
    assert source[0]["amount"] == "50.00" and late[0]["amount"] == "125.00"
    skipped = await client.post(
        "/api/forecast/calculate",
        headers=auth_headers,
        json={
            "months": 2,
            "scenario": {
                "id": str(uuid.uuid4()),
                "name": "No bonus",
                "excluded_recurring_ids": [recurring_id],
            },
        },
    )
    assert skipped.status_code == 200, skipped.text
    assert not [e for e in skipped.json()["events"] if e["kind"] in ("income", "allocation")]
    cash = await client.get(
        "/api/reports/cash-flow",
        headers=auth_headers,
        params={"months": 2, "funding": "true", "interval": "monthly"},
    )
    assert cash.status_code == 200, cash.text
    assert cash.json()["funding"]["events"] == base.json()["events"]
    assert len(cash.json()["trend"]) <= 3
    mmap = await client.get(
        "/api/reports/money-map", headers=auth_headers, params={"months": 2, "mode": "plan"}
    )
    assert mmap.status_code == 200, mmap.text
    assert mmap.json()["funding"]["events"] == base.json()["events"]


def test_zero_unpriced_account_and_missing_source_are_not_fake_funding():
    t, _, _, _ = run([account(), account("b", "0", "XYZ")], [])
    assert t[0]["balance"] == "100.00"
    t, events, gs, warnings = run(
        [account(pockets=[pocket(balance="100")])],
        [flow(source="deleted")],
        [goal()],
    )
    assert gs[0].status == "unknown" and any(w.code == "missing_account" for w in warnings)
    assert events[0]["funded"] is None and t[0]["balance"] is None


async def test_readonly_goal_conversion_never_fetches_or_falls_back(monkeypatch):
    from app.services import goal_service
    from unittest.mock import AsyncMock

    session = AsyncMock()
    rate = AsyncMock(return_value=None)
    mutate = AsyncMock(side_effect=AssertionError("Read attempted FX fetch"))
    monkeypatch.setattr(goal_service, "_resolve_rate", rate)
    monkeypatch.setattr(goal_service, "convert", mutate)
    with pytest.raises(ValueError, match="valuation unavailable"):
        await goal_service._convert_amount(session, Decimal(10), "IDR", "USD", allow_fx_fetch=False)
    rate.assert_awaited_once_with(session, "IDR", "USD", allow_fetch=False)
    mutate.assert_not_awaited()


@pytest.mark.parametrize(
    "credit_kind,net",
    [("refund", "60"), ("reimbursement", "60"), ("loan_principal", "80"), ("income", "80")],
)
async def test_multi_account_envelope_credit_roles(credit_kind, net):
    from app.services.forecast_service import expense_flows

    aid, bid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rule = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=aid,
        actual_account_ids=[aid, bid],
        amount=100,
        currency="USD",
        category_ids=[cid],
        credit_kind=credit_kind,
        start_date=date(2026, 2, 1),
    )
    rows = [
        SimpleNamespace(
            account_id=a,
            pocket_id=None,
            category_id=cid,
            date=date(2026, 2, 2),
            type=kind,
            amount=Decimal(amount),
            currency="USD",
        )
        for a, kind, amount in [(aid, "debit", 30), (bid, "debit", 50), (bid, "credit", 20)]
    ]

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: rows)

    fs, periods, _ = await expense_flows(
        Session(),
        uuid.uuid4(),
        [rule],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [],
        {"USD": Decimal(1)},
    )
    assert Decimal(periods[0]["actual"]) == Decimal(net)
    assert sum(Decimal(str(f.amount)) for f in fs) == 100 - Decimal(net)


async def test_history_requires_six_reviewed_complete_cycles():
    from app.services.forecast_service import history_recommendations

    rule = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=uuid.uuid4(),
        amount=100,
        currency="USD",
        category_ids=[uuid.uuid4()],
        start_date=date(2026, 1, 1),
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

    unavailable = await history_recommendations(Session(), uuid.uuid4(), [rule], date(2026, 10, 12))
    assert unavailable[0]["status"] == "unavailable"
    rule.reviewed_history = True
    rule.history_from, rule.history_through = date(2026, 4, 1), date(2026, 9, 30)
    available = await history_recommendations(Session(), uuid.uuid4(), [rule], date(2026, 10, 12))
    assert available[0]["status"] == "available" and len(available[0]["periods"]) == 6


async def test_mixed_merchant_category_is_visible_until_mapped():
    from app.services.forecast_service import expense_flows

    aid, cid, mixed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rule = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=aid,
        amount=100,
        currency="USD",
        category_ids=[cid],
        start_date=date(2026, 2, 1),
    )
    tx = SimpleNamespace(
        account_id=aid,
        pocket_id=None,
        category_id=mixed,
        date=date(2026, 2, 2),
        type="debit",
        amount=Decimal(20),
        currency="USD",
        description="Mixed merchant",
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [tx])

    _, periods, warnings = await expense_flows(
        Session(),
        uuid.uuid4(),
        [rule],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [],
        {"USD": Decimal(1)},
    )
    assert Decimal(periods[0]["actual"]) == 0
    assert any(
        w.code == "unmapped_actual_category" and w.detail == "Mixed merchant" for w in warnings
    )
    rule.category_ids.append(mixed)
    _, periods, warnings = await expense_flows(
        Session(),
        uuid.uuid4(),
        [rule],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [],
        {"USD": Decimal(1)},
    )
    assert Decimal(periods[0]["actual"]) == 20
    assert not any(w.code == "unmapped_actual_category" for w in warnings)


async def test_expected_refund_is_cash_return_and_consumes_one_net_envelope():
    from app.services.forecast_service import expense_flows

    aid, cid = uuid.uuid4(), uuid.uuid4()
    rule = ExpenseForecastRule(
        id=uuid.uuid4(),
        name="Food",
        account_id=aid,
        amount=100,
        currency="USD",
        category_ids=[cid],
        start_date=date(2026, 2, 1),
    )

    class Session:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

    purchase = flow(amount=30, source=str(aid), category_id=str(cid))
    refund = flow(
        id="refund",
        kind="investment",
        amount=10,
        source=None,
        dest=str(aid),
        category_id=str(cid),
        cash_role="refund",
    )
    fs, periods, _ = await expense_flows(
        Session(),
        uuid.uuid4(),
        [rule],
        date(2026, 2, 5),
        date(2026, 2, 28),
        [purchase, refund],
        {"USD": Decimal(1)},
    )
    assert Decimal(periods[0]["committed"]) == 20
    assert sum(Decimal(str(f.amount)) for f in fs) == 80
    t, events, _, _ = run(
        [account(str(aid), "100")], [refund, purchase] + fs, end=date(2026, 2, 28)
    )
    assert t[-1]["balance"] == "0.00"
    assert events[0]["cash_role"] == "refund"
