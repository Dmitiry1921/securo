import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders, t } from '@/test/utils'
import GoalDetailPage from './goal-detail'
import { goals, forecast, transferSchedules } from '@/lib/api'

vi.mock('@/lib/api', () => ({ goals: { get: vi.fn() }, forecast: { calculate: vi.fn(), scenarios: vi.fn() }, transferSchedules: { list: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (value: string) => value }) }))
const target = { id: 'g', name: 'Shared savings', currency: 'USD', current_amount: 100, target_amount: 200, target_date: '2027-04-30', repeat_frequency: 'yearly', next_target_date: '2028-04-30', valuation_available: true }
const plan = { start_date: '2026-10-12', end_date: '2028-10-11', currency: 'USD',
  opening_accounts: [{ account_id: 'a', name: 'Source', currency: 'USD', items: [{ id: 'p', name: 'Savings', goal_id: 'g', currency: 'USD', balance: '100', available: '100', reserved: '0', needs_review: false }], income_rules: [{ id: 'r', account_id: 'a', pocket_id: 'p', percentage: '10', start_date: '2026-10-12', end_date: '2027-04-30' }] }],
  goals: [{ goal_id: 'g', name: target.name, currency: 'USD', target: '200', current: '100', projected: '140', shortfall: '60', deadline: '2027-04-30', achieved_date: null, status: 'shortfall', history: [{ date: '2026-10-12', amount: '100' }, { date: '2027-04-30', amount: '140' }], cycles: [{ deadline: '2027-04-30', projected: '140', shortfall: '60' }, { deadline: '2028-04-30', projected: '1', shortfall: '199' }] }],
  warnings: [], events: [{ id: 'salary', date: '2026-11-05', kind: 'income', status: 'projected', description: 'Salary payment', from_account_id: null, to_account_id: 'a', amount: '500', currency: 'USD', funded: '500', shortfall: '0' }, { id: 'salary:rule:r', date: '2026-11-05', kind: 'allocation', status: 'projected', description: 'Income contribution', from_account_id: 'a', to_account_id: 'a', from_pocket_id: null, to_pocket_id: 'p', amount: '50', currency: 'USD', funded: '50', shortfall: '0' }] }
const render = () => renderWithProviders(<GoalDetailPage />, { route: '/goals/g', path: '/goals/:id' })
beforeEach(() => {
  vi.mocked(goals.get).mockResolvedValue(target as Awaited<ReturnType<typeof goals.get>>)
  vi.mocked(forecast.calculate).mockResolvedValue({ ...plan, goals: [...plan.goals, { ...plan.goals[0], goal_id: 'other', name: 'Unrelated goal' }] } as unknown as Awaited<ReturnType<typeof forecast.calculate>>)
  vi.mocked(forecast.scenarios).mockResolvedValue([{ id: 'scenario', name: 'Delayed' }] as Awaited<ReturnType<typeof forecast.scenarios>>)
  vi.mocked(transferSchedules.list).mockResolvedValue([])
})
describe('Goal detail page', () => {
  it('shows the shared forecast, contribution rules, pockets and future movements', async () => {
    render()
    expect(await screen.findByRole('heading', { name: target.name })).toBeInTheDocument()
    expect((await screen.findByText(/Next goal cycle due .* has no planned contributions/i)).closest('[role="status"]')).toBeInTheDocument()
    expect(forecast.calculate).toHaveBeenCalledWith({ months: 24 })
    expect(screen.getByText(/2028-04-30: \$1\.00/)).toBeInTheDocument()
    expect(await screen.findByText('Income contribution')).toBeInTheDocument()
    expect(screen.getAllByLabelText(t('funding.goalChart'))).toHaveLength(1)
    expect(screen.getByLabelText(t('funding.goalChart')).closest('details')).toBeNull()
    expect(screen.getByText('$60.00')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Source · Savings' })).toHaveAttribute('href', '/accounts/a')
    expect(screen.getByRole('link', { name: t('goalDetail.back') })).toHaveAttribute('href', '/goals')
  })
  it('recalculates the same service when changing horizon or scenario', async () => {
    const { user } = render()
    await screen.findByText('Income contribution')
    await user.selectOptions(screen.getByLabelText(t('funding.scenario')), 'scenario')
    await waitFor(() => expect(forecast.calculate).toHaveBeenCalledWith({ months: 24, scenario_id: 'scenario' }))
    await user.selectOptions(screen.getByLabelText(t('goalDetail.horizon')), '12')
    await waitFor(() => expect(forecast.calculate).toHaveBeenCalledWith({ months: 12, scenario_id: 'scenario' }))
    await user.selectOptions(screen.getByLabelText(t('goalDetail.horizon')), '24')
    await waitFor(() => expect(forecast.calculate).toHaveBeenCalledWith({ months: 24, scenario_id: 'scenario' }))
  })
  it('shows a goal load error instead of inventing savings', async () => {
    vi.mocked(goals.get).mockRejectedValue(new Error('Unavailable'))
    render()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('$100.00')).not.toBeInTheDocument()
  })
})


it('shows actual expected income, date, percentage and contribution instead of fictional examples', async () => {
  render()
  await screen.findByText(/Salary payment/)
  expect(screen.getByText('$500.00', { exact: false })).toBeInTheDocument()
  expect(screen.getByText(t('goalDetail.incomeAllocation', { percentage: '10%', amount: '$50.00' }))).toBeInTheDocument()
  expect(screen.queryByText(/Example:/)).not.toBeInTheDocument()
  expect(screen.getByText(t('goalDetail.rulesConfiguration'))).toBeInTheDocument()
})
