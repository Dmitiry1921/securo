import { describe, expect, it } from 'vitest'
import { goalPlan } from './goal-plan'
import type { FundingForecast } from '@/types'

const data = {
  start_date: '2026-10-12', opening_accounts: [
    { account_id: 'a', name: 'Source', items: [{ id: 'p', goal_id: 'g' }], income_rules: [{ id: 'r', pocket_id: 'p', end_date: null }] },
    { account_id: 'b', name: 'Destination', items: [{ id: 'q', goal_id: 'g' }, { id: 'other', goal_id: 'h' }], income_rules: [] },
  ], events: [
    { id: 'in', from_pocket_id: null, to_pocket_id: 'p' },
    { id: 'move', from_pocket_id: 'p', to_pocket_id: 'q' },
    { id: 'pay', from_pocket_id: 'q', to_pocket_id: null },
    { id: 'unrelated', from_pocket_id: 'other', to_pocket_id: null },
  ],
} as unknown as FundingForecast

describe('Goal resource selection', () => {
  it('includes both linked accounts and lists an internal transfer once', () => {
    const plan = goalPlan(data, 'g')
    expect(plan.linked).toHaveLength(2)
    expect(plan.events.map(e => e.id)).toEqual(['in', 'move', 'pay'])
    expect(plan.events.map(plan.direction)).toEqual(['inflow', 'internal', 'outflow'])
    expect(plan.incomeRules).toHaveLength(1)
  })
  it('recognizes an internal transfer with different settlement dates', () => {
    const delayed = { ...data, events: [
      { id: 'pair', kind: 'transfer', from_pocket_id: 'p', to_pocket_id: null },
      { id: 'pair:receipt', kind: 'transfer', from_pocket_id: null, to_pocket_id: 'q' },
    ] } as unknown as FundingForecast
    const plan = goalPlan(delayed, 'g')
    expect(plan.events.map(plan.direction)).toEqual(['internal', 'internal'])
  })
  it('never borrows another goal’s movements or supplies a fake plan', () => {
    const plan = goalPlan(data, 'missing')
    expect(plan.events).toEqual([])
    expect(plan.linked).toEqual([])
  })
})
