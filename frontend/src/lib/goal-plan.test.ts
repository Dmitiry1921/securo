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

it('links contribution amounts to the exact source income and rule, never just to a date', () => {
  const fixture = { ...data, events: [
    { id: 'salary', kind: 'income', date: '2026-11-05', to_account_id: 'a', amount: '4908', currency: 'USD', description: 'Salary' },
    { id: 'bonus', kind: 'income', date: '2026-11-05', to_account_id: 'a', amount: '2454', currency: 'USD', description: 'Bonus' },
    { id: 'salary:rule:r', kind: 'allocation', from_account_id: 'a', to_pocket_id: 'p', amount: '1272.15', funded: '1200', shortfall: '72.15' },
    { id: 'bonus:rule:r', kind: 'allocation', from_account_id: 'a', to_pocket_id: 'p', amount: '2208.60', funded: '2208.60', shortfall: '0' },
    { id: 'missing:rule:r', kind: 'allocation', from_account_id: 'a', to_pocket_id: 'p', amount: '10' },
    { id: 'manual', kind: 'allocation', to_pocket_id: 'p' },
  ] } as unknown as FundingForecast
  const result = goalPlan(fixture, 'g').contributions
  expect(result.map(r => r.income?.id)).toEqual(['salary', 'bonus', undefined])
  expect(result[0].event.amount).toBe('1272.15')
  expect(result[0].event.shortfall).toBe('72.15')
  expect(result[0].rule?.id).toBe('r')
  expect(result[2].income).toBeUndefined()
  expect(goalPlan({ ...fixture, events: [] }, 'g').contributions).toEqual([])
})
