"""Disposable local preview of pocket funding; no application database writes."""

import asyncio
import uuid
import json
from datetime import timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles
from app.core.database import Base
from app.core.app_clock import app_today
from app.models import (
    User,
    Workspace,
    WorkspaceMember,
    Account,
    Pocket,
    PocketMovement,
    Transaction,
)
from app.schemas.pocket import PocketIncomeRuleCreate
from app.services import pocket_service
from app.services.goal_service import _resolve_current_amount
from app.models.goal import Goal
from app.services.transfer_schedule_service import occurrences, occurrence_amount
from app.services.recurring_transaction_service import _advance_months
from types import SimpleNamespace
from datetime import date


@compiles(UUID, "sqlite")
def sqlite_uuid(type_, compiler, **kw):
    return "CHAR(32)"


async def run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        today = app_today()
        u = User(
            id=uuid.uuid4(),
            email="preview@example.invalid",
            hashed_password="unused",
            preferences={"currency_display": "USD"},
        )
        s.add(u)
        await s.flush()
        w = Workspace(id=uuid.uuid4(), name="Disposable preview", created_by_user_id=u.id)
        s.add(w)
        await s.flush()
        s.add(WorkspaceMember(workspace_id=w.id, user_id=u.id, role="owner"))
        a = Account(
            id=uuid.uuid4(),
            user_id=u.id,
            workspace_id=w.id,
            name="Any bank",
            currency="USD",
            type="checking",
            balance=0,
        )
        s.add(a)
        await s.flush()
        p = Pocket(
            id=uuid.uuid4(), workspace_id=w.id, account_id=a.id, name="Any pocket", start_date=today
        )
        s.add(p)
        await s.flush()

        def income(amount, **changes):
            args = dict(
                id=uuid.uuid4(),
                workspace_id=w.id,
                user_id=u.id,
                account_id=a.id,
                description="Sample income",
                type="credit",
                currency="USD",
                date=today,
                effective_date=today,
                status="posted",
                source="manual",
                amount=Decimal(str(amount)),
            )
            args.update(changes)
            tx = Transaction(**args)
            s.add(tx)
            return tx

        income(1000, source="opening_balance")
        posted = income(100)
        income(500, status="pending")
        income(700, date=today + timedelta(days=1), effective_date=today + timedelta(days=1))
        income(150, transfer_pair_id=uuid.uuid4())
        income(80, exclude_from_pnl=True)
        await s.commit()
        data = PocketIncomeRuleCreate(
            id=uuid.uuid4(), account_id=a.id, pocket_id=p.id, percentage=10, start_date=today
        )
        from mcp_server.auth import CallContext
        from mcp_server.tools.pockets import propose_pocket

        ctx = CallContext(user_id=u.id, workspace_id=w.id, external=True)
        args = dict(session=s, ctx=ctx, operation="income_rule", data=data.model_dump(mode="json"))
        preview = await propose_pocket(**args)
        before = await pocket_service.get_account_pockets(s, w.id, a.id)
        applied = await propose_pocket(**args, apply=True)
        await propose_pocket(**args, apply=True)
        report: dict[str, object] = {
            "mcp_preview_does_not_write": "applied" not in preview
            and len(before.income_rules) == 0,
            "mcp_applied": applied.get("applied"),
        }

        async def sample(label):
            state = await pocket_service.get_account_pockets(s, w.id, a.id)
            report[label] = {
                "bank": str(state.balance),
                "pocket": str(state.items[0].balance),
                "unallocated": str(state.unallocated),
                "rule_count": len(state.income_rules),
            }

        await sample("posted_100_plus_excluded_rows")
        posted.amount = 200
        await s.commit()
        await sample("corrected_income_200")
        posted.is_ignored = True
        await s.commit()
        await sample("ignored_income")
        posted.is_ignored = False
        await s.commit()
        await pocket_service.stop_income_rule(s, w.id, data.id, today)
        await sample("closed_rule_keeps_history")
        for label, change in [
            ("past_date", {"id": uuid.uuid4(), "start_date": today - timedelta(days=1)}),
            ("over_100_percent", {"id": uuid.uuid4(), "percentage": 100}),
        ]:
            try:
                await pocket_service.create_income_rule(s, w.id, data.model_copy(update=change))
            except ValueError as e:
                report[label] = str(e)
        await s.delete(posted)
        await s.commit()
        await sample("deleted_income")
        s.add(
            PocketMovement(
                workspace_id=w.id,
                account_id=a.id,
                to_pocket_id=p.id,
                amount=Decimal("600"),
                idempotency_key=uuid.uuid4(),
                created_by=u.id,
            )
        )
        await s.commit()
        income(200, type="debit", status="pending", pocket_id=p.id)
        await s.commit()
        g = Goal(
            id=uuid.uuid4(),
            workspace_id=w.id,
            user_id=u.id,
            name="Refill",
            currency="USD",
            tracking_type="pockets",
            target_amount=600,
            pocket_balance_basis="balance",
        )
        s.add(g)
        await s.flush()
        p.goal_id = g.id
        await s.commit()
        report["funding_goal_before_pending"] = str(await _resolve_current_amount(s, g, u.id))
        g.pocket_balance_basis = "available"
        await s.commit()
        report["savings_goal_after_pending"] = str(await _resolve_current_amount(s, g, u.id))
        report["movements_written"] = await s.scalar(
            select(func.count()).select_from(PocketMovement)
        )
        from app.services import transaction_service, transfer_schedule_service
        from app.schemas.transaction import TransferCreate
        from app.schemas.transfer_schedule import TransferScheduleCreate
        from mcp_server.tools.transfers import propose_delete_pending_transfer

        destination = Account(
            user_id=u.id,
            workspace_id=w.id,
            name="Another bank",
            currency="USD",
            type="checking",
            balance=0,
        )
        s.add(destination)
        await s.commit()
        pair = await transaction_service.create_transfer(
            s,
            w.id,
            u.id,
            TransferCreate(
                from_account_id=a.id,
                to_account_id=destination.id,
                amount=Decimal("1"),
                date=today,
                description="One-off pending preview",
                status="pending",
            ),
        )
        key = str(pair[0].transfer_pair_id)
        args_delete = dict(session=s, ctx=ctx, transfer_pair_id=key)
        preview_delete = await propose_delete_pending_transfer(**args_delete)
        report["pending_pair_preview_count"] = preview_delete["affected_count"]
        deleted = await propose_delete_pending_transfer(**args_delete, apply=True)
        retry = await propose_delete_pending_transfer(**args_delete, apply=True)
        report["pending_pair_deleted_count"] = deleted["affected_count"]
        report["pending_pair_delete_retry_count"] = retry["affected_count"]
        plan = await transfer_schedule_service.save_schedule(
            s,
            w.id,
            u.id,
            TransferScheduleCreate(
                from_account_id=a.id,
                to_account_id=destination.id,
                description="Automatic weekly preview",
                amount=25,
                currency="USD",
                amount_mode="prorated_weekly",
                cycle_day=5,
                weekday=0,
                frequency="days",
                interval=7,
                start_date=today,
                initial_cycle_amount=15,
            ),
        )
        generated = await transfer_schedule_service.materialize(s, w.id, plan.id, today)
        repeated = await transfer_schedule_service.materialize(s, w.id, plan.id, today)
        report["scheduled_pair_retry_same_ids"] = {tx.id for tx in generated} == {
            tx.id for tx in repeated
        }
        try:
            await transfer_schedule_service.delete_pending_pair(
                s, w.id, generated[0].transfer_pair_id, apply=True
            )
        except ValueError as e:
            report["scheduled_pair_delete_rejected"] = str(e)
        await transfer_schedule_service.save_schedule(
            s,
            w.id,
            u.id,
            TransferScheduleCreate(
                from_account_id=a.id,
                to_account_id=destination.id,
                description="Due transfer without automatic salary or bills",
                amount=1,
                currency="USD",
                frequency="months",
                interval=1,
                day_of_month=today.day,
                start_date=today,
            ),
        )
        from app.services.recurring_transaction_service import generate_pending

        count = await generate_pending(s, u.id)
        report["scheduled_transfer_without_auto_generated_bills"] = count
        if count != 1:
            raise RuntimeError("Due transfer was skipped when no bills auto-generate")
        retry_count = await generate_pending(s, u.id)
        report["scheduled_transfer_worker_retry_count"] = retry_count
        if retry_count:
            raise RuntimeError("Worker retry duplicated a transfer")
        print(json.dumps(report, indent=2))
    await engine.dispose()


