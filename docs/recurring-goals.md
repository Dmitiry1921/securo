# Recurring goals

Goals can repeat weekly, every two weeks, monthly, quarterly, every six months,
or yearly. Repetition requires a deadline and manual or pocket tracking.
The existing calendar helper preserves the intended day through short months.

In Goals, edit a goal and choose **Repeat**. Pocket goals show available funds,
the amount still needed, and the existing monthly contribution / on-track estimate.
This is a funding estimate, not a prediction of future income. Pending reservations
are excluded from available pocket funds.

**Complete** opens a confirmation dialog with three choices:

- Record a posted expense from one linked pocket, specifying actual amount in that
  account's native currency, payment date and optional category.
- Use a posted expense already assigned to a linked pocket. No second expense is
  created. The selector shows the latest 100 eligible expenses.
- Complete without an expense (for milestones or funds that remain saved).

Completion saves a cycle snapshot. A recurring goal then offers **Start next cycle**.
The new deadline is one scheduled period after the previous deadline; overdue
periods are not silently skipped. Leftover pocket funds carry forward. Manual
progress resets to zero. There is no automatic bank transfer, automatic charge,
or external reminder notification.

Example: a pocket holds 22 million IDR against a 21 million goal. Completing it
with a 21 million expense leaves 1 million in the account's pocket. Restarting
sets the next deadline and begins with that 1 million already funded. Completing
without an expense leaves the full 22 million available for the next cycle.

The regular transaction service creates expenses, so account balances, categories,
budgets and reconciliation suggestions behave like other manual expenses. Existing
recurring payment plans remain independent; reconcile the actual expense with a
matching plan in the usual workflow. A goal cycle does not duplicate that plan.

## API and MCP

- Goal create/update accepts `repeat_frequency` (null disables repetition).
- `POST /api/goals/{id}/complete`: `cycle_number` plus optional `payment`
  (`pocket_id`, `amount`, `date`, optional `category_id`) OR `transaction_id`.
- `POST /api/goals/{id}/restart`: the completed `cycle_number`.
- `GET /api/goals/{id}/history`: completion snapshots and payment references.
- `GET /api/goals/{id}/funding`: linked native pockets and eligible expenses.
- MCP: `propose_create_goal`, `propose_update_goal`, `propose_complete_goal`,
  `propose_restart_goal`, `get_goal_history`, `get_goal_funding`, `list_goals`.
  Proposal tools use the existing preview/apply convention, including internal
  agent proposal cards. Never infer payment from reaching 100% funding.

The cycle number and row lock prevent a stale click from charging the next cycle.
A unique history row per goal/cycle and unique payment reference guard against
reusing an expense. Creating a payment and completing a goal commit together;
FX uses stored rates inside that transaction. Missing pocket valuation, insufficient
available funds, future payment dates and invalid workspace access reject the action.
Migration `f003` is additive. Existing goals keep their settings and remain one-time.
Workspace backups include recurrence fields and `goal_completions.json`.

Local verification:

```sh
cd backend
.venv/bin/python -m pytest tests/test_recurring_goals.py tests/test_goals_api.py tests/test_goal_service_coverage.py tests/test_pockets.py -q
cd ../frontend
npm run typecheck
npm test -- src/components/goal-cycle-dialog.test.tsx src/components/account-pockets.test.tsx
```
