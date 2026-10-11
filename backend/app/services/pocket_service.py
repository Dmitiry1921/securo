"""Shared pocket accounting for HTTP and MCP.

Bank facts remain authoritative. A linked transaction contributes its live
amount exactly once; ignored/deleted entries contribute nothing. Incompatible
provider edits are visible as needs_review instead of invented conversions.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.goal import Goal
from app.models.category import Category
from app.models.pocket import Pocket, PocketMovement, PocketIncomeRule
from app.models.transaction import Transaction
from app.schemas.pocket import (PocketAccountRead, PocketCreate, PocketMove, PocketRead, PocketUpdate,
    PocketIncomeRuleCreate, PocketIncomeRuleRead)
from app.services.account_service import get_account_summary


async def account(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID, *, lock=False
) -> Account:
    query = select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise ValueError("Account not found")
    return row


async def _goal(session, workspace_id, goal_id):
    if goal_id is None:
        return
    goal = await session.get(Goal, goal_id)
    if not goal or goal.workspace_id != workspace_id or goal.tracking_type != "pockets":
        raise ValueError("Select a pocket-tracked goal in this workspace")


async def get_account_pockets(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> PocketAccountRead:
    today = app_today()
    acc = await account(session, workspace_id, account_id)
    pockets = list(
        (
            await session.scalars(
                select(Pocket)
                .where(
                    Pocket.workspace_id == workspace_id,
                    Pocket.account_id == account_id,
                )
                .order_by(Pocket.created_at, Pocket.id)
            )
        ).all()
    )
    balances = {p.id: Decimal(0) for p in pockets}
    reserved = dict(balances)
    invalid = set()
    movements = (
        await session.scalars(
            select(PocketMovement).where(
                PocketMovement.account_id == account_id,
                PocketMovement.workspace_id == workspace_id,
            )
        )
    ).all()
    for movement in movements:
        if movement.from_pocket_id is not None and movement.from_pocket_id in balances:
            balances[movement.from_pocket_id] -= movement.amount
        if movement.to_pocket_id is not None and movement.to_pocket_id in balances:
            balances[movement.to_pocket_id] += movement.amount
    by_id = {p.id: p for p in pockets}
    income_rules = list((await session.scalars(select(PocketIncomeRule).where(
        PocketIncomeRule.workspace_id == workspace_id, PocketIncomeRule.account_id == account_id,
    ).order_by(PocketIncomeRule.start_date, PocketIncomeRule.id))).all())
    ignored_categories = set(
        (
            await session.scalars(
                select(Category.id).where(
                    Category.workspace_id == workspace_id,
                    Category.is_ignored == True,
                )
            )
        ).all()
    )
    if by_id:
        rows = (
            await session.scalars(
                select(Transaction).where(
                    Transaction.pocket_id.in_(by_id),
                    Transaction.workspace_id == workspace_id,
                )
            )
        ).all()
        for tx in rows:
            assert tx.pocket_id is not None
            if tx.is_ignored or tx.category_id in ignored_categories:
                continue
            pocket = by_id[tx.pocket_id]
            if (
                tx.account_id != account_id
                or tx.currency != acc.currency
                or tx.date < pocket.start_date
                or tx.amount < 0
                or tx.type not in ("debit", "credit")
            ):
                invalid.add(pocket.id)
                continue
            # Future rows belong to the forecast, regardless of their status.
            # Only Pending payments already due reserve today's allocations.
            if tx.date > today:
                continue
            if tx.status == "pending":
                if tx.type == "debit":
                    reserved[pocket.id] += abs(tx.amount)
            elif tx.status == "posted":
                balances[pocket.id] += abs(tx.amount) * (-1 if tx.type == "debit" else 1)
    summary = await get_account_summary(session, account_id, workspace_id)
    assert summary is not None
    balance = Decimal(str(summary["current_balance"]))
    # Unassigned Pending payments already due reserve current money too.
    # Future plans do not commit today's funds or credit future income.
    unassigned_reserved = Decimal(0)
    native_balance_unknown = False
    account_transactions = (
        await session.scalars(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.workspace_id == workspace_id,
                Transaction.is_ignored == False,
            )
        )
    ).all()
    for tx in account_transactions:
        if tx.category_id in ignored_categories:
            continue
        if tx.currency != acc.currency:
            native_balance_unknown = True
        if (
            tx.pocket_id is None
            and tx.type == "debit"
            and tx.status == "pending"
            and tx.date <= today
        ):
            unassigned_reserved += abs(tx.amount)
    # Derived allocations reuse the income row itself as the source of truth.
    # Editing, ignoring or deleting that row adjusts its allocation exactly once;
    # reads never write movements or bank transactions. Closed rules retain history.
    if income_rules:
        from app.services._query_filters import counts_as_pnl, is_confirmed, is_not_future
        incomes = (await session.scalars(select(Transaction).where(
            Transaction.account_id == account_id, Transaction.workspace_id == workspace_id,
            Transaction.type == "credit", Transaction.amount > 0,
            Transaction.source.not_in(["opening_balance", "settlement"]),
            counts_as_pnl(), is_confirmed(), is_not_future(today),
        ))).all()
        for tx in incomes:
            if tx.currency != acc.currency:
                continue
            percentage = Decimal(0)
            allocated = Decimal(0)
            for rule in income_rules:
                if tx.date < rule.start_date or (rule.end_date and tx.date > rule.end_date):
                    continue
                percentage += rule.percentage
                total = (tx.amount * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                part = total - allocated
                allocated = total
                if tx.pocket_id is not None and tx.pocket_id in balances:
                    balances[tx.pocket_id] -= part
                balances[rule.pocket_id] += part
    invalid.update(p.id for p in pockets if p.archived_at and balances[p.id])
    unallocated = balance - sum(balances.values(), Decimal(0))
    return PocketAccountRead(
        account_id=account_id,
        currency=acc.currency,
        balance=balance,
        unallocated=unallocated,
        unallocated_available=unallocated - unassigned_reserved,
        needs_review=bool(invalid) or unallocated < 0 or native_balance_unknown,
        income_rules=[PocketIncomeRuleRead.model_validate(r) for r in income_rules],
        items=[
            PocketRead(
                id=p.id,
                account_id=p.account_id,
                goal_id=p.goal_id,
                name=p.name,
                currency=acc.currency,
                start_date=p.start_date,
                archived=p.archived_at is not None,
                balance=balances[p.id],
                reserved=reserved[p.id],
                available=balances[p.id] - reserved[p.id],
                needs_review=p.id in invalid,
            )
            for p in pockets
        ],
    )


async def create_pocket(session, workspace_id, user_id, data: PocketCreate):
    acc = await account(session, workspace_id, data.account_id, lock=True)
    if acc.is_closed or acc.type not in ("checking", "savings", "wallet"):
        raise ValueError("Pockets require an open checking, savings or wallet account")
    await _goal(session, workspace_id, data.goal_id)
    name = data.name.strip()
    if not name:
        raise ValueError("Pocket name is required")
    row = Pocket(
        workspace_id=workspace_id,
        account_id=acc.id,
        name=name,
        goal_id=data.goal_id,
        start_date=app_today(),
    )
    session.add(row)
    await session.commit()
    return await get_account_pockets(session, workspace_id, acc.id)


async def update_pocket(session, workspace_id, pocket_id, data: PocketUpdate):
    row = await session.get(Pocket, pocket_id)
    if not row or row.workspace_id != workspace_id:
        raise ValueError("Pocket not found")
    await account(session, workspace_id, row.account_id, lock=True)
    if "goal_id" in data.model_fields_set:
        await _goal(session, workspace_id, data.goal_id)
        row.goal_id = data.goal_id
    if data.name is not None:
        if not data.name.strip():
            raise ValueError("Pocket name is required")
        row.name = data.name.strip()
    if data.archived is not None:
        state = await get_account_pockets(session, workspace_id, row.account_id)
        item = next(p for p in state.items if p.id == row.id)
        pending = await session.scalar(
            select(Transaction.id)
            .where(
                Transaction.pocket_id == row.id,
                Transaction.is_ignored == False,
                (Transaction.status == "pending") | (Transaction.date > app_today()),
            )
            .limit(1)
        )
        income_rule = await session.scalar(select(PocketIncomeRule.id).where(
            PocketIncomeRule.pocket_id == row.id,
            (PocketIncomeRule.end_date.is_(None)) | (PocketIncomeRule.end_date >= app_today()),
        ).limit(1))
        if data.archived and (item.balance or item.reserved or item.needs_review or pending or income_rule):
            raise ValueError("Only empty pockets without pending transactions can be archived")
        row.archived_at = datetime.now(timezone.utc) if data.archived else None
    await session.commit()
    return await get_account_pockets(session, workspace_id, row.account_id)


async def move(session, workspace_id, user_id, data: PocketMove):
    acc = await account(session, workspace_id, data.account_id, lock=True)
    if acc.is_closed:
        raise ValueError("Account is closed")
    previous = (
        await session.scalars(
            select(PocketMovement).where(
                PocketMovement.account_id == acc.id,
                PocketMovement.idempotency_key == data.idempotency_key,
            )
        )
    ).first()
    if previous:
        if (previous.from_pocket_id, previous.to_pocket_id, previous.amount) != (
            data.from_pocket_id,
            data.to_pocket_id,
            data.amount,
        ):
            raise ValueError("Idempotency key already used for another movement")
        return await get_account_pockets(session, workspace_id, acc.id)
    state = await get_account_pockets(session, workspace_id, acc.id)
    items = {p.id: p for p in state.items}
    for pocket_id in (data.from_pocket_id, data.to_pocket_id):
        if pocket_id is not None and (
            pocket_id not in items or items[pocket_id].archived or items[pocket_id].needs_review
        ):
            raise ValueError(
                "Select an active pocket on this account without unresolved transactions"
            )
    available = (
        items[data.from_pocket_id].available if data.from_pocket_id else state.unallocated_available
    )
    if state.needs_review and data.from_pocket_id is None:
        raise ValueError("Resolve account shortfall or pocket review before allocating more funds")
    if data.amount > available:
        raise ValueError("Insufficient available funds")
    session.add(PocketMovement(workspace_id=workspace_id, created_by=user_id, **data.model_dump()))
    await session.commit()
    return await get_account_pockets(session, workspace_id, acc.id)


async def validate_transaction(session, transaction):
    """Explicit assignment only. Historical transactions aren't auto-allocated."""
    if transaction.pocket_id is None:
        return
    pocket = await session.get(Pocket, transaction.pocket_id)
    if (
        not pocket
        or pocket.workspace_id != transaction.workspace_id
        or pocket.account_id != transaction.account_id
    ):
        raise ValueError("Pocket must belong to the transaction account and workspace")
    acc = await account(session, transaction.workspace_id, transaction.account_id, lock=True)
    if pocket.archived_at or acc.is_closed:
        raise ValueError("Pocket or account is archived")
    if transaction.currency != acc.currency:
        raise ValueError(
            "Record the actual amount in the account currency before assigning a pocket"
        )
    if transaction.amount is not None and transaction.amount < 0:
        raise ValueError("Pocket transaction amounts must be non-negative")
    if transaction.date < pocket.start_date:
        raise ValueError(
            "Transaction predates the pocket; leave historical transactions unallocated"
        )