p = SimpleNamespace(
    amount=Decimal("25000000"),
    amount_mode="prorated_weekly",
    cycle_day=5,
    weekday=0,
    start_date=date(2026, 11, 5),
    end_date=None,
    frequency="days",
    interval=7,
)
print("November food cycle:")
for on in occurrences(p, date(2026, 11, 5), date(2026, 12, 5)):
    print(on, occurrence_amount(p, on))
p.start_date = date(2026, 10, 12)
p.initial_cycle_amount = Decimal("15326525")
print("First cycle remainder:")
initial_total = Decimal(0)
for on in occurrences(p, p.start_date, date(2026, 11, 5)):
    amount = occurrence_amount(p, on)
    initial_total += amount
    print(on, amount)
if initial_total != p.initial_cycle_amount:
    raise RuntimeError("First cycle does not match its remaining budget")
next_total = sum(
    occurrence_amount(p, on) for on in occurrences(p, date(2026, 11, 5), date(2026, 12, 5))
)
if next_total != p.amount:
    raise RuntimeError("First cycle remainder leaked into later cycles")
p.initial_cycle_amount = None
for day in (1, 5, 28, 29, 30, 31):
    p.cycle_day = day
    p.start_date = date(2026, 1, 1)
    for m in range(36):
        start = _advance_months(date(2026, 1, 1), m, day)
        end = _advance_months(start, 1, day)
        total = sum(occurrence_amount(p, on) for on in occurrences(p, start, end))
        if total != p.amount:
            raise RuntimeError(f"Unexpected cycle total: {start} {total}")
print("216 full cycles, including February 2028: exact monthly totals")
asyncio.run(run())
