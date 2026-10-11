"""Scheduled transfers reuse native transfer pairs; forecasts never post money."""
import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from app.core.app_clock import get_workspace_timezone, today_in
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer_schedule import TransferSchedule
from app.schemas.transfer_schedule import TransferScheduleCreate, TransferConfirm
from app.schemas.transaction import TransferCreate
from app.services import pocket_service, transaction_service
from app.services.fx_rate_service import _resolve_rate
from app.services.recurring_transaction_service import _advance_months


def occurrences(plan, start: date, end: date):
    """Nominal dates in [start, end), preserving the requested month day."""
    if getattr(plan, "amount_mode", "fixed") == "prorated_weekly":
        current = max(start, plan.start_date)
        while current < end and (plan.end_date is None or current <= plan.end_date):
            anchor = _advance_months(current, 0, plan.cycle_day)
            if current.weekday() == plan.weekday or current == anchor or current == plan.start_date:
                yield current
            current += timedelta(days=1)
        return
    current = plan.start_date
    if plan.frequency == "months":
        day = plan.day_of_month or current.day
        current = _advance_months(current, 0, day)
        if current < plan.start_date:
            current = _advance_months(current, plan.interval, day)
        month_gap = (start.year - current.year) * 12 + start.month - current.month
        if month_gap > 0:
            current = _advance_months(current, (month_gap // plan.interval) * plan.interval, day)
    else:
        day_gap = (start - current).days
        if day_gap > 0:
            current += timedelta(days=(day_gap // plan.interval) * plan.interval)
    while current < end and (plan.end_date is None or current <= plan.end_date):
        if current >= start:
            yield current
        current = (current + timedelta(days=plan.interval) if plan.frequency == "days"
                   else _advance_months(current, plan.interval, day))


async def list_schedules(session, workspace_id):
    return list((await session.scalars(select(TransferSchedule).where(
        TransferSchedule.workspace_id == workspace_id,
    ).order_by(TransferSchedule.start_date, TransferSchedule.id))).all())


async def save_schedule(session, workspace_id, user_id, data: TransferScheduleCreate, schedule_id=None):
    accounts = [await pocket_service.account(session, workspace_id, key) for key in
                (data.from_account_id, data.to_account_id)]
    if any(a.is_closed for a in accounts):
        raise ValueError("Choose open accounts")
    if data.currency not in {a.currency for a in accounts}:
        raise ValueError("The amount currency must match one of the accounts")
    for acc, pocket_id in sorted(zip(accounts, (data.from_pocket_id, data.to_pocket_id)), key=lambda item: str(item[0].id)):
        await pocket_service.validate_transaction(session, Transaction(
            workspace_id=workspace_id, account_id=acc.id, pocket_id=pocket_id,
            currency=acc.currency, date=data.start_date,
        ))
    if schedule_id:
        plan = await session.scalar(select(TransferSchedule).where(
            TransferSchedule.id == schedule_id, TransferSchedule.workspace_id == workspace_id,
        ).with_for_update())
        if plan is None:
            raise ValueError("Transfer schedule not found")
        # Posted and Pending occurrences are snapshots, never rewritten by plan edits.
        for key, value in data.model_dump().items():
            setattr(plan, key, value)
    else:
        plan = TransferSchedule(workspace_id=workspace_id, user_id=user_id, **data.model_dump())
        session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


def occurrence_amount(plan, on):
    """Split a monthly envelope by calendar days; cumulative rounding keeps its exact total."""
    if getattr(plan, "amount_mode", "fixed") != "prorated_weekly":
        return plan.amount
    anchor = _advance_months(on, 0, plan.cycle_day)
    if anchor > on:
        anchor = _advance_months(anchor, -1, plan.cycle_day)
    next_anchor = _advance_months(anchor, 1, plan.cycle_day)
    amount = plan.amount
    initial_amount = getattr(plan, "initial_cycle_amount", None)
    if initial_amount is not None and anchor <= plan.start_date <= on < next_anchor:
        # A first, partially funded cycle uses the remaining envelope from start_date.
        anchor = plan.start_date
        amount = initial_amount
    days_to_weekday = (plan.weekday - on.weekday()) % 7 or 7
    until = min(on + timedelta(days=days_to_weekday), next_anchor)
    if plan.end_date is not None:
        until = min(until, plan.end_date + timedelta(days=1))
    days = (next_anchor - anchor).days
    def cumulative(d):
        return (amount * Decimal((d - anchor).days) / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return cumulative(until) - cumulative(on)


async def _amounts(session, plan, on, source, destination):
    rates = [await _resolve_rate(session, plan.currency, acc.currency, on, allow_fetch=False)
             for acc in (source, destination)]
    if any(rate is None for rate in rates):
        raise ValueError(f"Exchange rate unavailable for {plan.description}")
    return tuple((occurrence_amount(plan, on) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                 for rate in rates)


async def projections(session, workspace_id, start, end, primary_currency, account_ids=None):
    """Shared two-sided forecasts for account tables, balances and calendar."""
    if account_ids == []:
        return []
    plans = [p for p in await list_schedules(session, workspace_id) if p.is_active]
    existing = set((await session.execute(select(
        Transaction.transfer_schedule_id, Transaction.transfer_occurrence_date,
    ).where(Transaction.workspace_id == workspace_id,
            Transaction.transfer_schedule_id.is_not(None)))).all())
    rows = []
    for plan in plans:
        source = await session.get(Account, plan.from_account_id)
        destination = await session.get(Account, plan.to_account_id)
        if not source or not destination or source.is_closed or destination.is_closed:
            continue
        for on in occurrences(plan, start, end):
            if (plan.id, on) in existing:
                continue
            debit, credit = await _amounts(session, plan, on, source, destination)
            rate = await _resolve_rate(session, plan.currency, primary_currency, on, allow_fetch=False)
            primary = float((occurrence_amount(plan, on) * rate).quantize(Decimal("0.01"))) if rate is not None else None
            pair_id = uuid.uuid5(plan.id, on.isoformat())
            for acc, amount, kind in ((source, debit, "debit"), (destination, credit, "credit")):
                if account_ids is not None and acc.id not in account_ids:
                    continue
                rows.append(dict(recurring_id=str(plan.id), account_id=str(acc.id),
                    account_name=acc.display_name or acc.name, description=plan.description,
                    amount=float(amount), amount_primary=primary, currency=acc.currency,
                    type=kind, date=on.isoformat(), category_id=None, category_name=None,
                    category_icon=None, category_color=None, transfer_pair_id=str(pair_id)))
    return rows


async def materialize(session, workspace_id, schedule_id, on):
    plan = await session.scalar(select(TransferSchedule).where(
        TransferSchedule.id == schedule_id, TransferSchedule.workspace_id == workspace_id,
    ).with_for_update())
    if not plan or not plan.is_active:
        raise ValueError("Active transfer schedule not found")
    if not list(occurrences(plan, on, on + timedelta(days=1))):
        raise ValueError("Date is not an occurrence of this schedule")
    existing = list((await session.scalars(select(Transaction).where(
        Transaction.transfer_schedule_id == plan.id, Transaction.transfer_occurrence_date == on,
    ).order_by(Transaction.type))).all())
    if existing:
        return existing
    source = await session.get(Account, plan.from_account_id)
    destination = await session.get(Account, plan.to_account_id)
    if not source or not destination or source.is_closed or destination.is_closed:
        raise ValueError("Choose open accounts")
    debit, credit = await _amounts(session, plan, on, source, destination)
    pair = await transaction_service.create_transfer(session, workspace_id, plan.user_id, TransferCreate(
        from_account_id=plan.from_account_id, to_account_id=plan.to_account_id,
        from_pocket_id=plan.from_pocket_id, to_pocket_id=plan.to_pocket_id,
        amount=debit, destination_amount=credit if source.currency != destination.currency else None,
        date=on, description=plan.description, notes=plan.notes, status="pending",
    ), commit=False)
    for tx in pair:
        tx.transfer_schedule_id = plan.id
        tx.transfer_occurrence_date = on
    await session.commit()
    return pair


async def generate_pending(session, user_id):
    count = 0
    plans = list((await session.scalars(select(TransferSchedule).where(
        TransferSchedule.user_id == user_id, TransferSchedule.is_active.is_(True),
    ))).all())
    for plan in plans:
        cutoff = today_in(await get_workspace_timezone(session, plan.workspace_id))
        existing = set((await session.scalars(select(Transaction.transfer_occurrence_date).where(
            Transaction.transfer_schedule_id == plan.id,
        ))).all())
        for on in occurrences(plan, plan.start_date, cutoff + timedelta(days=1)):
            if on not in existing:
                await materialize(session, plan.workspace_id, plan.id, on)
                count += 1
    return count


async def confirm(session, workspace_id, user_id, pair_id, data: TransferConfirm):
    return await _update_pair(session, workspace_id, user_id, pair_id, data, "posted")


async def update_pending(session, workspace_id, user_id, pair_id, data):
    return await _update_pair(session, workspace_id, user_id, pair_id, data, "pending")


async def _update_pair(session, workspace_id, user_id, pair_id, data, status):
    pair = list((await session.scalars(select(Transaction).where(
        Transaction.workspace_id == workspace_id, Transaction.transfer_pair_id == pair_id,
    ).order_by(Transaction.id).with_for_update())).all())
    if len(pair) != 2 or {tx.type for tx in pair} != {"debit", "credit"}:
        raise ValueError("Transfer pair not found")
    debit = next(tx for tx in pair if tx.type == "debit")
    credit = next(tx for tx in pair if tx.type == "credit")
    if debit.currency == credit.currency and data.amount != data.destination_amount:
        raise ValueError("Same-currency transfer amounts must match")
    if all(tx.status == "posted" for tx in pair):
        if status == "pending":
            raise ValueError("Cannot edit a Posted transfer through the Pending action")
        if debit.amount == data.amount and credit.amount == data.destination_amount and debit.date == data.date:
            return pair
        raise ValueError("Transfer already confirmed; edit the transaction instead")
    if any(tx.status != "pending" for tx in pair):
        raise ValueError("Both transfer legs must be Pending")
    for account_id in sorted({tx.account_id for tx in pair}, key=str):
        await pocket_service.account(session, workspace_id, account_id, lock=True)
    # Native editing helpers preserve both observed amounts and effective dates.
    for tx, amount in ((debit, data.amount), (credit, data.destination_amount)):
        tx.amount = amount
        tx.date = data.date
        tx.status = status
        if "notes" in data.model_fields_set:
            tx.notes = data.notes
        tx.transfer_amount_explicit = True
        from app.services.credit_card_service import apply_effective_date
        apply_effective_date(tx, await session.get(Account, tx.account_id))
        await pocket_service.validate_transaction(session, tx)
    from app.services.fx_rate_service import stamp_primary_amount
    await stamp_primary_amount(session, user_id, debit, allow_fetch=False)
    credit.amount_primary = debit.amount_primary
    credit.fx_rate_used = debit.amount_primary / credit.amount if debit.amount_primary is not None else None
    await session.commit()
    return pair


async def pending_pairs(session, workspace_id):
    rows = list((await session.scalars(select(Transaction).where(
        Transaction.workspace_id == workspace_id, Transaction.status == "pending",
        Transaction.transfer_pair_id.is_not(None),
    ).order_by(Transaction.date, Transaction.id))).all())
    pairs = {}
    for tx in rows:
        pair = pairs.setdefault(str(tx.transfer_pair_id), {"id": str(tx.transfer_pair_id)})
        acc = await session.get(Account, tx.account_id)
        pair[tx.type] = {"id": str(tx.id), "account": acc.display_name or acc.name,
            "amount": float(tx.amount), "currency": tx.currency,
            "date": tx.date.isoformat(), "description": tx.description}
    return [p for p in pairs.values() if "debit" in p and "credit" in p]


async def delete_pending_pair(session, workspace_id, pair_id, *, apply=False):
    """Remove an unexecuted one-off pair through the native transaction cleanup."""
    pair = list((await session.scalars(select(Transaction).where(
        Transaction.workspace_id == workspace_id, Transaction.transfer_pair_id == pair_id,
    ).order_by(Transaction.id).with_for_update())).all())
    if not pair:
        return []
    if len(pair) != 2 or {tx.type for tx in pair} != {"debit", "credit"}:
        raise ValueError("Transfer pair is incomplete")
    if any(tx.status != "pending" for tx in pair):
        raise ValueError("Only a Pending transfer can be removed here")
    if any(tx.transfer_schedule_id is not None for tx in pair):
        raise ValueError("Edit or pause the recurring plan; deleting its occurrence would recreate it")
    ids = [str(tx.id) for tx in pair]
    if apply:
        await transaction_service.delete_transaction(session, pair[0].id, workspace_id)
    return ids
