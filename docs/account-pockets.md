# Account pockets (fork preview)

Pockets earmark money already held in a checking, savings or wallet account.
Several pockets can share an account. They inherit its currency and never add
assets, income, expenses or extra accounts to reports.

## Using this version

1. Open an account and use **Pockets → New pocket**.
2. Use **Allocate funds** to move available unallocated money into a pocket,
   release it, or move it to another pocket on the same account.
3. Select a pocket when entering or editing a transaction. A debit spends from
   it; a credit/refund replenishes it. Categories still control budgets/reports.
4. For a real bank transfer, use **Transfer** and select a pocket on either or
   both sides. The two bank transactions carry the pocket assignments. Enter
   the actual destination amount for cross-currency pocket transfers.
5. Create a goal with **Pockets** tracking. Edit each contributing pocket and
   select that goal. One goal can have several pockets on different accounts;
   a pocket funds at most one goal.

The shown pocket amount is available money after Pending outgoing reservations
dated today or earlier, using the workspace's calendar. Future-dated transactions
remain in the forecast and do not reserve current pocket or unallocated money,
even when their status is Pending or Posted. On their planned day, unconfirmed
debits start reserving funds automatically; overdue Pending debits keep reserving
them. Future and Pending credits never increase today's spendable money.
Manual allocations therefore distribute money already held in the account
without having to remove transfers funded by a future salary from the forecast.
Goals use this available amount. Account shortfalls, incompatible imported edits
and missing exchange rates make goal valuation unavailable instead of using 1:1.
FX valuation uses already cached rates and does not fetch or commit during a
pocket allocation. The normal exchange-rate synchronization remains necessary.

Initialization distributes the account's current balance. Older transactions
are not assigned automatically. A transaction predating the pocket's start day
cannot be assigned. Assigning a transaction from the start day is explicit and
changes the pocket immediately, including an already recorded transaction.

## Accounting and integration

- `pockets`: account, workspace, optional goal, name, start date and archive state.
- `pocket_movements`: append-only internal allocations with positive decimal
  amounts, optional source/destination (null means unallocated), creator and
  a unique request key per account. Reverse a movement by recording another.
- `transactions.pocket_id`: optional assignment of the whole transaction. Live
  transaction amount, status, date and ignore flags determine the contribution.
  Updates, deletion, refunds and pending-to-posted transitions therefore do not
  require a second mutable balance or mirrored bank transactions.
- Same-account allocation locks the account row and checks available funds.
  Actual expenses may create negative pocket balances; they are not hidden.
- Pocket endpoints and MCP use the same services and workspace write gate.
  Internal MCP proposals require the normal Apply button. External proposals
  write only with `apply=true`. Membership is checked again on each call.
- MCP tools: `list_pockets`, `propose_pocket`, `propose_assign_pocket`,
  `propose_transfer`; existing transaction and goal proposals accept pockets.
  Enable these tools in an agent's allowed-tool list if its list is restricted.
- Exports include pockets, allocation history, goals and transaction assignments.
  Existing exports are not a full database restore mechanism.

Only empty pockets without pending transactions can be archived. Deleting a
goal unlinks its pockets. Account/workspace deletion removes their pocket data.
An account with pocket history cannot change into an investment/credit account.

## Deliberate boundaries of this first version

- One transaction belongs to one pocket. Multi-pocket receipt splits are not yet
  implemented; the existing shared-expense splits are a different feature.
- Funding schedules, percentage-of-income allocation, weekly top-up suggestions,
  automatic pocket rules and recurring pocket assignments are the next stage.
  Imports and recurring entries remain unallocated until explicitly assigned.
- Foreign-currency transactions must be recorded using their actual account
  currency amount before assignment. A foreign-currency entry on the account
  triggers review, since `amount_primary` is not necessarily account currency.
- No automatic reallocation when the bank balance decreases. Resolve an account
  deficit by releasing earmarks or correcting the bank data.
- Allocation history is stored and exported; its timeline UI is not yet included.
- English, Russian and Portuguese pocket labels are translated. Other locale
  files currently carry English pocket labels.

## Test on your server

### Importing historical statements through MCP

`propose_import_history` imports dated, settled rows using the existing importer
and duplicate detection. It accepts only manual checking, savings and wallet
accounts in the caller's workspace, with amounts in their native currency.
Preview first; an authorized external client applies with `apply=true`.

Supply `expected_balance` as an exact decimal string read from Securo. The tool
locks the account, rejects a changed balance, imports the rows, and reconciles
the existing `opening_balance` transaction in the same database transaction.
The current balance is preserved by default. `closing_balance` may explicitly
replace it with a user-confirmed bank closing balance; it is never inferred from
an incomplete statement. An inferred negative opening balance is a discrepancy
to reconcile, not evidence of a bank overdraft.

Every row needs a stable `external_id`; reuse the same IDs on retries. Rows
without dates, bank pending entries, future dates, foreign currencies and
inaccessible categories are rejected. Historical rows receive no pocket
assignment. If matching a recurring placeholder or an income rule changes
existing pocket balances, the entire import rolls back for manual review.
Transfers should use a transfer category, not an income category. No paired
bank transaction is invented when the other account's statement is missing.

The tool uses cached FX rates so an on-demand FX sync cannot commit a partial
import. Missing primary-currency valuations can be filled by the existing FX
healer. There are no schema migrations or new frontend controls.

Use branch `codex/account-pockets` from your fork. Build the existing
**Build and Push Docker Images** workflow on that branch with `image_tag=pockets`
and the server's architecture. This keeps the preview separate from `latest`.

In the existing Portainer stack, set these images, keeping its existing database,
volumes and environment settings:

```text
backend, worker, beat, mcp-server: ghcr.io/dmitiry1921/securo-backend:pockets
frontend:                        ghcr.io/dmitiry1921/securo-frontend:pockets
```

Use the corresponding backend image for every backend-derived service actually
present in the stack. Keep all services on the same build. The backend startup
runs migration `f001`, adding the pocket tables and optional transaction link.
The fork-specific revision ID avoids reusing upstream's next numeric ID; future
upstream merges still need the migration-chain check before deployment.
Take a database backup before trying a schema migration on your own data.

Smoke check: allocate 100 from an account with 200; verify bank balance stays
200 and unallocated becomes 100. Spend 20 from the pocket; it becomes 80. Link
it to a 200 goal; progress becomes 40%. Repeat using MCP. Check the same account
on mobile and with privacy mode enabled.

Local checks:

```sh
cd backend
.venv/bin/pytest tests/test_pockets.py tests/test_goals_api.py tests/test_agents_mcp_tools.py -q -p no:cacheprovider
.venv/bin/python scripts/check_migration_chain.py
cd ../frontend
npm run build
npm run lint
npm test
```
