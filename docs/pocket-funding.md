# Pocket funding

Funding uses the existing account balances, pockets, goals and native transfer
pairs. No operation in this feature sends bank money. Planned transfers stay
Pending until manually confirmed with actual amounts.

## Prorated weekly transfers

In **Recurring → Planned transfers**, choose **Monthly envelope split by days**.
The plan's `amount` is a monthly envelope, not an amount per occurrence.
`cycle_day` is the monthly funding day; `weekday` uses Monday=0, Sunday=6.
The API/MCP representation also requires `frequency=days`, `interval=7`.

The monthly cycle is `[funding day, next funding day)`. Funding days clamp to
the last day in shorter months using the existing calendar helper. Transfers
occur on the chosen weekday, on funding day, and once at the initial start
date if it is neither. Coinciding dates produce one occurrence.

Each transfer covers `[occurrence, next weekday or funding day)`. The amount
is the difference between two rounded cumulative daily allowances. This makes
complete cycles total exactly the monthly envelope, including rounding cents.
A partial initial or final cycle only pays for its covered days. You may set a separate `initial_cycle_amount`: it distributes that remaining amount
from `start_date` until the next funding day, then uses the usual monthly envelope.
For example, 15,326,525 IDR from October 12 until November 5 is distributed over
24 days; every complete cycle from November 5 uses 25,000,000 IDR. One plan
continues indefinitely when `end_date` is empty. No individual future transfers
need to be created by a user or MCP client. Amounts do
not automatically adapt to a delayed salary or actual spending; edit the plan
or confirm a different observed amount when the banking facts change.

Example: 25,000,000 IDR, funding day 5, Monday transfers, starting November 5,
2026. The November 5 → December 5 cycle has 30 days:

| Date | Days covered | IDR |
|---|---:|---:|
| November 5 | 4 | 3,333,333.33 |
| November 9 | 7 | 5,833,333.34 |
| November 16 | 7 | 5,833,333.33 |
| November 23 | 7 | 5,833,333.33 |
| November 30 | 5 | 4,166,666.67 |
| Total | 30 | 25,000,000.00 |

Projection, materialization and confirmation reuse the same services as fixed
transfer plans. Already materialized Pending and Posted rows are snapshots and
remain unchanged after editing a plan. Transfer pairs remain excluded from P&L
and budgets; bank exchange amounts are estimated until confirmed.

One-off Pending pairs can be removed atomically through MCP
`propose_delete_pending_transfer(transfer_pair_id=...)` or
DELETE `/api/transfer-schedules/pairs/{id}`. Posted pairs and scheduled
occurrences are rejected by this operation; deleting a scheduled occurrence
would otherwise make its generator recreate it.

## Income allocation rules

On any eligible account's **Pockets**, use **Income allocation** to choose a
pocket, percentage and start date. The same settings work through MCP
`propose_pocket(operation=income_rule, data={id, account_id, pocket_id,
percentage, start_date, end_date?})`. `id` is a stable UUID for safe retries.
`list_pockets` returns the rules alongside balances.

Only posted, nonfuture credit transactions count. The native P&L filter excludes
paired/category transfers, ignored rows/categories and excluded transactions.
Opening balances and group settlement reimbursements are also excluded. The
amount must be in the account currency. Changing an account's balance alone
is not a new income transaction and does not activate the rule.

Allocations are derived from those authoritative transactions, like explicit
pocket assignments. No read writes movements, charges or income. The pocket
receives the rule's fraction; unallocated money (or an explicitly assigned
source pocket) supplies it. Total account balance stays unchanged. Correcting,
ignoring or deleting income updates its allocation without duplicates. Multiple
rules share cumulative rounding so allocation never exceeds the income.

Rules cannot retroactively allocate old income. An account lock prevents
concurrent rules from exceeding 100% or overlapping for the same pocket.
Percentage/start date/pocket are immutable. To change one, close the old rule
through a date and create the replacement starting the following day. Closing
preserves past allocations. API: POST `/api/pockets/income-rules`, PATCH
`/api/pockets/income-rules/{id}` with `{end_date}`. MCP:
`propose_pocket(operation=stop_income_rule, rule_id=..., data={end_date})`.
Rules appear in workspace JSON exports as `pocket_income_rules.json`.

The rule trusts the ledger's Posted status. For manually confirmed income,
turn off automatic generation on the salary/bonus recurring templates: their
forecast remains Projected, and record the actual posted income when received.

## Refill goals

Pocket goals can choose `pocket_balance_basis=balance` for the assigned amount
before Pending reservations, or `available` (the existing default) for money
after reservations. A refill goal with 600 assigned and 200 Pending thus
shows 600 funded; an available-money goal shows 400. Reservation checks and
payment validation always continue using available money.

Linking the source and destination food pockets to the same monthly goal shows
their combined funding without counting an internal transfer as spending.
Recurring goals retain manual complete/restart and never create an expense
just because a refill target is reached. Complete a refill milestone without
an expense; record actual food/utility spending as native transactions.

## Deployment and local preview

Migration `f005` follows `f004`. Update backend, worker, beat and MCP together,
plus frontend. Existing transfer plans remain fixed; existing goals keep
available-money tracking. The same MCP tools acquire new fields/operations.

```sh
cd backend
PYTHONPATH=. .venv/bin/python scripts/preview_pocket_funding.py
cd ../frontend
npm run typecheck
npm run lint -- --max-warnings=0
npm run build
```

The preview creates an isolated in-memory SQLite database with synthetic data
and prints allocation changes and goal funding. It never connects to the user's
database or MCP. Unit test files are unchanged.
