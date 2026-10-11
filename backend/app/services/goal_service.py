import uuid
from datetime import date
from decimal import Decimal
from typing import Optional, cast

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.goal import Goal, GoalCompletion
from app.models.pocket import Pocket
from app.models.user import User
from app.schemas.goal import (
    GoalCreate, GoalRead, GoalSummary, GoalUpdate,
    GoalComplete, GoalRestart, GoalCompletionRead,
)
from app.schemas.recurring_transaction import RecurringFrequency
from app.services.asset_service import _compute_current_value, _get_latest_value, get_asset_values_at
from app.services.dashboard_service import _account_balance_at, _get_open_accounts
from app.services.account_service import get_account_name
from app.services.fx_rate_service import convert


async def _get_primary_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def _convert_amount(
    session: AsyncSession, amount: Decimal, from_currency: str, to_currency: str
) -> Decimal:
    if from_currency == to_currency:
        return amount
    converted, _ = await convert(session, amount, from_currency, to_currency)
    return converted


async def _sum_native_totals_in_currency(
    session: AsyncSession, totals_by_currency: dict[str, float], target_currency: str
) -> Decimal:
    total = Decimal("0")
    for currency, amount in totals_by_currency.items():
        total += await _convert_amount(session, Decimal(str(amount)), currency, target_currency)
    return total


async def _linked_name(session: AsyncSession, model: type, item_id: uuid.UUID | None) -> Optional[str]:
    if not item_id:
        return None
    item = await session.get(model, item_id)
    if not item:
        return None
    if isinstance(item, Account):
        return get_account_name(item)
    # Callers pass Asset / AssetGroup, both of which have `.name`. Stay
    # tolerant rather than raising: a goal whose link we can't name should
    # render without one, not 500 the whole goals list.
    return getattr(item, "name", None)


