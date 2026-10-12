"""Account routes for Money Map. Reading a report never materializes bank entries."""
import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import get_workspace_timezone, today_in
from app.models.category import Category
from app.models.pocket import PocketMovement
from app.models.transaction import Transaction
from app.models.transfer_schedule import TransferSchedule
from app.models.user import User
from app.schemas.report import (
    MoneyMapAccount, MoneyMapBudgetGap, MoneyMapData, MoneyMapFlow,
    ReportBreakdown, ReportMeta, ReportResponse, ReportSummary,
)
from app.services import budget_service, dashboard_service, goal_service, pocket_service, report_service
from app.services.fx_rate_service import _resolve_rate
from app.services.recurring_transaction_service import _advance_months


async def get_money_map(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, *,
    mode: Literal["actual", "plan"] = "plan", months: int = 3,
    start_date: date | None = None, end_date: date | None = None,
    account_ids: list[uuid.UUID] | None = None, days: int | None = None,
    period: str | None = None, financial_year_start_month: int = 1,
    include_expense_rules: bool = True,
) -> ReportResponse:
    if mode not in {"actual", "plan"} or not 1 <= months <= 24:
        raise ValueError("Invalid money map mode or months")
    if (start_date is None) != (end_date is None):
        raise ValueError("Provide both start_date and end_date")
    timezone = await get_workspace_timezone(session, workspace_id)
    today = today_in(timezone)
    start = start_date or (today if mode == "plan" else report_service._report_start_date(
        today, months, days=days, period=period, financial_year_start_month=financial_year_start_month))
    end = end_date or (_advance_months(today, months, today.day) - timedelta(days=1) if mode == "plan" else today)
    if start > end or end > _advance_months(start, 24, start.day):
        raise ValueError("Choose an ordered range of at most 24 months")
    if mode == "actual" and end > today:
        raise ValueError("Actual reports cannot end in the future")
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    primary = user.primary_currency
    accounts = await dashboard_service._get_open_accounts(session, workspace_id)
    by_id = {str(a.id): a for a in accounts}
    visible = {str(a.id) for a in accounts if account_ids is None or a.id in account_ids}
    names = {key: a.display_name or a.name for key, a in by_id.items()}
    pocket_accounts = []
    rules = []
    for key in sorted(visible, key=lambda key: names[key]):
        acc = by_id[key]
        # Legacy connected accounts may belong to a connection's workspace.
        if acc.workspace_id != workspace_id:
            balance = Decimal(str(await dashboard_service._account_balance_at(session, acc, today)))
            pocket_accounts.append(MoneyMapAccount(account_id=acc.id, name=names[key], account_type=acc.type, currency=acc.currency,
                balance=balance, unallocated=balance, unallocated_available=balance, needs_review=False, items=[]))
            continue
        snapshot = await pocket_service.get_account_pockets(session, workspace_id, acc.id)
        pocket_accounts.append(MoneyMapAccount(name=names[key], account_type=acc.type, **snapshot.model_dump()))
        rules.extend(snapshot.income_rules)

    rates: dict[str, Decimal | None] = {primary: Decimal(1)}
    missing_rates: set[str] = set()
    async def value(amount: Decimal | float, currency: str, stamped: Decimal | float | None = None) -> float | None:
        if currency == primary:
            return float(amount)
        if stamped is not None:
            return float(abs(stamped))
        if currency not in rates:
            rates[currency] = await _resolve_rate(session, currency, primary, allow_fetch=False)
        rate = rates[currency]
        if rate is None:
            missing_rates.add(currency)
            return None
        return float((Decimal(str(amount)) * rate).quantize(Decimal("0.01")))

    window = (Transaction.date >= start) & (Transaction.date <= end)
    if mode == "plan":
        window = window | ((Transaction.status == "pending") & (Transaction.date < start))
        pair_ids = select(Transaction.transfer_pair_id).where(
            Transaction.workspace_id == workspace_id, window, Transaction.transfer_pair_id.is_not(None))
        window = window | Transaction.transfer_pair_id.in_(pair_ids)
    txs = (await session.scalars(select(Transaction).options(selectinload(Transaction.category)).where(
        Transaction.workspace_id == workspace_id, window, Transaction.is_ignored == False,
        Transaction.source.not_in(["opening_balance", "settlement"]),
    ))).all()
    rows: list[dict[str, Any]] = []
    for tx in txs:
        if str(tx.account_id) not in by_id or (tx.category and tx.category.is_ignored):
            continue
        status = tx.status if tx.date <= today else "pending"
        if mode == "actual" and status != "posted":
            continue
        rows.append(dict(id=str(tx.id), date=tx.date.isoformat(), account_id=str(tx.account_id),
            description=tx.description, type=tx.type, status=status, currency=tx.currency,
            amount=float(abs(tx.amount)), amount_primary=tx.amount_primary,
            pocket_id=str(tx.pocket_id) if tx.pocket_id else None,
            category_id=str(tx.category_id) if tx.category_id else None,
            category_name=tx.category.name if tx.category else None,
            transfer_pair_id=str(tx.transfer_pair_id) if tx.transfer_pair_id else None,
            recurring_id=str(tx.recurring_transaction_id) if tx.recurring_transaction_id else None,
            income_eligible=not (tx.category and tx.category.treat_as_transfer)))
    schedules = {str(p.id): p for p in (await session.scalars(select(TransferSchedule).where(
        TransferSchedule.workspace_id == workspace_id))).all()}
    if mode == "plan" and visible:
        projected = await dashboard_service.get_projected_transactions(session, workspace_id, user_id,
            from_date=start, to_date=end, allow_fx_fetch=False)
        categories = {str(c.id): c for c in (await session.scalars(select(Category).where(
            Category.workspace_id == workspace_id))).all()}
        for tx in projected:
            row = tx.model_dump()
            if tx.account_id not in by_id:
                continue
            plan = schedules.get(tx.recurring_id)
            pocket = (plan.from_pocket_id if tx.type == "debit" else plan.to_pocket_id) if plan else None
            category = categories.get(tx.category_id)
            rows.append(row | dict(id=f"{tx.recurring_id}:{tx.date}:{tx.type}", status="projected",
                pocket_id=str(pocket) if pocket else None,
                income_eligible=not (category and category.treat_as_transfer)))

    flows: list[MoneyMapFlow] = []
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("transfer_pair_id"):
            pairs.setdefault(row["transfer_pair_id"], {})[row["type"]] = row
            continue
        key = row["account_id"]
        if key not in visible:
            continue
        credit = row["type"] == "credit"
        amount_primary = await value(row["amount"], row["currency"], row["amount_primary"])
        flows.append(MoneyMapFlow(id=row["id"], date=row["date"],
            kind=("income" if credit else "expense") if row["income_eligible"] else "investment",
            status=row["status"], description=row["description"],
            from_account_id=None if credit else key, to_account_id=key if credit else None,
            from_account_name=None if credit else names[key], to_account_name=names[key] if credit else None,
            from_pocket_id=None if credit else row["pocket_id"], to_pocket_id=row["pocket_id"] if credit else None,
            amount=row["amount"], currency=row["currency"], amount_primary=amount_primary,
            recurring_id=row.get("recurring_id"), category_id=row["category_id"], category_name=row.get("category_name")))
        # Show the existing percentage rule as an earmark, never as spending.
        if credit and row["income_eligible"] and row["currency"] == by_id[key].currency:
            cumulative = Decimal(0)
            previous = Decimal(0)
            for rule in rules:
                on = date.fromisoformat(row["date"])
                if str(rule.account_id) != key or on < rule.start_date or (rule.end_date and on > rule.end_date):
                    continue
                cumulative += rule.percentage
                total = (Decimal(str(row["amount"])) * cumulative / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                part = total - previous
                previous = total
                flows.append(MoneyMapFlow(id=f"{row['id']}:rule:{rule.id}", date=row["date"],
                    kind="allocation", status="posted" if row["status"] == "posted" else "projected",
                    description=f"{rule.percentage}% · {row['description']}",
                    from_account_id=key, to_account_id=key, from_account_name=names[key], to_account_name=names[key],
                    from_pocket_id=row["pocket_id"], to_pocket_id=str(rule.pocket_id),
                    amount=float(part), currency=row["currency"], recurring_id=row.get("recurring_id"), amount_primary=await value(part, row["currency"])))
    unpaired_transfers = 0
    for pair_id, pair in pairs.items():
        debit, credit = pair.get("debit"), pair.get("credit")
        if not debit or not credit:
            # A broken pair must not masquerade as external income/expense.
            if any(row["account_id"] in visible for row in pair.values()):
                unpaired_transfers += 1
            continue
        if not {debit["account_id"], credit["account_id"]} & visible:
            continue
        flows.append(MoneyMapFlow(id=pair_id, date=debit["date"], kind="transfer", status=debit["status"],
            description=debit["description"], from_account_id=debit["account_id"], to_account_id=credit["account_id"],
            from_account_name=names[debit["account_id"]], to_account_name=names[credit["account_id"]],
            from_pocket_id=debit["pocket_id"], to_pocket_id=credit["pocket_id"],
            amount=debit["amount"], currency=debit["currency"],
            amount_primary=await value(debit["amount"], debit["currency"], debit["amount_primary"]),
            received_amount=credit["amount"], received_currency=credit["currency"], received_date=credit["date"], received_status=credit["status"]))
    if visible:
        movements = (await session.scalars(select(PocketMovement).where(
            PocketMovement.workspace_id == workspace_id,
            PocketMovement.account_id.in_([uuid.UUID(key) for key in visible]),
        ))).all()
        for movement in movements:
            on = movement.created_at.astimezone(timezone).date()
            if not start <= on <= end:
                continue
            key = str(movement.account_id)
            flows.append(MoneyMapFlow(id=str(movement.id), date=on.isoformat(), kind="allocation", status="posted",
                description="Pocket allocation", from_account_id=key, to_account_id=key,
                from_account_name=names[key], to_account_name=names[key],
                from_pocket_id=str(movement.from_pocket_id) if movement.from_pocket_id else None,
                to_pocket_id=str(movement.to_pocket_id) if movement.to_pocket_id else None,
                amount=float(movement.amount), currency=by_id[key].currency,
                amount_primary=await value(movement.amount, by_id[key].currency)))

    if mode == "plan" and include_expense_rules:
        from app.services import forecast_service
        expense_rules = await forecast_service.list_settings(session, workspace_id, "expense_rule")
        for rule in expense_rules:
            await value(0, rule.currency)
        budget_flows, _, _ = await forecast_service.expense_flows(session, workspace_id, expense_rules,
            max(start, today), end, [f for f in flows if f.status != "posted"], rates)
        flows.extend(budget_flows)

    linked_goals = {p.goal_id for a in pocket_accounts for p in a.items if p.goal_id}
    goals = [g for g in await goal_service.get_goals(session, workspace_id, user_id, allow_fx_fetch=False) if g.id in linked_goals]
    gaps = []
    if mode == "plan" and account_ids is None:
        covered = {(f.date[:7], f.category_id) for f in flows if f.kind == "expense"}
        cursor = start.replace(day=1)
        while cursor <= end:
            for budget in await budget_service.get_budgets(session, workspace_id, cursor):
                if (cursor.isoformat()[:7], str(budget.category_id)) in covered or budget.amount <= 0:
                    continue
                category = await session.get(Category, budget.category_id)
                if category and not category.is_ignored:
                    gaps.append(MoneyMapBudgetGap(month=cursor.isoformat(), category=category.name,
                        amount=float(budget.amount), currency=budget.currency or primary))
            cursor = _advance_months(cursor, 1, 1)
    totals = {kind: sum(f.amount_primary or 0 for f in flows if f.kind == kind)
              for kind in ["income", "expense", "transfer", "allocation"]}
    flows.sort(key=lambda flow: (flow.date, flow.id))
    return ReportResponse(
        summary=ReportSummary(primary_value=round(totals["income"]-totals["expense"], 2), change_amount=0,
            change_percent=None, breakdowns=[ReportBreakdown(key=kind, label=kind.title(), value=round(amount, 2),
                color=color) for (kind, amount), color in zip(totals.items(), ["#10B981", "#F43F5E", "#3B82F6", "#8B5CF6"])]),
        trend=[], meta=ReportMeta(type="money_map", series_keys=[], currency=primary, interval="monthly"),
        money_map=MoneyMapData(start_date=start.isoformat(), end_date=end.isoformat(), mode=mode,
            accounts=pocket_accounts, goals=goals, flows=flows,
            budget_gaps=gaps, missing_rates=sorted(missing_rates), unpaired_transfers=unpaired_transfers),
    )
