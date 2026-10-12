# Funding forecast

## Model

Forecasts are settings and calculated views, never another ledger. Expense rules and optional what-if scenarios live in `forecast_settings` (migration `f007`). Existing Money Map projection, recurring occurrence deduplication, pocket snapshots, budgets, and workspace authorization are reused.

The simulator starts with current native account/pocket balances. Posted operations are not replayed. It uses Decimal amounts and cent rounding. Receipt/withdrawal dates can differ: funds in transit remain in family wealth once. Expected income is processed before allocations, transfers, and payments; dependencies on receipt that same day are exposed. A past-due Pending is an obligation for today with a reconciliation warning, not an automatic posting.

Pocket allocations are limited by unallocated funds. They do not change bank wealth. Payments/withdrawals remain visible even when there is insufficient funding, yielding negative bank balances or warnings that protected pockets would be required. No pocket is automatically raided to make a forecast succeed. Unbacked or unpriced goal valuations are unknown, never presented as funded.

## Expense rules

Each rule selects a paying account, optional paying pocket, native currency, budget or explicit amount, cycle day, distribution, categories, and optional accounts whose real purchases consume the same envelope. Multiple cards do not multiply the budget. Active overlapping category/pocket matchers are rejected. A payments-only rule maps existing planned expenses to an earmarked pocket without generating another expense (e.g. annual rent from its saving pocket).

Remaining forecast = max(0, period budget - matched posted net purchases - matching future commitments). Exceeded periods are flagged. Already materialized Pending and recurring projections consume the envelope rather than being added twice. Weekly slots are weighted by covered days; truncated horizons do not accelerate spending. A month-specific budget override wins over the effective recurring budget.

Matching purchase refunds/reimbursements reduce the expense envelope. Credits explicitly identified as external income, principal financing, or other movements do not do so. Non-earned credits in future projections are not allocated again by earned-income percentage rules. These roles are visible in the plan; historical report classifications are not silently rewritten.

Uncategorized purchases are a visible limitation; do not assume their purpose. Historical recommendations require an explicit reviewed coverage window containing six completed cycles. Recurring payments and own transfers are excluded; the recommendation is a median/range with source periods, never an automatic budget change.

## Goals and scenarios

All active goals appear; targets without linked planning resources show unknown feasibility. Deadline values use the funded balance before that day's goal payment, and recurring goals expose later cycles. Goal/pocket history uses the same event simulation as the account report. Contributions whose rules stop before the horizon are explicitly flagged.

Scenarios can omit chosen income plans, delay expected receipts, change income/expense assumptions, and override native-to-reporting FX. Allocation phases are recalculated at the delayed receipt date. Cross-currency planned transfers keep their recipient target. Unknown rates yield null values, not 1:1 or zero. Rate provenance and dates are included. Forecast reads use cached FX only and never refresh/write rates.

## UI and interfaces

- **Cash Flow**: plan mode uses the shared forecast; legacy cash-flow mode remains available.
- **Money Map → Plan**: the existing routes include budget purchases and funded allocations; warnings, date explanations, pocket charts, and goal feasibility are below the controls.
- **Goals**: expandable forecast details per active goal.
- **Forecast settings** in reports: expense rules, payment-pocket mapping, saved scenarios, optional history coverage.

HTTP: `/api/forecast/rules`, `/scenarios`, `/calculate`; PUT settings by stable UUID; DELETE by kind/id. Reads and scenario calculations never post transactions. Writes require workspace edit rights.

MCP: `list_forecast_settings`, `get_funding_forecast`, `propose_forecast_setting`; the existing plan Money Map and `get_cash_flow(funding=true)` use the same domain calculation. Future custom Money Map windows simulate from today before filtering the displayed routes; historical windows retain their original meaning. Internal proposals are applied with Securo's normal Apply card; external calls preview first, then apply with the same stable ID.

## Deployment

Deploy backend, worker, beat, MCP server, and frontend from the same revision. Newly introduced controls are translated in English/Russian; other supported locales use English fallback strings, including previously missing planning keys. Run `alembic upgrade head` once before new APIs are used (the repository Compose backend command does this before starting Uvicorn). `f007` creates planning settings only; it does not alter transactions, balances, or existing allocation rules. Reload MCP tools after server update. Do not enable historical coverage claims or alter financial records merely to eliminate a warning.

## Goal detail

Click a goal name in Goals to open `/goals/:id`. The page reads the existing goal,
shared family forecast, saved scenarios and native transfer schedules. It shows
current savings, the target/deadline, the shared forecast chart, linked pockets,
income allocation phases and future movements. Changing the horizon or scenario
recalculates the same family plan; it does not create transactions or another
independent goal balance. Transfers between two pockets of the same goal are
labelled internal, including pairs with different settlement dates.

A family account deficit stays visible even when the selected goal is conditionally
funded. Unknown amounts, missing rates and deadlines outside the horizon remain
unknown. Forecast shortfalls include their native source currency; cross-currency
transfer rows also expose the expected recipient amount.

## Reconcile a settled bank leg

`propose_update_transaction` exposes native single-row PATCH for status, category,
bank date and notes. It cannot change amounts or create an operation. Use preview
first, and `expected_status` to guard an external reconciliation. Confirm settlement
from the bank or account owner before posting a Pending row. For mixed-status
transfer pairs, update only the outstanding leg; an already Posted counterpart is
not replayed. Identical retries are safe. Workspace write permissions apply to
preview and apply, and internal agent calls remain proposals for user review.

A pocket-matched expense rule may use a category budget as its amount source.
That source budget is covered by the forecast envelope, while unrelated historical
purchases in the old category are still flagged for review rather than silently
assigned to the pocket.