async def _resolve_current_amount(
    session: AsyncSession, goal: Goal, user_id: uuid.UUID
) -> Decimal:
    """Resolve the current_amount based on tracking_type.

    Returns the amount in the goal's currency.
    """
    goal_currency = goal.currency
    # Asset/account/net_worth lookups need the goal's workspace scope.
    workspace_id = goal.workspace_id

    if goal.tracking_type == "pockets":
        if goal.id is None:
            return Decimal(0)
        from app.services.pocket_service import get_account_pockets
        from app.services.fx_rate_service import _resolve_rate
        account_ids = (await session.scalars(select(Pocket.account_id).where(
            Pocket.goal_id == goal.id, Pocket.workspace_id == workspace_id,
        ).distinct())).all()
        total = Decimal(0)
        for account_id in account_ids:
            state = await get_account_pockets(session, workspace_id, account_id)
            rate = await _resolve_rate(session, state.currency, goal.currency, allow_fetch=False)
            if rate is None or state.needs_review:
                raise ValueError("Pocket valuation unavailable")
            for pocket in state.items:
                if pocket.goal_id == goal.id:
                    if pocket.needs_review:
                        raise ValueError("Pocket valuation unavailable")
                    total += (pocket.balance if goal.pocket_balance_basis == "balance" else pocket.available) * rate
        if goal.pocket_balance_basis == "balance":
            return total.quantize(Decimal("0.01"))
        # Reserving a due internal transfer between two pockets of this goal
        # moves the savings, it does not spend them. The pending destination
        # is not yet in its pocket balance, so retain the source reservation.
        # Future pairs reserve nothing and must not add goal funding.
        from sqlalchemy.orm import aliased
        from app.models.transaction import Transaction
        from app.services._query_filters import is_not_ignored, is_not_future
        from app.models.category import Category
        today = app_today()
        destination = aliased(Transaction)
        source_pocket = aliased(Pocket)
        destination_pocket = aliased(Pocket)
        retained = await session.scalars(select(Transaction).join(
            source_pocket, source_pocket.id == Transaction.pocket_id,
        ).join(destination, (destination.transfer_pair_id == Transaction.transfer_pair_id)
               & (destination.type == "credit")
               & or_(destination.status == "pending", destination.date > today))
        .join(destination_pocket, destination_pocket.id == destination.pocket_id).where(
            Transaction.workspace_id == workspace_id, Transaction.type == "debit",
            Transaction.status == "pending", is_not_future(today),
            is_not_ignored(), destination.is_ignored.is_(False),
            or_(destination.category_id.is_(None), destination.category_id.not_in(select(Category.id).where(Category.is_ignored.is_(True)))),
            source_pocket.goal_id == goal.id,
            destination_pocket.goal_id == goal.id,
        ))
        for tx in retained:
            rate = await _resolve_rate(session, tx.currency, goal.currency, allow_fetch=False)
            if rate is None:
                raise ValueError("Pocket valuation unavailable")
            total += tx.amount * rate
        return total.quantize(Decimal("0.01"))

    if goal.tracking_type == "account" and goal.account_id:
        account = await session.get(Account, goal.account_id)
        if account:
            # Use dashboard's balance logic so manual accounts are computed correctly
            bal = Decimal(str(await _account_balance_at(session, account, app_today())))
            return await _convert_amount(session, bal, account.currency, goal_currency)
        return goal.current_amount
    elif goal.tracking_type == "asset" and goal.asset_id:
        asset = await session.get(Asset, goal.asset_id)
        if asset:
            latest = await _get_latest_value(session, asset.id)
            value = _compute_current_value(asset, latest)
            if value is not None:
                return await _convert_amount(session, Decimal(str(value)), asset.currency, goal_currency)
        return goal.current_amount
    elif goal.tracking_type == "asset_group" and goal.asset_group_id:
        group = await session.get(AssetGroup, goal.asset_group_id)
        if group:
            assets_by_currency, _ = await get_asset_values_at(
                session, workspace_id, by_workspace=True, group_ids=[group.id]
            )
            return await _sum_native_totals_in_currency(session, assets_by_currency, goal_currency)
        return goal.current_amount
    elif goal.tracking_type == "net_worth":
        # Reuse dashboard's account query and balance logic so manual accounts
        # (whose balance is computed from transactions) are handled correctly.
        accounts = await _get_open_accounts(session, workspace_id)
        today = app_today()
        total = Decimal("0")
        for acc in accounts:
            bal = Decimal(str(await _account_balance_at(session, acc, today)))
            total += await _convert_amount(session, bal, acc.currency, goal_currency)

        # Add asset values (scoped by the goal's workspace).
        assets_by_currency, _ = await get_asset_values_at(session, workspace_id, by_workspace=True)
        total += await _sum_native_totals_in_currency(session, assets_by_currency, goal_currency)

        return total
    else:
        return goal.current_amount


async def _ensure_goal_link_scope(
    session: AsyncSession, workspace_id: uuid.UUID, data: GoalCreate | GoalUpdate
) -> None:
    """Validate linked tracking targets stay inside the current workspace."""
    checks = [
        (data.account_id, Account, "Linked account not found"),
        (data.asset_id, Asset, "Linked asset not found"),
        (data.asset_group_id, AssetGroup, "Linked wallet not found"),
    ]
    for item_id, model, error in checks:
        if not item_id:
            continue
        item = await session.get(model, item_id)
        if not item or item.workspace_id != workspace_id:
            raise ValueError(error)


def _clear_inactive_tracking_links(goal: Goal) -> None:
    """Keep only the link field used by the selected tracking type."""
    if goal.tracking_type != "account":
        goal.account_id = None
    if goal.tracking_type != "asset":
        goal.asset_id = None
    if goal.tracking_type != "asset_group":
        goal.asset_group_id = None


def _compute_percentage(current: Decimal, target: Decimal) -> float:
    if target <= 0:
        return 100.0 if current > 0 else 0.0
    return round(float(current / target) * 100, 1)


def _compute_monthly_contribution(
    current: Decimal, target: Decimal, target_date: Optional[date]
) -> Optional[float]:
    if not target_date:
        return None
    today = app_today()
    if today >= target_date:
        return 0.0
    remaining = target - current
    if remaining <= 0:
        return 0.0
    # Calculate months remaining (approximate)
    months = (target_date.year - today.year) * 12 + (target_date.month - today.month)
    if months <= 0:
        months = 1
    return round(float(remaining / months), 2)


