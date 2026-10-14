"""One read-only plan built from existing Money Map routes and budget settings."""

import calendar
from typing import cast
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.forecast import ForecastSetting
from app.models.fx_rate import FxRate
from app.models.pocket import Pocket
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.forecast import (
    ExpenseForecastRule,
    ForecastRequest,
    ForecastScenario,
    ForecastWarning,
    FundingForecast,
    GoalForecast,
)
from app.schemas.report import MoneyMapFlow
from app.services import budget_service, money_map_service, goal_service
from app.services.fx_rate_service import _resolve_rate
from app.services.recurring_transaction_service import _advance_months

CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def cycle_start(on: date, day: int) -> date:
    candidate = on.replace(day=min(day, calendar.monthrange(on.year, on.month)[1]))
    return candidate if candidate <= on else _advance_months(candidate, -1, day)


async def list_settings(session, workspace_id, kind):
    if kind not in ("expense_rule", "scenario"):
        raise ValueError("Unknown forecast setting kind")
    rows = (
        await session.scalars(
            select(ForecastSetting)
            .where(ForecastSetting.workspace_id == workspace_id, ForecastSetting.kind == kind)
            .order_by(ForecastSetting.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    schema = ExpenseForecastRule if kind == "expense_rule" else ForecastScenario
    return [schema.model_validate(row.data) for row in rows]


async def validate_setting(session, workspace_id, kind, data):
    if kind not in ("expense_rule", "scenario"):
        raise ValueError("Unknown forecast setting kind")
    schema = ExpenseForecastRule if kind == "expense_rule" else ForecastScenario
    payload = schema.model_validate(data)
    existing = await session.get(ForecastSetting, payload.id)
    if existing and (existing.workspace_id != workspace_id or existing.kind != kind):
        raise ValueError("Setting ID belongs to a different workspace or kind")
    if isinstance(payload, ForecastScenario):
        for rid in payload.excluded_recurring_ids:
            row = await session.get(RecurringTransaction, rid)
            if not row or row.workspace_id != workspace_id or row.type != "credit":
                raise ValueError("Excluded income plan is not in this workspace")
        return payload
    account = await session.get(Account, payload.account_id)
    from app.services.recurring_transaction_service import _verify_account_in_workspace

    await _verify_account_in_workspace(session, workspace_id, payload.account_id)
    if not account or account.is_closed:
        raise ValueError("Choose an open account in this workspace")
    for aid in payload.actual_account_ids:
        matched_account = await session.get(Account, aid)
        await _verify_account_in_workspace(session, workspace_id, aid)
        if not matched_account:
            raise ValueError("Actual-spending account is not in this workspace")
    if payload.currency != account.currency:
        raise ValueError("Rule currency must match the paying account")
    if payload.pocket_id:
        pocket = await session.get(Pocket, payload.pocket_id)
        if (
            not pocket
            or pocket.workspace_id != workspace_id
            or pocket.account_id != account.id
            or pocket.archived_at
        ):
            raise ValueError("Choose an active pocket of the paying account")
    if payload.budget_id:
        budget = await session.get(Budget, payload.budget_id)
        if not budget or budget.workspace_id != workspace_id:
            raise ValueError("Budget is not in this workspace")
        if budget.currency and budget.currency != payload.currency:
            raise ValueError("Budget and rule currencies must match")
    for cid in payload.category_ids:
        category = await session.get(Category, cid)
        if (
            not category
            or category.workspace_id != workspace_id
            or (category.treat_as_transfer and payload.mode != "payments_only")
            or category.is_ignored
        ):
            raise ValueError("Choose reporting expense categories in this workspace")
    if payload.enabled:
        for rule in await list_settings(session, workspace_id, "expense_rule"):
            assert isinstance(rule, ExpenseForecastRule)
            if (
                rule.id == payload.id
                or not rule.enabled
                or not set(rule.actual_account_ids or [rule.account_id])
                & set(payload.actual_account_ids or [payload.account_id])
            ):
                continue
            if (rule.end_date and rule.end_date < payload.start_date) or (
                payload.end_date and payload.end_date < rule.start_date
            ):
                continue
            if set(rule.category_ids) & set(payload.category_ids) or (
                not rule.category_ids
                and not payload.category_ids
                and rule.pocket_id == payload.pocket_id
            ):
                raise ValueError("Overlapping rules would count the same actual spending twice")
    return payload


async def save_setting(session: AsyncSession, workspace_id, kind, data):
    from app.models.workspace import Workspace

    await session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    payload = await validate_setting(session, workspace_id, kind, data)
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    values = dict(
        id=payload.id, workspace_id=workspace_id, kind=kind, data=payload.model_dump(mode="json")
    )
    stmt = (
        insert(ForecastSetting)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[ForecastSetting.id],
            set_={"data": values["data"]},
            where=(ForecastSetting.workspace_id == workspace_id) & (ForecastSetting.kind == kind),
        )
    )
    result = await session.scalar(stmt.returning(ForecastSetting.id))
    if result is None:
        raise ValueError("Setting ID belongs to a different workspace or kind")
    await session.commit()
    return payload


async def delete_setting(session, workspace_id, kind, setting_id, *, apply=False):
    row = await session.get(ForecastSetting, setting_id)
    if row is None:
        return
    if row.workspace_id != workspace_id or row.kind != kind:
        raise ValueError("Setting not found")
    if apply:
        await session.delete(row)
        await session.commit()


def matches(rule, row):
    return str(row.account_id) in {
        str(a) for a in (rule.actual_account_ids or [rule.account_id])
    } and (
        str(row.category_id) in {str(cid) for cid in rule.category_ids}
        if rule.category_ids
        else str(row.pocket_id) == str(rule.pocket_id)
    )


def payment_pocket(rules, transaction):
    """Use the configured payment route for a new recurring debit only."""
    if transaction.pocket_id or transaction.type != "debit":
        return transaction.pocket_id
    candidates = {
        rule.pocket_id
        for rule in rules
        if rule.enabled
        and rule.mode == "payments_only"
        and rule.account_id == transaction.account_id
        and rule.pocket_id
        and rule.currency == transaction.currency
        and rule.start_date <= transaction.date
        and (rule.end_date is None or transaction.date <= rule.end_date)
        and matches(rule, transaction)
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


async def expense_flows(
    session, workspace_id, rules, start, end, existing, rates, expense_multiplier=Decimal(1)
):
    """Remaining envelope = budget - posted - booked/projected commitments."""
    if not rules:
        return [], [], []
    earliest = min(cycle_start(start, r.cycle_day) for r in rules)
    txs = (
        await session.scalars(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id,
                Transaction.date >= earliest,
                Transaction.date <= end,
                Transaction.status == "posted",
                Transaction.date <= start,
                Transaction.source != "opening_balance",
                Transaction.transfer_pair_id.is_(None),
                Transaction.is_ignored == False,
                Transaction.exclude_from_pnl == False,
            )
        )
    ).all()
    budgets = {}
    flows, periods, warnings = [], [], []
    for rule in rules:
        if not rule.enabled or rule.start_date > end or (rule.end_date and rule.end_date < start):
            continue
        if rule.mode == "payments_only":
            for flow in existing:
                if (
                    flow.kind == "expense"
                    and flow.from_account_id == str(rule.account_id)
                    and flow.category_id in {str(cid) for cid in rule.category_ids}
                    and rule.start_date <= date.fromisoformat(flow.date)
                    and (not rule.end_date or date.fromisoformat(flow.date) <= rule.end_date)
                ):
                    if rule.pocket_id and not flow.from_pocket_id:
                        flow.from_pocket_id = str(rule.pocket_id)
            continue
        cursor = cycle_start(max(start, rule.start_date), rule.cycle_day)
        while cursor <= end and (not rule.end_date or cursor <= rule.end_date):
            following = _advance_months(cursor, 1, rule.cycle_day)
            period_end = following - timedelta(days=1)
            lo = max(start, cursor, rule.start_date)
            hi = min(end, period_end, rule.end_date or period_end)
            amount = rule.amount
            if amount is None:
                template = await session.get(Budget, rule.budget_id)
                key = cursor.replace(day=1)
                if key not in budgets:
                    budgets[key] = await budget_service._build_budget_map(
                        session, workspace_id, key
                    )
                effective = budgets[key].get(str(template.category_id)) if template else None
                amount = effective.amount if effective else Decimal(0)
                if not effective:
                    warnings.append(
                        ForecastWarning(
                            code="missing_budget",
                            date=cursor,
                            account_id=str(rule.account_id),
                            detail=rule.name,
                        )
                    )
            unclassified = [
                tx
                for tx in txs
                if cursor <= tx.date < following
                and tx.type == "debit"
                and tx.category_id is None
                and str(tx.account_id)
                in {str(a) for a in (rule.actual_account_ids or [rule.account_id])}
            ]
            if unclassified and rule.category_ids:
                warnings.append(
                    ForecastWarning(
                        code="unclassified_actual",
                        date=lo,
                        account_id=str(rule.account_id),
                        detail=f"{rule.name}: {len(unclassified)}",
                    )
                )
            target = money(amount * expense_multiplier)
            spent = Decimal(0)
            for tx in txs:
                if cursor <= tx.date < following and matches(rule, tx):
                    if tx.currency != rule.currency:
                        warnings.append(
                            ForecastWarning(
                                code="transaction_currency",
                                date=tx.date,
                                account_id=str(rule.account_id),
                                detail=rule.name,
                            )
                        )
                        continue
                    if tx.type == "debit":
                        spent += tx.amount
                    elif rule.credit_kind in ("refund", "reimbursement"):
                        spent -= tx.amount
            committed = Decimal(0)
            committed_flow_ids = []
            for flow in existing:
                if not cursor <= date.fromisoformat(flow.date) < following:
                    continue
                returned = flow.kind in ("income", "investment") and (
                    flow.cash_role or rule.credit_kind
                ) in ("refund", "reimbursement")
                if flow.kind != "expense" and not returned:
                    continue
                account_id = flow.to_account_id if returned else flow.from_account_id
                pocket_id = flow.to_pocket_id if returned else flow.from_pocket_id
                if account_id not in {
                    str(a) for a in (rule.actual_account_ids or [rule.account_id])
                }:
                    continue
                matched = (
                    (flow.category_id in {str(cid) for cid in rule.category_ids})
                    if rule.category_ids
                    else pocket_id == str(rule.pocket_id)
                )
                if matched and flow.currency == rule.currency:
                    committed_flow_ids.append(flow.id)
                    committed += money(flow.amount) * (-1 if returned else 1)
                    if rule.pocket_id and not pocket_id and account_id == str(rule.account_id):
                        if returned:
                            flow.to_pocket_id = str(rule.pocket_id)
                        else:
                            flow.from_pocket_id = str(rule.pocket_id)
            remaining = max(Decimal(0), target - spent - committed)
            excess = max(Decimal(0), spent + committed - target)
            periods.append(
                dict(
                    rule_id=str(rule.id),
                    name=rule.name,
                    start=cursor.isoformat(),
                    end=period_end.isoformat(),
                    currency=rule.currency,
                    target=str(target),
                    actual=str(spent),
                    committed=str(committed),
                    committed_flow_ids=committed_flow_ids,
                    forecast=str(remaining),
                    exceeded=str(excess),
                )
            )
            if excess:
                warnings.append(
                    ForecastWarning(
                        code="budget_exceeded",
                        date=lo,
                        account_id=str(rule.account_id),
                        amount=excess,
                        currency=rule.currency,
                        detail=rule.name,
                    )
                )
            # Horizon truncation must not accelerate spending after its end.
            all_days = [
                lo + timedelta(days=i)
                for i in range(max(0, (min(period_end, rule.end_date or period_end) - lo).days + 1))
            ]
            dates = (
                all_days
                if rule.distribution == "daily"
                else (
                    [d for d in all_days if d == lo or d.weekday() == rule.weekday]
                    if rule.distribution == "weekly"
                    else [lo]
                )
            )
            if not dates and all_days:
                dates = [lo]
            prior = Decimal(0)
            for index, on in enumerate(dates):
                if rule.distribution == "weekly":
                    following_slot = (
                        dates[index + 1]
                        if index + 1 < len(dates)
                        else all_days[-1] + timedelta(days=1)
                    )
                    cumulative = money(
                        remaining * Decimal((following_slot - lo).days) / Decimal(len(all_days))
                    )
                else:
                    cumulative = money(remaining * Decimal(index + 1) / Decimal(len(dates)))
                part = cumulative - prior
                prior = cumulative
                if on > hi or not part:
                    continue
                rate = rates.get(rule.currency)
                flows.append(
                    MoneyMapFlow(
                        id=f"budget:{rule.id}:{cursor}:{on}",
                        date=on.isoformat(),
                        kind="expense",
                        status="projected",
                        description=rule.name,
                        from_account_id=str(rule.account_id),
                        from_pocket_id=str(rule.pocket_id) if rule.pocket_id else None,
                        amount=float(part),
                        currency=rule.currency,
                        amount_primary=float(money(part * rate)) if rate is not None else None,
                        category_id=str(rule.category_ids[0])
                        if len(rule.category_ids) == 1
                        else None,
                    )
                )
            cursor = following
    # Explicitly surface categorized purchases outside every envelope. Mixed
    # merchant categories must be configured rather than silently treated as food.
    active = [
        r
        for r in rules
        if r.enabled and r.start_date <= end and (not r.end_date or r.end_date >= start)
    ]
    covered_accounts = {str(a) for r in active for a in (r.actual_account_ids or [r.account_id])}
    for tx in txs:
        if (
            tx.type == "debit"
            and tx.category_id is not None
            and str(tx.account_id) in covered_accounts
            and not any(matches(r, tx) for r in active)
        ):
            warnings.append(
                ForecastWarning(
                    code="unmapped_actual_category",
                    date=tx.date,
                    account_id=str(tx.account_id),
                    amount=tx.amount,
                    currency=tx.currency,
                    detail=getattr(tx, "description", "") or str(tx.category_id),
                )
            )
    return flows, periods, warnings


def simulate(accounts, flows, goals, rates, start, end, *, scenario=None):
    """Pure Decimal cash/pocket simulation. No ledger mutation or imaginary funding."""
    balances = {str(a.account_id): money(a.balance) for a in accounts}
    currencies = {str(a.account_id): a.currency for a in accounts}
    types = {str(a.account_id): getattr(a, "account_type", "checking") for a in accounts}
    pockets = {str(p.id): money(p.balance) for a in accounts for p in a.items}
    pocket_accounts = {str(p.id): str(a.account_id) for a in accounts for p in a.items}
    links = {str(p.id): str(p.goal_id) for a in accounts for p in a.items if p.goal_id}
    warnings, events, timeline = [], [], []
    unknown = set()
    uncertain_accounts = set()
    transit = {}
    transit_funding = {}

    def convert(value, src, dst):
        if src == dst or Decimal(str(value)) == 0:
            return money(value)
        if rates.get(src) is None or rates.get(dst) is None:
            return None
        return money(Decimal(str(value)) * rates[src] / rates[dst])

    def unallocated(account):
        return balances[account] - sum(
            v for p, v in pockets.items() if pocket_accounts[p] == account
        )

    def total(values):
        parts = [
            Decimal(0)
            if v == 0
            else money(v * rates[currencies[a]])
            if rates.get(currencies[a]) is not None
            else None
            for a, v in values.items()
        ]
        return (
            money(sum((p for p in parts if p is not None), Decimal(0)))
            if all(x is not None for x in parts)
            else None
        )

    def goal_amount(goal):
        shared_free = total({a: unallocated(a) for a in balances})
        if shared_free is None or shared_free < 0:
            return None
        if any(
            balances[pocket_accounts[p]]
            < sum(v for q, v in pockets.items() if pocket_accounts[q] == pocket_accounts[p])
            for p in pockets
            if links.get(p) == str(goal.id)
        ):
            return None
        return goal_allocated_amount(goal)

    def goal_allocated_amount(goal):
        """Native simulated pocket balance, without claiming family funding."""
        vals = [
            convert(v, currencies[pocket_accounts[p]], goal.currency)
            for p, v in pockets.items()
            if links.get(p) == str(goal.id)
        ]
        return (
            sum(vals, Decimal(0))
            if vals and all(v is not None for v in vals) and str(goal.id) not in unknown
            else None
        )

    def reason_for(goal):
        if not any(links.get(p) == str(goal.id) for p in pockets):
            return "no_linked_pocket"
        if str(goal.id) in unknown or rates.get(goal.currency) is None:
            return "missing_fx"
        free = total({a: unallocated(a) for a in balances})
        if free is None:
            return "missing_fx"
        if free < 0:
            return "shared_funds_shortfall"
        if goal_amount(goal) is None:
            return "unbacked_pocket"
        return None

    history = {str(g.id): [] for g in goals}
    current = {
        str(g.id): money(g.current_amount)
        if getattr(g, "valuation_available", True) and hasattr(g, "current_amount")
        else goal_amount(g)
        for g in goals
    }
    achieved = {
        str(g.id): start
        if current[str(g.id)] is not None and current[str(g.id)] >= g.target_amount
        else None
        for g in goals
    }
    at_deadline = {}
    deadline_reasons = {}
    daily_peaks = {str(g.id): {} for g in goals}
    by_day = {}
    # Split settlement dates; funds in transit remain part of family wealth once.
    expanded = []
    for flow in flows:
        if flow.kind == "transfer" and flow.received_date and (
            flow.received_date != flow.date
            or (flow.received_status or flow.status) != flow.status
        ):
            receipt = flow.model_copy(
                update=dict(
                    id=flow.id + ":receipt",
                    date=flow.received_date,
                    status=flow.received_status or flow.status,
                    from_account_id=None,
                    from_pocket_id=None,
                    amount=flow.received_amount
                    if flow.received_amount is not None
                    else flow.amount,
                    currency=flow.received_currency or flow.currency,
                    received_amount=None,
                    received_currency=None,
                )
            )
            debit = flow.model_copy(
                update=dict(
                    to_account_id=None,
                    to_pocket_id=None,
                    received_amount=None,
                    received_currency=None,
                )
            )
            expanded.extend([debit, receipt])
            if flow.status == "posted" and receipt.status != "posted":
                transit[flow.id] = (money(flow.amount), flow.currency)
        else:
            expanded.append(flow)
    # Posted rows are already in current balances and must never be replayed.
    for flow in expanded:
        on = date.fromisoformat(flow.date)
        if flow.status != "posted" and start <= on <= end:
            by_day.setdefault(on, []).append(flow)

    def wealth():
        if uncertain_accounts:
            return None
        bank = total(balances)
        values = [
            money(v * rates[c]) if rates.get(c) is not None else None for v, c in transit.values()
        ]
        return (
            money(bank + sum((v for v in values if v is not None), Decimal(0)))
            if bank is not None and all(x is not None for x in values)
            else None
        )

    for i in range((end - start).days + 1):
        on = start + timedelta(days=i)
        opening = dict(balances)
        peak = {str(g.id): goal_amount(g) for g in goals}
        day_events = []
        # Income first, then allocations, transfers, expenses; show funding reliance separately.
        order = {"income": 0, "allocation": 1, "transfer": 2, "investment": 3, "expense": 4}
        todays = sorted(
            by_day.get(on, []),
            key=lambda f: (
                0 if f.to_account_id and not f.from_account_id else order[f.kind],
                f.status != "pending",
                f.id,
            ),
        )
        for flow in todays:
            source, dest = flow.from_account_id, flow.to_account_id
            amount = money(flow.amount)
            state = dict(
                cash_role=flow.cash_role or flow.kind,
                id=flow.id,
                date=flow.date,
                kind=flow.kind,
                status=flow.status,
                description=flow.description,
                currency=flow.currency,
                amount=str(amount),
                from_account_id=source,
                to_account_id=dest,
                from_pocket_id=flow.from_pocket_id,
                to_pocket_id=flow.to_pocket_id,
                funded=str(amount),
                funded_currency=currencies.get(source, flow.currency),
                received_amount=str(flow.received_amount)
                if flow.received_amount is not None
                else None,
                received_currency=flow.received_currency,
                shortfall="0.00",
            )
            if source and source not in balances or dest and dest not in balances:
                warnings.append(
                    ForecastWarning(
                        code="missing_account", date=on, flow_id=flow.id, detail=flow.description
                    )
                )
                unknown.update(links.values())
                uncertain_accounts.update(a for a in [source, dest] if a)
                state.update(funded=None, shortfall=None)
                day_events.append(state)
                continue
            debit = convert(amount, flow.currency, currencies[source]) if source else None
            received = flow.received_amount if flow.received_amount is not None else amount
            receipt_currency = flow.received_currency or flow.currency
            credit = convert(received, receipt_currency, currencies[dest]) if dest else None
            if source and debit is None or dest and credit is None:
                warnings.append(
                    ForecastWarning(
                        code="missing_fx", date=on, flow_id=flow.id, detail=flow.description
                    )
                )
                unknown.update(links.values())
                uncertain_accounts.update(a for a in [source, dest] if a)
                state.update(funded=None, shortfall=None)
                day_events.append(state)
                continue
            if source:
                assert debit is not None
            if dest:
                assert credit is not None
            if flow.kind == "allocation":
                assert debit is not None
                if flow.to_pocket_id not in pockets:
                    warnings.append(
                        ForecastWarning(
                            code="missing_pocket", date=on, flow_id=flow.id, detail=flow.description
                        )
                    )
                    state.update(funded="0.00", shortfall=str(amount))
                    day_events.append(state)
                    continue
                available = (
                    max(
                        Decimal(0),
                        min(pockets.get(flow.from_pocket_id, Decimal(0)), balances[source]),
                    )
                    if flow.from_pocket_id
                    else max(Decimal(0), unallocated(source))
                )
                funded = min(debit, available)
                if flow.from_pocket_id:
                    pockets[flow.from_pocket_id] -= funded
                if flow.to_pocket_id in pockets:
                    pockets[flow.to_pocket_id] += funded
                else:
                    funded = Decimal(0)
                    warnings.append(
                        ForecastWarning(
                            code="missing_pocket", date=on, flow_id=flow.id, detail=flow.description
                        )
                    )
                short = debit - funded
                state.update(funded=str(funded), shortfall=str(short))
                if short:
                    warnings.append(
                        ForecastWarning(
                            code="unfunded_allocation",
                            date=on,
                            account_id=source,
                            goal_id=links.get(flow.to_pocket_id),
                            flow_id=flow.id,
                            amount=short,
                            currency=currencies[source],
                            detail=flow.description,
                        )
                    )
            else:
                if source:
                    assert debit is not None
                    available = (
                        pockets.get(flow.from_pocket_id, Decimal(0))
                        if flow.from_pocket_id
                        else unallocated(source)
                    )
                    short = max(
                        Decimal(0), debit - max(Decimal(0), min(available, balances[source]))
                    )
                    if short:
                        warnings.append(
                            ForecastWarning(
                                code="pocket_top_up_required"
                                if flow.from_pocket_id
                                and max(Decimal(0), unallocated(source)) >= short
                                else "protected_funds_required",
                                date=on,
                                account_id=source,
                                flow_id=flow.id,
                                goal_id=links.get(flow.from_pocket_id),
                                amount=short,
                                currency=currencies[source],
                                detail=flow.description,
                            )
                        )
                    if flow.from_pocket_id in pockets:
                        pockets[flow.from_pocket_id] -= min(
                            debit, max(Decimal(0), pockets[flow.from_pocket_id])
                        )
                    # Cash obligation remains visible even when there is no funding.
                    balances[source] -= debit
                    state.update(funded=str(max(Decimal(0), debit - short)), shortfall=str(short))
                    if (
                        opening[source]
                        < debit
                        <= opening[source]
                        + sum(
                            money(x.amount)
                            for x in todays
                            if x.kind == "income"
                            and x.to_account_id == source
                            and x.currency == currencies[source]
                        )
                    ):
                        warnings.append(
                            ForecastWarning(
                                code="same_day_income_dependency",
                                date=on,
                                account_id=source,
                                flow_id=flow.id,
                                detail=flow.description,
                            )
                        )
                if dest:
                    assert credit is not None
                    balances[dest] += credit
                    if flow.to_pocket_id in pockets:
                        funded_receipt = (
                            min(credit, money(credit * max(Decimal(0), debit - short) / debit))
                            if source and debit
                            else credit
                        )
                        if flow.id.endswith(":receipt"):
                            funded_receipt = money(
                                funded_receipt
                                * transit_funding.get(flow.id.removesuffix(":receipt"), Decimal(1))
                            )
                        pockets[flow.to_pocket_id] += funded_receipt
            if (
                flow.kind == "transfer"
                and flow.received_date
                and flow.received_date != flow.date
                and source
                and not dest
            ):
                transit[flow.id] = (amount, flow.currency)
                transit_funding[flow.id] = (
                    max(Decimal(0), debit - short) / debit if debit else Decimal(0)
                )
            if flow.id.endswith(":receipt"):
                transit.pop(flow.id.removesuffix(":receipt"), None)
            day_events.append(state)
            for goal in goals:
                gid = str(goal.id)
                value = goal_amount(goal)
                if value is not None:
                    peak[gid] = max(peak[gid] or Decimal(0), value)
                    if value >= goal.target_amount and achieved[gid] is None:
                        achieved[gid] = on
        for aid, balance in balances.items():
            if balance < 0:
                warnings.append(
                    ForecastWarning(
                        code="credit_liability"
                        if types[aid] in {"credit_card", "loan"}
                        else "account_deficit",
                        date=on,
                        account_id=aid,
                        amount=-balance,
                        currency=currencies[aid],
                        detail="Negative projected bank balance",
                    )
                )
        for goal in goals:
            gid = str(goal.id)
            value = goal_amount(goal)
            allocated = goal_allocated_amount(goal)
            history[gid].append(
                dict(
                    date=on.isoformat(),
                    amount=str(value) if value is not None else None,
                    allocated_amount=str(allocated) if allocated is not None else None,
                )
            )
            daily_peaks[gid][on] = peak[gid] if value is not None else None
            if goal.target_date == on:
                at_deadline[gid] = daily_peaks[gid][on]
                deadline_reasons[gid] = reason_for(goal)
        events.extend(day_events)
        timeline.append(
            dict(
                date=on.isoformat(),
                balance=str(wealth()) if wealth() is not None else None,
                free_funds=str(total({a: unallocated(a) for a in balances}))
                if not uncertain_accounts
                and total({a: unallocated(a) for a in balances}) is not None
                else None,
                accounts={
                    a: None if a in uncertain_accounts else str(v) for a, v in balances.items()
                },
                pockets={p: str(v) for p, v in pockets.items()},
                in_transit={k: dict(amount=str(v), currency=c) for k, (v, c) in transit.items()},
                event_ids=[e["id"] for e in day_events],
            )
        )
    result = []
    for goal in goals:
        gid = str(goal.id)
        projected = at_deadline.get(gid) if goal.target_date else goal_amount(goal)
        if goal.target_date and not start <= goal.target_date <= end:
            projected = None
        short = max(Decimal(0), goal.target_amount - projected) if projected is not None else None
        result.append(
            GoalForecast(
                goal_id=gid,
                name=goal.name,
                currency=goal.currency,
                deadline=goal.target_date,
                target=goal.target_amount,
                current=current[gid],
                projected=projected,
                shortfall=short,
                achieved_date=achieved[gid],
                status="unknown" if short is None else "funded" if short == 0 else "shortfall",
                reason=(
                    "outside_horizon"
                    if goal.target_date and not start <= goal.target_date <= end
                    else deadline_reasons.get(gid, reason_for(goal))
                )
                if short is None
                else None,
                history=history[gid],
                cycles=_goal_cycles(goal, start, end, daily_peaks[gid]),
            )
        )
    return timeline, events, result, warnings


def _goal_cycles(goal, start, end, peaks):
    if not goal.repeat_frequency or not goal.target_date:
        return []
    cursor = goal.target_date
    rows = []
    count = 0
    while cursor <= end and count < 110:
        if cursor >= start:
            amount = peaks.get(cursor)
            rows.append(
                dict(
                    deadline=cursor.isoformat(),
                    projected=str(amount) if amount is not None else None,
                    shortfall=str(max(Decimal(0), goal.target_amount - amount))
                    if amount is not None
                    else None,
                )
            )
        if goal.repeat_frequency in ("weekly", "biweekly"):
            cursor += timedelta(days=7 if goal.repeat_frequency == "weekly" else 14)
        else:
            step = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}.get(
                goal.repeat_frequency
            )
            if step is None:
                break
            cursor = _advance_months(cursor, step, goal.target_date.day)
        count += 1
    return rows


async def get_forecast(session, workspace_id, user_id, request: ForecastRequest):
    scenario = request.scenario
    if request.scenario_id:
        scenarios = await list_settings(session, workspace_id, "scenario")
        scenario = next((s for s in scenarios if s.id == request.scenario_id), None)
        if scenario is None:
            raise ValueError("Scenario not found")
    if scenario:
        await validate_setting(session, workspace_id, "scenario", scenario.model_dump())
    base = await money_map_service.get_money_map(
        session,
        workspace_id,
        user_id,
        mode="plan",
        months=request.months,
        end_date=None,
        account_ids=None,
        include_expense_rules=False,
    )
    data = base.money_map
    assert data is not None
    start = date.fromisoformat(data.start_date)
    end = request.end_date or date.fromisoformat(data.end_date)
    if request.full_months and request.end_date is None:
        end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
    if end < start or end > _advance_months(start, 24, start.day):
        raise ValueError("Forecast horizon must be within 24 months from today")
    # Request all routes once through the existing projection deduplication.
    if end != date.fromisoformat(data.end_date):
        base = await money_map_service.get_money_map(
            session,
            workspace_id,
            user_id,
            mode="plan",
            start_date=start,
            end_date=end,
            include_expense_rules=False,
        )
        data = base.money_map
    assert data is not None
    accounts = data.accounts
    data.goals = [
        g
        for g in await goal_service.get_goals(session, workspace_id, user_id, allow_fx_fetch=False)
        if g.status == "active"
    ]
    rates = {base.meta.currency: Decimal(1)}
    currencies = (
        {a.currency for a in accounts}
        | {g.currency for g in data.goals}
        | {f.currency for f in data.flows}
    )
    rate_rows = []
    for currency in sorted(currencies):
        override = (
            scenario.fx_rates.get(currency) if scenario and currency != base.meta.currency else None
        )
        rate = override or await _resolve_rate(
            session, currency, base.meta.currency, allow_fetch=False
        )
        rates[currency] = rate
        stamps = (
            await session.scalars(
                select(FxRate)
                .where(
                    FxRate.quote_currency.in_([currency, base.meta.currency]), FxRate.date <= start
                )
                .order_by(FxRate.date.desc())
                .limit(2)
            )
        ).all()
        rate_rows.append(
            dict(
                currency=currency,
                target=base.meta.currency,
                rate=str(rate) if rate else None,
                date=start.isoformat()
                if override or currency == base.meta.currency
                else (min(s.date for s in stamps).isoformat() if stamps else None),
                source="scenario"
                if override
                else "identity"
                if currency == base.meta.currency
                else ", ".join(sorted({s.source for s in stamps})),
            )
        )
    flows = [
        f.model_copy(deep=True)
        for f in data.flows
        if f.status != "posted"
        or f.kind == "transfer"
        and (f.received_status or f.status) != "posted"
        and f.received_date
    ]
    overdue = []
    for flow in flows:
        debit_overdue = flow.status == "pending" and date.fromisoformat(flow.date) < start
        if debit_overdue:
            overdue.append(
                ForecastWarning(
                    code="overdue_pending",
                    date=date.fromisoformat(flow.date),
                    account_id=flow.from_account_id or flow.to_account_id,
                    flow_id=flow.id,
                    detail=flow.description,
                )
            )
            flow.date = start.isoformat()
        if (
            flow.kind == "transfer"
            and (flow.received_status or flow.status) == "pending"
            and flow.received_date
            and date.fromisoformat(flow.received_date) < start
        ):
            # Each unsettled leg is due now; posted legs stay in opening balances.
            if not debit_overdue:
                overdue.append(
                    ForecastWarning(
                        code="overdue_pending",
                        date=date.fromisoformat(flow.received_date),
                        account_id=flow.to_account_id,
                        flow_id=flow.id,
                        detail=flow.description,
                    )
                )
            flow.received_date = start.isoformat()
    if scenario:
        excluded = {str(x) for x in scenario.excluded_recurring_ids}
        retained = []
        for f in flows:
            if f.recurring_id in excluded or any(f.id.startswith(rid + ":") for rid in excluded):
                continue
            if f.kind == "income" or f.kind == "allocation" and ":rule:" in f.id:
                f.date = (
                    date.fromisoformat(f.date) + timedelta(days=scenario.income_delay_days)
                ).isoformat()
                f.amount = float(money(Decimal(str(f.amount)) * scenario.income_multiplier))
            elif f.kind == "expense":
                f.amount = float(money(Decimal(str(f.amount)) * scenario.expense_multiplier))
            if date.fromisoformat(f.date) <= end:
                retained.append(f)
        flows = retained
    rules = await list_settings(session, workspace_id, "expense_rule")
    extra, periods, warnings = await expense_flows(
        session,
        workspace_id,
        rules,
        start,
        end,
        flows,
        rates,
        expense_multiplier=scenario.expense_multiplier if scenario else Decimal(1),
    )
    non_income_ids = set()
    for flow in flows:
        flow.cash_role = (
            "income"
            if flow.kind == "income"
            else "financing_or_transfer"
            if flow.kind == "investment"
            else flow.kind
        )
        if flow.to_account_id and flow.kind in ("income", "investment"):
            for rule in rules:
                if (
                    rule.enabled
                    and rule.start_date <= date.fromisoformat(flow.date)
                    and (not rule.end_date or date.fromisoformat(flow.date) <= rule.end_date)
                    and flow.to_account_id
                    in {str(a) for a in (rule.actual_account_ids or [rule.account_id])}
                    and flow.category_id in {str(c) for c in rule.category_ids}
                ):
                    flow.cash_role = rule.credit_kind
                    if rule.credit_kind != "income":
                        non_income_ids.add(flow.id)
                        flow.kind = "investment"
                    break
    flows = [
        f
        for f in flows
        if not (
            f.kind == "allocation" and any(f.id.startswith(i + ":rule:") for i in non_income_ids)
        )
    ]
    if scenario:
        # Allocation percentages are evaluated on the delayed receipt date, not on its old phase.
        flows = [f for f in flows if not (f.kind == "allocation" and ":rule:" in f.id)]
        for income in list(flows):
            if income.kind != "income":
                continue
            on = date.fromisoformat(income.date)
            cumulative = Decimal(0)
            previous = Decimal(0)
            for account in accounts:
                if (
                    str(account.account_id) != income.to_account_id
                    or account.currency != income.currency
                ):
                    continue
                for rule in account.income_rules:
                    if not rule.start_date <= on or rule.end_date and on > rule.end_date:
                        continue
                    cumulative += rule.percentage
                    part = money(Decimal(str(income.amount)) * cumulative / 100) - previous
                    previous += part
                    flows.append(
                        MoneyMapFlow(
                            id=f"{income.id}:rule:{rule.id}",
                            date=income.date,
                            kind="allocation",
                            status="projected",
                            description=f"{rule.percentage}% · {income.description}",
                            from_account_id=income.to_account_id,
                            to_account_id=income.to_account_id,
                            to_pocket_id=str(rule.pocket_id),
                            amount=float(part),
                            currency=income.currency,
                            recurring_id=income.recurring_id,
                        )
                    )
        # Scheduled cross-currency transfers keep their recipient target under FX stress.
        for f in flows:
            if (
                f.kind == "transfer"
                and f.received_currency
                and f.received_currency != f.currency
                and f.received_amount is not None
            ):
                if rates.get(f.received_currency) is not None and rates.get(f.currency) is not None:
                    f.amount = float(
                        money(
                            Decimal(str(f.received_amount))
                            * cast(Decimal, rates[f.received_currency])
                            / cast(Decimal, rates[f.currency])
                        )
                    )
    flows.extend(extra)
    pocket_rules = [r for a in accounts for r in a.income_rules]
    for pid in {r.pocket_id for r in pocket_rules}:
        matching = [r for r in pocket_rules if r.pocket_id == pid]
        if any(r.end_date is None for r in matching):
            continue
        stopped = max(r.end_date for r in matching if r.end_date is not None)
        if start <= stopped < end:
            warnings.append(
                ForecastWarning(
                    code="allocation_rule_ends",
                    date=stopped,
                    detail=next(
                        (p.name for a in accounts for p in a.items if p.id == pid), str(pid)
                    ),
                )
            )
    covered_categories = {str(cid) for r in rules if r.enabled for cid in r.category_ids}
    categories = (
        await session.scalars(select(Category).where(Category.workspace_id == workspace_id))
    ).all()
    # A pocket-matched rule can use a budget amount without consuming that
    # category's historical purchases. Its budget still funds ONE expense envelope.
    for rule in rules:
        if (
            rule.enabled
            and rule.mode == "budget"
            and rule.budget_id
            and rule.start_date <= end
            and (not rule.end_date or rule.end_date >= start)
        ):
            source_budget = await session.get(Budget, rule.budget_id)
            if source_budget:
                covered_categories.add(str(source_budget.category_id))
    covered_names = {c.name for c in categories if str(c.id) in covered_categories}
    for gap in data.budget_gaps:
        if gap.category not in covered_names:
            # Exact budget/category IDs are checked below; a warning never adds fake money.
            warnings.append(
                ForecastWarning(
                    code="uncovered_budget",
                    date=date.fromisoformat(gap.month),
                    amount=money(gap.amount),
                    currency=gap.currency,
                    detail=gap.category,
                )
            )
    if not rules:
        warnings.append(
            ForecastWarning(
                code="no_expense_rules",
                detail="Budgets have not been connected to future purchases",
            )
        )
    for currency, rate in rates.items():
        if rate is None:
            warnings.append(
                ForecastWarning(
                    code="missing_fx", currency=currency, detail="No saved conversion rate"
                )
            )
    timeline, events, goals, simulated_warnings = simulate(
        accounts, flows, data.goals, rates, start, end, scenario=scenario
    )
    warnings.extend(simulated_warnings)
    warnings.extend(overdue)
    if data.unpaired_transfers:
        warnings.append(
            ForecastWarning(code="unpaired_transfers", detail=str(data.unpaired_transfers))
        )
    if request.account_ids is not None:
        visible = {str(x) for x in request.account_ids}
        if not visible <= {str(a.account_id) for a in accounts}:
            raise ValueError("Unknown account selection")
        # Simulation is global: filter the display only, never invent external income.
        for point in timeline:
            point["accounts"] = {a: v for a, v in point["accounts"].items() if a in visible}
    return FundingForecast(
        start_date=start,
        end_date=end,
        currency=base.meta.currency,
        scenario=scenario,
        rates=rate_rows,
        opening_accounts=[a.model_dump(mode="json") for a in accounts],
        timeline=timeline,
        events=events,
        goals=goals,
        warnings=warnings,
        budget_periods=periods,
        history_recommendations=await history_recommendations(session, workspace_id, rules, start),
        monthly_plan=monthly_income_plan(
            accounts, events, periods, rules, rates, start, end, goals=goals
        ),
        ending_balance=Decimal(timeline[-1]["balance"])
        if timeline[-1]["balance"] is not None
        else None,
        free_funds=Decimal(timeline[-1]["free_funds"])
        if timeline[-1]["free_funds"] is not None
        else None,
    )


def monthly_income_plan(accounts, events, periods, rules, rates, start, end, *, goals=()):
    """Income envelopes for complete future months, using the shared plan's inputs.

    Full budget cycles are attributed to their start month. This is an income
    allocation view, not another bank balance or proof of dated funding.
    """
    pockets = {str(p.id): p for account in accounts for p in account.items}
    operating = {
        str(r.pocket_id)
        for r in rules
        if r.enabled
        and r.mode == "budget"
        and r.pocket_id
        and r.start_date <= end
        and (r.end_date is None or r.end_date >= start)
    }
    operating_goals = {
        str(pockets[pid].goal_id) for pid in operating if pid in pockets and pockets[pid].goal_id
    }
    income_rules = {
        str(r.id): r
        for account in accounts
        for r in account.income_rules
        if r.end_date is None or r.end_date >= start
    }
    saving_ids = {
        str(r.pocket_id)
        for r in income_rules.values()
        if str(r.pocket_id) in pockets
        and str(r.pocket_id) not in operating
        and str(pockets[str(r.pocket_id)].goal_id) not in operating_goals
    }
    saving_goals = {str(pockets[pid].goal_id) for pid in saving_ids if pockets[pid].goal_id}
    saving_ids.update(
        pid for pid, p in pockets.items() if p.goal_id and str(p.goal_id) in saving_goals
    )
    # Keep unlinked saving envelopes visible even without a scheduled deposit.
    saving_ids.update(
        pid
        for pid, p in pockets.items()
        if not p.goal_id and pid not in operating and not p.archived
    )
    goal_names = {g.goal_id: g.name for g in goals}
    destinations = {}
    pocket_keys = {}
    for pid in sorted(saving_ids):
        pocket = pockets[pid]
        key = f"goal:{pocket.goal_id}" if pocket.goal_id else f"pocket:{pid}"
        pocket_keys[pid] = key
        destinations.setdefault(
            key,
            dict(
                id=key,
                name=goal_names.get(str(pocket.goal_id), pocket.name),
                goal_id=str(pocket.goal_id) if pocket.goal_id else None,
            ),
        )
    event_by_id = {e["id"]: e for e in events}
    covered = {fid for period in periods for fid in period.get("committed_flow_ids", [])}
    expense_rules = {str(rule.id): rule for rule in rules}
    display_currencies = sorted(set(rates) | {a.currency for a in accounts})

    def valuations(parts):
        values = {}
        for currency in display_currencies:
            total = Decimal(0)
            for amount, native in parts:
                amount = Decimal(str(amount))
                if not amount or native == currency:
                    total += amount
                elif rates.get(native) is None or rates.get(currency) is None:
                    values[currency] = None
                    break
                else:
                    total += amount * rates[native] / rates[currency]
            else:
                values[currency] = str(money(total))
        return values

    def total(rows, currency):
        values = [row["values"].get(currency) for row in rows]
        return (
            money(sum((Decimal(v) for v in values if v is not None), Decimal(0)))
            if all(v is not None for v in values)
            else None
        )

    result = []
    cursor = _advance_months(start.replace(day=1), 1, 1)
    while cursor <= end:
        last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        if last > end:
            break
        month_events = [e for e in events if cursor.isoformat() <= e["date"] <= last.isoformat()]
        incomes, costs, payments = [], [], []
        contributions = {key: {**item, "sources": []} for key, item in destinations.items()}
        for period in periods:
            if period["start"][:7] != cursor.isoformat()[:7]:
                continue
            rule = expense_rules.get(period["rule_id"])
            amount = Decimal(period["target"]) + Decimal(period["exceeded"])
            costs.append(
                dict(
                    id=f"budget:{period['rule_id']}:{period['start']}",
                    name=period["name"],
                    start=period["start"],
                    end=period["end"],
                    amount=str(amount),
                    currency=period["currency"],
                    rule_id=period["rule_id"],
                    budget_id=str(rule.budget_id) if rule and rule.budget_id else None,
                    account_id=str(rule.account_id) if rule else None,
                    pocket_id=str(rule.pocket_id) if rule and rule.pocket_id else None,
                    values=valuations([(amount, period["currency"])]),
                )
            )
        for event in month_events:
            kind, pid = event["kind"], event.get("from_pocket_id")
            item = dict(
                id=event["id"],
                name=event["description"],
                date=event["date"],
                amount=event["amount"],
                currency=event["currency"],
                account_id=event.get("from_account_id"),
                pocket_id=pid,
                values=valuations([(event["amount"], event["currency"])]),
            )
            if (
                kind == "income"
                and event.get("cash_role", "income") == "income"
                and not event["from_account_id"]
            ):
                incomes.append({**item, "account_id": event["to_account_id"]})
            elif (
                kind == "allocation"
                and event.get("to_pocket_id") in pocket_keys
                and pid not in saving_ids
            ):
                marker = event["id"].rfind(":rule:")
                income = event_by_id.get(event["id"][:marker]) if marker >= 0 else None
                rule = income_rules.get(event["id"][marker + 6 :]) if marker >= 0 else None
                if not (
                    income
                    and income["kind"] == "income"
                    and income["to_account_id"] == event["from_account_id"]
                ):
                    income = None
                if rule and str(rule.pocket_id) != event.get("to_pocket_id"):
                    rule = None
                contributions[pocket_keys[event["to_pocket_id"]]]["sources"].append(
                    {
                        **item,
                        "income_id": income["id"] if income else None,
                        "income_name": income["description"] if income else None,
                        "income_amount": income["amount"] if income else None,
                        "income_currency": income["currency"] if income else None,
                        "account_id": event["from_account_id"],
                        "pocket_id": event["to_pocket_id"],
                        "percentage": str(rule.percentage) if rule else None,
                        "funded": event.get("funded"),
                        "shortfall": event.get("shortfall"),
                    }
                )
            elif (
                kind == "expense"
                or kind == "investment"
                and event["from_account_id"]
                and not event["to_account_id"]
            ):
                if pid in saving_ids:
                    payments.append(item)
                elif event["id"] not in covered and not event["id"].startswith("budget:"):
                    costs.append(item)
        contribution_rows = list(contributions.values())
        for row in contribution_rows:
            row["values"] = valuations([(s["amount"], s["currency"]) for s in row["sources"]])
        totals = {}
        for currency in display_currencies:
            income, living, saved, payments_total = (
                total(rows, currency) for rows in [incomes, costs, contribution_rows, payments]
            )
            requirements = (
                money(living + saved) if living is not None and saved is not None else None
            )
            remaining = (
                money(income - requirements)
                if income is not None and requirements is not None
                else None
            )
            totals[currency] = {
                key: str(value) if value is not None else None
                for key, value in dict(
                    income=income,
                    living=living,
                    contributions=saved,
                    requirements=requirements,
                    remaining=remaining,
                    saved_payments=payments_total,
                ).items()
            }
        result.append(
            dict(
                month=cursor.isoformat()[:7],
                start=cursor.isoformat(),
                end=last.isoformat(),
                incomes=incomes,
                costs=costs,
                contributions=contribution_rows,
                saved_payments=payments,
                totals=totals,
            )
        )
        cursor = _advance_months(cursor, 1, 1)
    return result


async def history_recommendations(session, workspace_id, rules, today):
    from statistics import median

    results = []
    for rule in rules:
        if rule.mode == "payments_only":
            continue
        last_end = cycle_start(today, rule.cycle_day) - timedelta(days=1)
        first = _advance_months(last_end + timedelta(days=1), -6, rule.cycle_day)
        if (
            not rule.reviewed_history
            or not rule.history_from
            or not rule.history_through
            or rule.history_from > first
            or rule.history_through < last_end
        ):
            results.append(
                dict(
                    rule_id=str(rule.id),
                    status="unavailable",
                    reason="Six complete periods and reviewed exceptional transactions are required",
                )
            )
            continue
        rows = (
            await session.scalars(
                select(Transaction).where(
                    Transaction.workspace_id == workspace_id,
                    Transaction.account_id.in_(rule.actual_account_ids or [rule.account_id]),
                    Transaction.date >= first,
                    Transaction.date <= last_end,
                    Transaction.status == "posted",
                    Transaction.is_ignored == False,
                    Transaction.exclude_from_pnl == False,
                    Transaction.transfer_pair_id.is_(None),
                    Transaction.recurring_transaction_id.is_(None),
                    Transaction.source != "opening_balance",
                    Transaction.currency == rule.currency,
                )
            )
        ).all()
        months = []
        cursor = first
        for _ in range(6):
            following = _advance_months(cursor, 1, rule.cycle_day)
            matched = [t for t in rows if cursor <= t.date < following and matches(rule, t)]
            amount = max(
                Decimal(0),
                sum(
                    (
                        t.amount
                        if t.type == "debit"
                        else -t.amount
                        if rule.credit_kind in ("refund", "reimbursement")
                        else Decimal(0)
                        for t in matched
                    ),
                    Decimal(0),
                ),
            )
            months.append(
                dict(
                    start=cursor.isoformat(),
                    end=(following - timedelta(days=1)).isoformat(),
                    amount=str(money(amount)),
                    transactions=len(matched),
                )
            )
            cursor = following
        values = [Decimal(m["amount"]) for m in months]
        results.append(
            dict(
                rule_id=str(rule.id),
                status="available",
                currency=rule.currency,
                median=str(money(median(values))),
                minimum=str(min(values)),
                maximum=str(max(values)),
                periods=months,
                reason="Verified range; recurring payments and own transfers excluded. Advisory only.",
            )
        )
    return results


async def as_cash_flow_report(session, workspace_id, user_id, request, interval="daily"):
    from app.schemas.report import (
        ReportResponse,
        ReportSummary,
        ReportBreakdown,
        ReportMeta,
        ReportDataPoint,
    )

    result = await get_forecast(session, workspace_id, user_id, request)
    rates = {r["currency"]: Decimal(r["rate"]) if r["rate"] else None for r in result.rates}
    visible = {str(a) for a in request.account_ids} if request.account_ids is not None else None
    initial_parts = [
        money(Decimal(a["balance"]) * cast(Decimal, rates[a["currency"]]))
        if rates.get(a["currency"]) is not None
        else Decimal(0)
        if Decimal(a["balance"]) == 0
        else None
        for a in result.opening_accounts
        if visible is None or a["account_id"] in visible
    ]
    initial = (
        sum((p for p in initial_parts if p is not None), Decimal(0))
        if all(p is not None for p in initial_parts)
        else None
    )
    points = []
    for point in result.timeline:
        inflow = outflow = Decimal(0)
        for event in result.events:
            if event["date"] != point["date"]:
                continue
            source, dest = event["from_account_id"], event["to_account_id"]
            amount = (
                Decimal(event["amount"]) * cast(Decimal, rates[event["currency"]])
                if rates.get(event["currency"]) is not None
                else None
            )
            if amount is None:
                continue
            if dest and (
                visible is None
                and source is None
                and event["kind"] != "transfer"
                or visible is not None
                and dest in visible
                and source not in visible
            ):
                inflow += amount
            if source and (
                visible is None
                and dest is None
                and event["kind"] != "transfer"
                or visible is not None
                and source in visible
                and dest not in visible
            ):
                outflow += amount
        value = Decimal(point["balance"]) if point["balance"] is not None else None
        if visible is not None:
            parts = [
                Decimal(v)
                * cast(
                    Decimal,
                    rates[
                        next(
                            a["currency"] for a in result.opening_accounts if a["account_id"] == aid
                        )
                    ],
                )
                if v is not None
                and rates.get(
                    next(a["currency"] for a in result.opening_accounts if a["account_id"] == aid)
                )
                is not None
                else Decimal(0)
                if v == "0.00"
                else None
                for aid, v in point["accounts"].items()
                if aid in visible
            ]
            value = (
                sum((p for p in parts if p is not None), Decimal(0))
                if all(v is not None for v in parts)
                else None
            )
        points.append(
            ReportDataPoint(
                date=point["date"],
                value=float(value or 0),
                breakdowns={
                    "inflow": float(money(inflow)),
                    "outflow": float(money(outflow)),
                    **(
                        {"freeFunds": float(point["free_funds"])}
                        if point["free_funds"] is not None
                        else {}
                    ),
                },
            )
        )
    if interval != "daily":
        grouped = {}
        for point in points:
            on = date.fromisoformat(point.date)
            key = (
                point.date[:7]
                if interval == "monthly"
                else (on - timedelta(days=on.weekday())).isoformat()
            )
            old = grouped.get(key)
            if old:
                point.breakdowns["inflow"] += old.breakdowns["inflow"]
                point.breakdowns["outflow"] += old.breakdowns["outflow"]
            grouped[key] = point
        points = list(grouped.values())
    ending = points[-1].value
    return ReportResponse(
        summary=ReportSummary(
            primary_value=ending,
            change_amount=round(ending - float(initial), 2) if initial is not None else 0,
            change_percent=None,
            breakdowns=[
                ReportBreakdown(
                    key="startingBalance",
                    label="Starting balance",
                    value=float(initial or 0),
                    color="#6366F1",
                ),
                ReportBreakdown(
                    key="freeFunds",
                    label="Free funds",
                    value=float(result.free_funds or 0),
                    color="#10B981",
                ),
            ],
        ),
        trend=points,
        meta=ReportMeta(
            type="cash_flow",
            series_keys=["freeFunds"],
            currency=result.currency,
            interval=interval,
            forecast_start_date=result.start_date.isoformat(),
        ),
        funding=result,
    )


async def as_money_map_report(
    session,
    workspace_id,
    user_id,
    *,
    months=3,
    start_date=None,
    end_date=None,
    scenario_id=None,
    account_ids=None,
    **kwargs,
):
    from app.schemas.report import ReportSummary, ReportBreakdown

    result = await get_forecast(
        session,
        workspace_id,
        user_id,
        ForecastRequest(
            months=months, end_date=end_date, scenario_id=scenario_id, account_ids=account_ids
        ),
    )
    selected_start = start_date or result.start_date
    if not result.start_date <= selected_start <= result.end_date:
        raise ValueError("Plan range must begin within the forecast horizon")
    base = await money_map_service.get_money_map(
        session,
        workspace_id,
        user_id,
        mode="plan",
        months=months,
        start_date=selected_start,
        end_date=result.end_date,
        account_ids=account_ids,
        include_expense_rules=False,
    )
    data = base.money_map
    assert data is not None
    names = {str(a["account_id"]): a["name"] for a in result.opening_accounts}
    rates = {r["currency"]: Decimal(r["rate"]) if r["rate"] else None for r in result.rates}
    rows = [f for f in data.flows if f.status == "posted"]
    for event in result.events:
        if date.fromisoformat(event["date"]) < selected_start:
            continue
        amount = (
            Decimal(event["funded"] or "0")
            if event["kind"] == "allocation"
            else Decimal(event["amount"])
        )
        rate = rates.get(event["currency"])
        rows.append(
            MoneyMapFlow(
                **{k: v for k, v in event.items() if k in MoneyMapFlow.model_fields},
                from_account_name=names.get(event["from_account_id"]),
                to_account_name=names.get(event["to_account_id"]),
                amount_primary=float(money(amount * rate)) if rate is not None else None,
            )
        )
        rows[-1].amount = float(amount)
    data.flows = rows
    data.budget_gaps = []
    totals = {
        kind: sum((Decimal(str(f.amount_primary or 0)) for f in rows if f.kind == kind), Decimal(0))
        for kind in ["income", "expense", "transfer", "allocation"]
    }
    # Cash returns offset purchases; principal financing remains outside P&L.
    totals["expense"] -= sum(
        Decimal(str(f.amount_primary or 0))
        for f in rows
        if f.cash_role in ("refund", "reimbursement") and f.to_account_id and not f.from_account_id
    )
    base.summary = ReportSummary(
        primary_value=float(money(totals["income"] - totals["expense"])),
        change_amount=0,
        change_percent=None,
        breakdowns=[
            ReportBreakdown(key=k, label=k.title(), value=float(money(v)), color=c)
            for (k, v), c in zip(totals.items(), ["#10B981", "#F43F5E", "#3B82F6", "#8B5CF6"])
        ],
    )
    base.funding = result
    return base
