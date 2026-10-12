import type { FundingForecast } from '@/types'

/** Select existing forecast resources and events; never calculate a second balance. */
export function goalPlan(data: FundingForecast, goalId: string) {
  const linked = data.opening_accounts.flatMap(account => account.items
    .filter(pocket => pocket.goal_id === goalId)
    .map(pocket => ({ account, pocket })))
  const pocketIds = new Set(linked.map(item => item.pocket.id))
  const events = data.events.filter(event =>
    pocketIds.has(event.from_pocket_id ?? '') || pocketIds.has(event.to_pocket_id ?? ''))
  const incomeRules = data.opening_accounts.flatMap(account => (account.income_rules ?? [])
    .filter(rule => pocketIds.has(rule.pocket_id) && (!rule.end_date || rule.end_date >= data.start_date))
    .map(rule => ({ account, rule })))
  const pairId = (id: string) => id.endsWith(':receipt') ? id.slice(0, -8) : id
  const internalPairs = new Set(events.filter(e => e.kind === 'transfer').map(e => pairId(e.id))
    .filter(id => {
      const legs = events.filter(e => pairId(e.id) === id)
      return legs.some(e => pocketIds.has(e.from_pocket_id ?? '')) && legs.some(e => pocketIds.has(e.to_pocket_id ?? ''))
    }))
  const direction = (event: FundingForecast['events'][number]) => {
    const from = pocketIds.has(event.from_pocket_id ?? '')
    const to = pocketIds.has(event.to_pocket_id ?? '')
    return from && to || internalPairs.has(pairId(event.id)) ? 'internal' : to ? 'inflow' : 'outflow'
  }
  return { linked, pocketIds, events, incomeRules, direction }
}