def _compute_on_track(
    current: Decimal, target: Decimal, target_date: Optional[date],
    created_at: Optional[date] = None, initial_amount: Decimal = Decimal("0"),
) -> Optional[str]:
    if not target_date:
        return None
    if current >= target:
        return "achieved"
    today = app_today()
    if today > target_date:
        return "overdue"

    # Use created_at as start date; fall back to today (no progress expected yet)
    start = created_at if created_at else today
    total_days = (target_date - start).days
    if total_days <= 0:
        return "on_track"

    # Measure progress relative to the starting baseline, not from zero.
    # total_needed = how much needs to be saved from initial to target
    # actual_progress = how much has been saved since creation
    total_needed = target - initial_amount
    if total_needed <= 0:
        return "achieved"

    elapsed_days = (today - start).days
    expected_progress = total_needed * Decimal(str(elapsed_days / total_days))
    actual_progress = current - initial_amount

    diff = actual_progress - expected_progress
    # Express tolerance as percentage of total_needed
    tolerance = total_needed * Decimal("0.05")

    if diff >= tolerance * 2:
        return "ahead"
    if diff >= -tolerance:
        return "on_track"
    return "behind"


async def _enrich_goal(
    session: AsyncSession, goal: Goal, user_id: uuid.UUID
) -> GoalRead:
    """Enrich a goal with computed fields."""
    last_completion = await session.scalar(select(GoalCompletion).where(
        GoalCompletion.goal_id == goal.id,
    ).order_by(GoalCompletion.cycle_number.desc()).limit(1))
    valuation_available = True
    try:
        current = await _resolve_current_amount(session, goal, user_id)
    except ValueError:
        if goal.tracking_type != "pockets":
            raise
        current = Decimal(0)
        valuation_available = False
    if goal.status == "completed" and last_completion and last_completion.cycle_number == goal.cycle_number:
        from app.services.fx_rate_service import _resolve_rate
        rate = await _resolve_rate(session, last_completion.currency, goal.currency, allow_fetch=False)
        valuation_available = rate is not None
        current = last_completion.saved_amount * rate if rate is not None else Decimal(0)
    percentage = _compute_percentage(current, goal.target_amount)
    monthly = _compute_monthly_contribution(current, goal.target_amount, goal.target_date)
    goal_start = goal.cycle_started_on or (goal.created_at.date() if goal.created_at else None)
    on_track = _compute_on_track(
        current, goal.target_amount, goal.target_date, goal_start, goal.initial_amount
    )

    account_name = await _linked_name(session, Account, goal.account_id)
    asset_name = await _linked_name(session, Asset, goal.asset_id)
    asset_group_name = await _linked_name(session, AssetGroup, goal.asset_group_id)

    # Convert to primary currency if needed
    primary_currency = await _get_primary_currency(session, user_id)
    target_primary = None
    current_primary = None
    if goal.currency != primary_currency:
        if goal.tracking_type == "pockets":
            from app.services.fx_rate_service import _resolve_rate
            rate = await _resolve_rate(session, goal.currency, primary_currency, allow_fetch=False)
            if rate is not None and valuation_available:
                target_primary = goal.target_amount * rate
                current_primary = current * rate
        else:
            target_primary = await _convert_amount(session, goal.target_amount, goal.currency, primary_currency)
            current_primary = await _convert_amount(session, current, goal.currency, primary_currency)

    return GoalRead(
        repeat_frequency=cast(RecurringFrequency | None, goal.repeat_frequency),
        cycle_number=goal.cycle_number,
        next_target_date=_next_target_date(goal),
        last_completion=GoalCompletionRead.model_validate(last_completion) if last_completion else None,
        valuation_available=valuation_available,
        id=goal.id,
        user_id=goal.user_id,
        name=goal.name,
        target_amount=goal.target_amount,
        current_amount=current,
        currency=goal.currency,
        target_amount_primary=target_primary,
        current_amount_primary=current_primary,
        target_date=goal.target_date,
        tracking_type=goal.tracking_type,
        account_id=goal.account_id,
        asset_id=goal.asset_id,
        asset_group_id=goal.asset_group_id,
        status=goal.status,
        icon=goal.icon,
        color=goal.color,
        position=goal.position,
        metadata_json=goal.metadata_json,
        pocket_balance_basis=goal.pocket_balance_basis,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        percentage=percentage,
        monthly_contribution=monthly,
        on_track=on_track if valuation_available else None,
        account_name=account_name,
        asset_name=asset_name,
        asset_group_name=asset_group_name,
    )


