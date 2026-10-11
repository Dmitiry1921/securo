import type { MoneyMapData, MoneyMapFlow } from '@/types'

export const FLOW_COLORS = {
  income: '#10B981', expense: '#F43F5E', transfer: '#3B82F6',
  allocation: '#8B5CF6', investment: '#0EA5E9',
}

export interface MoneyMapNode {
  id: string
  name: string
  detail: string
  color: string
  pocketId?: string | null
}

export interface MoneyMapLink {
  source: number
  target: number
  value: number
  kind: MoneyMapFlow['kind']
  status: MoneyMapFlow['status']
  flows: MoneyMapFlow[]
}

// Pockets are locations within an account. Only ledger/planned movements form
// links: current balances, goal targets and budget limits are not cash flows.
export function buildMoneyMapGraph(data: MoneyMapData, unallocated: string) {
  const nodes: MoneyMapNode[] = []
  const links: MoneyMapLink[] = []
  const indices = new Map<string, number>()
  const adjacent = new Map<number, Set<number>>()
  const grouped = new Map<string, MoneyMapLink>()
  const accounts = new Map(data.accounts.map(a => [a.account_id, a]))
  const pockets = new Map(data.accounts.flatMap(a => a.items.map(p => [p.id, p] as const)))
  const node = (entry: MoneyMapNode) => {
    const existing = indices.get(entry.id)
    if (existing !== undefined) return existing
    const index = nodes.length
    nodes.push(entry)
    indices.set(entry.id, index)
    return index
  }
  const location = (accountId: string, pocketId: string | null, name: string | null) => {
    const key = `${accountId}:${pocketId ?? 'unallocated'}`
    const existing = indices.get(key)
    if (existing !== undefined) return existing
    return node({ id: key, name: accounts.get(accountId)?.name ?? name ?? accountId,
      detail: pocketId ? pockets.get(pocketId)?.name ?? pocketId : unallocated,
      color: FLOW_COLORS.transfer, pocketId })
  }
  const reaches = (start: number, target: number) => {
    const pending = [start]
    const visited = new Set<number>()
    while (pending.length) {
      const current = pending.pop()!
      if (current === target) return true
      if (visited.has(current)) continue
      visited.add(current)
      pending.push(...(adjacent.get(current) ?? []))
    }
    return false
  }

  for (const flow of [...data.flows].sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id))) {
    const value = flow.amount_primary
    if (value === null || !Number.isFinite(value) || value <= 0) continue
    const source = flow.from_account_id
      ? location(flow.from_account_id, flow.from_pocket_id, flow.from_account_name)
      : node({ id: `income:${flow.category_id ?? flow.description}`, name: flow.category_name ?? flow.description,
        detail: '', color: FLOW_COLORS.income })
    let target = flow.to_account_id
      ? location(flow.to_account_id, flow.to_pocket_id, flow.to_account_name)
      : node({ id: `${flow.kind}:${flow.category_id ?? flow.description}`, name: flow.category_name ?? flow.description,
        detail: '', color: FLOW_COLORS[flow.kind] })

    // Returning money must remain visible. Sankey needs a DAG, so a return to
    // an earlier location continues that same account at a dated later node.
    if (reaches(target, source)) {
      const original = nodes[target]
      target = node({ ...original, id: `${original.id}:${flow.id}`,
        detail: `${original.detail} · ${flow.date}` })
      if (flow.to_account_id) indices.set(`${flow.to_account_id}:${flow.to_pocket_id ?? 'unallocated'}`, target)
    }
    const key = [source, target, flow.kind, flow.status].join(':')
    const existing = grouped.get(key)
    if (existing) {
      existing.value += value
      existing.flows.push(flow)
    } else {
      const link = { source, target, value, kind: flow.kind, status: flow.status, flows: [flow] }
      links.push(link)
      grouped.set(key, link)
      if (!adjacent.has(source)) adjacent.set(source, new Set())
      adjacent.get(source)!.add(target)
    }
  }
  return { nodes, links }
}
