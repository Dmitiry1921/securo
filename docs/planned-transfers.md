# Planned internal transfers

Transfers keep the existing two-row ledger representation (`transfer_pair_id`)
so account balances, pockets and P&L exclusions continue to agree. Both legs
can be Pending. Changing their status confirms both; the confirmation form
additionally accepts the actual source and destination amounts and payment date.

## UI

**Recurring → Planned transfers** supports schedules, editing, pausing and
confirmation of pending pairs. **Transfer** also supports a one-off Pending pair.
Schedules fix an amount in either account's currency; the other leg is estimated
using cached real exchange rates. Confirmation records actual amounts.

A schedule repeats every N days, or every N calendar months on a chosen day.
Day 31 clamps in February, then returns to 31 in March. Start date is a lower
bound; a chosen monthly day before it first occurs in the next interval. End
date is inclusive. Pausing stops future virtual/generative occurrences; existing
Pending and Posted transfers remain in the ledger.

The existing recurring worker materializes due occurrences as Pending even for
manual accounts. Future dates stay virtual, or can be explicitly materialized
via API/MCP. The schedule ID plus immutable nominal date identifies each
occurrence; changing the actual date never recreates its forecast. Row locks
and a unique constraint prevent duplicate pairs on concurrent generation.

Transfers appear on both accounts, in the calendar and balance forecasts,
including account-filtered cash-flow reports. They are excluded from budgets
and income/expense totals. Pending movement between two pockets linked to one
goal retains the goal's savings until the eventual expense is paid.

## MCP

- `list_transfer_schedules`
- `propose_transfer_schedule`: create/replace, including `is_active=false` to pause
- `propose_transfer_occurrence`: one schedule/date → existing or new Pending pair
- `list_pending_transfers`
- `propose_confirm_transfer`: actual amounts/date → both legs Posted
- Existing `propose_transfer` accepts `status=pending`.

Mutations retain the shared preview/`apply` permission flow. No tool sends money
through a bank. Workspace ownership, write permissions and pocket compatibility
are validated by the same service used by HTTP.

## Kindergarten example

DEEL “Детский сад — накопления” → BCA Katya “Детский сад — оплата”.
Fixed destination: 21,000,000 IDR. Repeat every 3 months on day 31, starting
2026-10-31, before the quarterly school payment on November 1. The nominal
transfer dates are October 31, January 31, April 30 and July 31. The USD amount
is an estimate until the withdrawal actually occurs. Funding is allocated by
the user; scheduling alone does not allocate or post bank money.

## Deployment and checks

Migration `f004` follows the existing recurring-goal migration `f003`.
Update backend, worker, beat and mcp-server together, plus frontend.

```sh
cd backend
.venv/bin/python -m pytest tests/test_scheduled_transfers.py tests/test_transfer_api.py tests/test_recurring_goals.py tests/test_transaction_calendar_service.py tests/test_report_service.py
# PostgreSQL locking check, using a disposable test database:
POSTGRES_TEST_URL=postgresql+asyncpg://... .venv/bin/python -m pytest tests/test_scheduled_transfers_postgres.py
cd ../frontend
npm run typecheck
npm run build
```
