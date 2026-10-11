"""Import settled statement history without changing a manual account's balance."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from app.core.app_clock import app_today
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services import account_service, import_service, pocket_service
from app.services.category_service import get_assignable_category_ids
from app.services.credit_card_service import apply_effective_date
from mcp_server.registry import tool
from mcp_server.tools._helpers import parse_uuid, resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


async def _balance(session, account):
    # Same settled, native-currency ledger used by account balance reports.
    return await session.scalar(
        select(func.coalesce(func.sum(account_service._signed_amount_expr(account.currency)), 0))
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.account_id == account.id,
            Transaction.date <= app_today(),
            Transaction.status == "posted",
            Transaction.is_ignored == False,
            or_(Transaction.category_id.is_(None), Category.is_ignored == False),
        )
    )


@tool(
    name="propose_import_history",
    description=_PROPOSAL_PREFACE + (
        "Import settled bank history into a manual checking, savings or wallet account. "
        "Preserves its CURRENT balance by default, reconciling its opening_balance row atomically; "
        "never creates a compensating income/expense or assigns historical rows to pockets. "
        "expected_balance is the current Securo balance, NOT a replacement bank balance. "
        "Pass closing_balance only when the user confirmed a different bank closing balance. "
        "Rejects stale balances, foreign currencies, future/undated rows and inaccessible categories. "
        "Reuse stable external_id values to avoid duplicate imports. Pending bank rows need dates "
        "and are not supported here. Internal transfers must use a transfer category so reports "
        "do not treat them as family income/expenses. A negative inferred opening balance means "
        "the provided history or current balance needs reconciliation, not proven bank overdraft."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "expected_balance": {"type": "string", "description": "Exact current native balance"},
            "closing_balance": {
                "type": "string", "description": "Optional user-confirmed native closing balance",
            },
            "filename": {"type": "string", "default": ""},
            "transactions": {
                "type": "array", "minItems": 1, "maxItems": 500,
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "minLength": 1},
                        "amount": {"type": "string", "description": "Positive native amount"},
                        "date": {"type": "string", "format": "date"},
                        "type": {"type": "string", "enum": ["debit", "credit"]},
                        "currency": {"type": "string"},
                        "external_id": {"type": "string", "minLength": 1},
                        "category_id": {"type": "string", "format": "uuid"},
                        "notes": {"type": "string"},
                    },
                    "required": ["description", "amount", "date", "type", "external_id"],
                    "additionalProperties": False,
                },
            },
            "apply": _APPLY_FIELD,
        },
        "required": ["account_id", "expected_balance", "transactions"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["write", "import", "transactions"],
)
async def propose_import_history(
    *, session, ctx, account_id, expected_balance, transactions, filename="", apply=False,
    closing_balance=None,
):
    do_apply = _can_apply(ctx, apply)
    ws = await resolve_tool_workspace(session, ctx, write=do_apply)
    account = await pocket_service.account(
        session, ws.id, parse_uuid(account_id), lock=do_apply,
    )
    if account.connection_id or account.type not in ("checking", "savings", "wallet"):
        raise ValueError("History reconciliation requires a manual checking, savings or wallet account")
    expected = Decimal(expected_balance)
    before = await _balance(session, account)
    if not expected.is_finite() or before != expected:
        raise ValueError("Current account balance changed; read it again before importing")
    target = before if closing_balance is None else Decimal(closing_balance)
    if not target.is_finite() or target != target.quantize(Decimal("0.01")):
        raise ValueError("Closing balance must be finite with at most two decimal places")
    if not 1 <= len(transactions) <= 500:
        raise ValueError("Import between 1 and 500 settled rows")
    rows = [TransactionImport.model_validate(row) for row in transactions]
    assignable = await get_assignable_category_ids(session, ws.id)
    for row in rows:
        if (not row.amount.is_finite() or row.amount <= 0
                or row.amount != row.amount.quantize(Decimal("0.01"))):
            raise ValueError("Amounts must be positive with at most two decimal places")
        if row.type not in ("debit", "credit") or row.date > app_today():
            raise ValueError("Only dated, settled history through today can be imported")
        if not row.description.strip() or not row.external_id:
            raise ValueError("Each row needs a description and a stable external_id")
        if row.currency and row.currency != account.currency:
            raise ValueError("Import native account currency only")
        if row.category_id and row.category_id not in assignable:
            raise ValueError("Category is not assignable in this workspace")
    opening = (await session.execute(select(Transaction).where(
        Transaction.account_id == account.id,
        Transaction.source == "opening_balance",
    ))).scalar_one_or_none()
    if opening and (opening.is_ignored or opening.category_id or opening.pocket_id
                    or opening.status != "posted" or opening.currency != account.currency):
        raise ValueError("Opening balance has custom accounting; reconcile it manually")
    result = {
        "kind": "import_history", "account_id": str(account.id),
        "currency": account.currency, "balance_before": str(before),
        "row_count": len(rows), "preserves_current_balance": target == before,
        "target_balance": str(target),
        "applied": False,
    }
    if not do_apply:
        return result

    try:
        pockets_before = await pocket_service.get_account_pockets(session, ws.id, account.id)
        imported, skipped, excluded, log_id = await import_service.import_transactions(
            session, ws.id, ctx.user_id, account.id, rows, "import",
            filename=filename, detected_format="csv", commit=False,
        )
        await session.flush()
        delta = target - await _balance(session, account)
        signed_opening = Decimal(0)
        if opening:
            signed_opening = opening.amount * (1 if opening.type == "credit" else -1)
        initial = signed_opening + delta
        opening_date = min(row.date for row in rows) - timedelta(days=1)
        if opening is None:
            opening = Transaction(
                user_id=ctx.user_id, workspace_id=ws.id, account_id=account.id,
                description="Saldo inicial", currency=account.currency,
                source="opening_balance", status="posted", date=opening_date,
            )
            session.add(opening)
        else:
            opening.date = min(opening.date, opening_date)
        opening.amount, opening.type = account_service._opening_balance_values(account.type, initial)
        apply_effective_date(opening, account)
        await session.flush()
        after = await _balance(session, account)
        if after != target:
            raise ValueError("Balance reconciliation failed; import rolled back")
        pockets_after = await pocket_service.get_account_pockets(session, ws.id, account.id)
        if ({p.id: p.balance for p in pockets_before.items}
                != {p.id: p.balance for p in pockets_after.items}):
            raise ValueError("History changed pocket allocations; import rolled back")
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    result.update(
        applied=True, imported=imported, skipped=skipped, excluded=excluded,
        import_log_id=str(log_id), balance_after=str(after),
        inferred_opening_balance=str(initial),
    )
    return result