async def get_goals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    status: Optional[str] = None,
) -> list[GoalRead]:
    query = select(Goal).where(Goal.workspace_id == workspace_id).order_by(Goal.position, Goal.created_at)
    if status:
        query = query.where(Goal.status == status)
    result = await session.execute(query)
    goals = list(result.scalars().all())
    return [await _enrich_goal(session, g, user_id) for g in goals]


async def get_goal(
    session: AsyncSession,
    goal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[GoalRead]:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == workspace_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return None
    return await _enrich_goal(session, goal, user_id)


async def create_goal(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: GoalCreate,
) -> GoalRead:
    _validate_repeat(data.repeat_frequency, data.target_date, data.tracking_type)
    await _ensure_goal_link_scope(session, workspace_id, data)
    goal = Goal(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        target_amount=data.target_amount,
        current_amount=data.current_amount,
        currency=data.currency,
        target_date=data.target_date,
        repeat_frequency=data.repeat_frequency,
        repeat_anchor_day=data.target_date.day if data.target_date else None,
        cycle_started_on=app_today(),
        tracking_type=data.tracking_type,
        pocket_balance_basis=data.pocket_balance_basis,
        account_id=data.account_id,
        asset_id=data.asset_id,
        asset_group_id=data.asset_group_id,
        icon=data.icon,
        color=data.color,
        metadata_json=data.metadata_json,
    )
    _clear_inactive_tracking_links(goal)
    # Capture the starting balance so on-track logic measures progress from baseline
    initial = await _resolve_current_amount(session, goal, user_id)
    goal.initial_amount = initial
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def update_goal(
    session: AsyncSession,
    goal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: GoalUpdate,
) -> Optional[GoalRead]:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == workspace_id).with_for_update()
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return None
    completed_cycle = await session.scalar(select(GoalCompletion.id).where(
        GoalCompletion.goal_id == goal.id, GoalCompletion.cycle_number == goal.cycle_number,
    ))
    if (goal.repeat_frequency or data.repeat_frequency) and data.status and (
        data.status == "completed" or (completed_cycle and data.status == "active")
    ):
        raise ValueError("Use the complete or restart action for a recurring goal")
    if completed_cycle and data.status == "active":
        goal.cycle_number += 1
        goal.cycle_started_on = app_today()
    await _ensure_goal_link_scope(session, workspace_id, data)
    previous_target_date = goal.target_date
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "tracking_type" and value != "pockets" and goal.tracking_type == "pockets":
            linked = await session.scalar(select(Pocket.id).where(Pocket.goal_id == goal.id).limit(1))
            if linked:
                raise ValueError("Unlink the goal's pockets before changing its tracking type")
        setattr(goal, field, value)
    _validate_repeat(goal.repeat_frequency, goal.target_date, goal.tracking_type)
    if goal.target_date != previous_target_date or goal.repeat_anchor_day is None:
        goal.repeat_anchor_day = goal.target_date.day if goal.target_date else None
    _clear_inactive_tracking_links(goal)
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def delete_goal(
    session: AsyncSession, goal_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == workspace_id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        return False
    await session.delete(goal)
    await session.commit()
    return True


async def get_goal_summary(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 3,
) -> list[GoalSummary]:
    """Get a summary of active goals for the dashboard widget."""
    result = await session.execute(
        select(Goal)
        .where(Goal.workspace_id == workspace_id, Goal.status == "active")
        .order_by(Goal.position, Goal.created_at)
        .limit(limit)
    )
    goals = list(result.scalars().all())
    summaries = []
    for goal in goals:
        enriched = await _enrich_goal(session, goal, user_id)
        current = enriched.current_amount
        percentage = _compute_percentage(current, goal.target_amount)
        monthly = _compute_monthly_contribution(current, goal.target_amount, goal.target_date)
        goal_start = goal.cycle_started_on or (goal.created_at.date() if goal.created_at else None)
        on_track = _compute_on_track(
            current, goal.target_amount, goal.target_date, goal_start, goal.initial_amount
        )
        summaries.append(GoalSummary(
            valuation_available=enriched.valuation_available,
            id=goal.id,
            name=goal.name,
            target_amount=goal.target_amount,
            current_amount=current,
            currency=goal.currency,
            target_date=goal.target_date,
            status=goal.status,
            icon=goal.icon,
            color=goal.color,
            percentage=percentage,
            monthly_contribution=monthly,
            on_track=on_track if enriched.valuation_available else None,
        ))
    return summaries



def _validate_repeat(frequency, target_date, tracking_type):
    if frequency and (not target_date or tracking_type not in ("manual", "pockets")):
        raise ValueError("Repeating goals require a target date and manual or pocket tracking")


def _next_target_date(goal):
    if not goal.repeat_frequency or not goal.target_date:
        return None
    from app.services.recurring_transaction_service import _advance_date
    return _advance_date(goal.target_date, goal.repeat_frequency, intended_day=goal.repeat_anchor_day)


async def completion_history(session, workspace_id, goal_id):
    goal = await session.scalar(select(Goal).where(Goal.id == goal_id, Goal.workspace_id == workspace_id))
    if not goal:
        raise ValueError("Goal not found")
    rows = await session.scalars(select(GoalCompletion).where(
        GoalCompletion.goal_id == goal_id,
    ).order_by(GoalCompletion.cycle_number.desc()))
    return [GoalCompletionRead.model_validate(row) for row in rows]


async def complete_goal(session, workspace_id, user_id, goal_id, data: GoalComplete):
    from app.models.transaction import Transaction
    from app.schemas.transaction import TransactionCreate
    from app.services import pocket_service, transaction_service
    from app.services.fx_rate_service import _resolve_rate

    goal = await session.scalar(select(Goal).where(
        Goal.id == goal_id, Goal.workspace_id == workspace_id,
    ).with_for_update())
    if not goal:
        raise ValueError("Goal not found")
    # A stale/retried click must never charge the next cycle.
    if goal.cycle_number != data.cycle_number or goal.status not in ("active", "paused"):
        raise ValueError("Goal cycle already completed or changed; refresh the goal")
    tx = None
    if data.payment or data.transaction_id:
        if goal.tracking_type != "pockets":
            raise ValueError("Payment completion requires pocket tracking")
        if data.transaction_id:
            tx = await session.scalar(select(Transaction).where(
                Transaction.id == data.transaction_id, Transaction.workspace_id == workspace_id,
            ).with_for_update())
            if (not tx or tx.type != "debit" or tx.status != "posted" or tx.is_ignored
                    or tx.transfer_pair_id or tx.exclude_from_pnl or tx.amount <= 0 or tx.date > app_today()):
                raise ValueError("Select an existing posted expense from a linked pocket")
            if await session.scalar(select(GoalCompletion.id).where(GoalCompletion.transaction_id == tx.id)):
                raise ValueError("This payment already completed another cycle")
        if data.payment:
            pocket_id = data.payment.pocket_id
        elif tx:
            pocket_id = tx.pocket_id
        else:
            raise ValueError("Payment not found")
        pocket = await session.scalar(select(Pocket).where(
            Pocket.id == pocket_id, Pocket.workspace_id == workspace_id, Pocket.goal_id == goal.id,
        ))
        if not pocket:
            raise ValueError("Payment must use a pocket linked to this goal")
        account = await pocket_service.account(session, workspace_id, pocket.account_id, lock=True)
        if data.payment:
            if data.payment.date > app_today():
                raise ValueError("A completed payment cannot be in the future")
            state = await pocket_service.get_account_pockets(session, workspace_id, account.id)
            balance = next(p for p in state.items if p.id == pocket.id)
            if state.needs_review or balance.needs_review or data.payment.amount > balance.available:
                raise ValueError("Not enough available money in this pocket")
    current = await _resolve_current_amount(session, goal, user_id)
    if tx:
        rate = await _resolve_rate(session, tx.currency, goal.currency, allow_fetch=False)
        if rate is None:
            raise ValueError("Exchange rate unavailable")
        current += tx.amount * rate
    if data.payment:
        tx = await transaction_service.create_transaction(session, workspace_id, user_id, TransactionCreate(
            description=goal.name, amount=data.payment.amount, date=data.payment.date,
            type="debit", status="posted", currency=account.currency,
            account_id=account.id, pocket_id=pocket.id, category_id=data.payment.category_id,
        ), commit=False)
    session.add(GoalCompletion(
        goal_id=goal.id, cycle_number=goal.cycle_number, target_amount=goal.target_amount,
        saved_amount=current, currency=goal.currency, target_date=goal.target_date,
        completed_on=app_today(), transaction_id=tx.id if tx else None,
        spent_amount=tx.amount if tx else None, spent_currency=tx.currency if tx else None,
        spent_on=tx.date if tx else None,
    ))
    goal.status = "completed"
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def restart_goal(session, workspace_id, user_id, goal_id, data: GoalRestart):
    goal = await session.scalar(select(Goal).where(
        Goal.id == goal_id, Goal.workspace_id == workspace_id,
    ).with_for_update())
    if not goal:
        raise ValueError("Goal not found")
    if goal.status not in ("completed", "archived") or goal.cycle_number != data.cycle_number:
        raise ValueError("Complete the current cycle before restarting; refresh the goal")
    if goal.status == "archived" and not await session.scalar(select(GoalCompletion.id).where(
        GoalCompletion.goal_id == goal.id, GoalCompletion.cycle_number == goal.cycle_number,
    )):
        raise ValueError("Complete the current cycle before restarting")
    next_date = _next_target_date(goal)
    if not next_date:
        raise ValueError("Configure repetition and a target date first")
    goal.target_date = next_date
    goal.cycle_number += 1
    goal.cycle_started_on = app_today()
    goal.status = "active"
    if goal.tracking_type == "manual":
        goal.current_amount = Decimal(0)
    goal.initial_amount = await _resolve_current_amount(session, goal, user_id)
    await session.commit()
    await session.refresh(goal)
    return await _enrich_goal(session, goal, user_id)


async def goal_funding(session, workspace_id, goal_id):
    from app.models.transaction import Transaction
    from app.services import pocket_service
    goal = await session.scalar(select(Goal).where(Goal.id == goal_id, Goal.workspace_id == workspace_id))
    if not goal:
        raise ValueError("Goal not found")
    pockets = []
    account_ids = await session.scalars(select(Pocket.account_id).where(
        Pocket.goal_id == goal_id, Pocket.workspace_id == workspace_id,
    ).distinct())
    for account_id in account_ids:
        account = await pocket_service.account(session, workspace_id, account_id)
        state = await pocket_service.get_account_pockets(session, workspace_id, account_id)
        for pocket in state.items:
            if pocket.goal_id == goal_id and not pocket.archived:
                pockets.append({**pocket.model_dump(mode="json"), "account_name": get_account_name(account)})
    rows = await session.scalars(select(Transaction).join(Pocket, Pocket.id == Transaction.pocket_id).where(
        Pocket.goal_id == goal_id, Transaction.workspace_id == workspace_id,
        Transaction.type == "debit", Transaction.status == "posted", Transaction.is_ignored.is_(False),
        Transaction.exclude_from_pnl.is_(False), Transaction.amount > 0,
        Transaction.transfer_pair_id.is_(None), Transaction.date <= app_today(),
        ~select(GoalCompletion.id).where(GoalCompletion.transaction_id == Transaction.id).exists(),
    ).order_by(Transaction.date.desc()).limit(100))
    payments = [{"id": str(tx.id), "description": tx.description, "date": tx.date.isoformat(),
                 "amount": str(tx.amount), "currency": tx.currency} for tx in rows]
    return {"pockets": pockets, "payments": payments}