async def create_income_rule(session, workspace_id, data: PocketIncomeRuleCreate):
    acc = await account(session, workspace_id, data.account_id, lock=True)
    previous = await session.get(PocketIncomeRule, data.id)
    if previous:
        if previous.workspace_id != workspace_id or any(getattr(previous, k) != getattr(data, k)
            for k in ("account_id", "pocket_id", "percentage", "start_date")):
            raise ValueError("Rule id already used for another allocation")
        return PocketIncomeRuleRead.model_validate(previous)
    target = await session.get(Pocket, data.pocket_id)
    if acc.is_closed or acc.type not in ("checking", "savings", "wallet") or not target or target.workspace_id != workspace_id or target.account_id != acc.id or target.archived_at:
        raise ValueError("Choose an active pocket of this account")
    if data.start_date < max(app_today(), target.start_date):
        raise ValueError("Income rules cannot reallocate historical income")
    rules = list((await session.scalars(select(PocketIncomeRule).where(
        PocketIncomeRule.account_id == acc.id, PocketIncomeRule.workspace_id == workspace_id,
    ))).all())
    # Checking each interval start covers the maximum overlap, including rules
    # starting after this one. The account lock serializes concurrent creation.
    candidates = [data, *rules]
    for on in {r.start_date for r in candidates}:
        overlapping = [r for r in candidates if r.start_date <= on and (r.end_date is None or on <= r.end_date)]
        if sum((r.percentage for r in overlapping), Decimal(0)) > 100:
            raise ValueError("Income allocations must total at most 100%")
        if sum(r.pocket_id == data.pocket_id for r in overlapping) > 1:
            raise ValueError("This pocket already has an overlapping income rule")
    rule = PocketIncomeRule(workspace_id=workspace_id, **data.model_dump())
    session.add(rule)
    await session.commit()
    return PocketIncomeRuleRead.model_validate(rule)


async def stop_income_rule(session, workspace_id, rule_id, end_date):
    rule = await session.scalar(select(PocketIncomeRule).where(
        PocketIncomeRule.id == rule_id, PocketIncomeRule.workspace_id == workspace_id,
    ).with_for_update())
    if not rule:
        raise ValueError("Income rule not found")
    await account(session, workspace_id, rule.account_id, lock=True)
    if rule.end_date == end_date:
        return PocketIncomeRuleRead.model_validate(rule)
    if end_date < max(app_today(), rule.start_date) or (rule.end_date and end_date > rule.end_date):
        raise ValueError("Close the rule without rewriting past allocations or extending its interval")
    rule.end_date = end_date
    await session.commit()
    return PocketIncomeRuleRead.model_validate(rule)
