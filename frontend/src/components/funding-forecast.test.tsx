import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders, t } from '@/test/utils'
import { GoalForecastDetails, FundingDetails, ForecastSettings } from './funding-forecast'
import type { ForecastGoal, FundingForecast } from '@/types'
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (s: string) => s }) }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'Asia/Makassar' }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: false }) }))
vi.mock('@/lib/api', () => ({ forecast: { rules: vi.fn().mockResolvedValue([]), scenarios: vi.fn().mockResolvedValue([]) } }))
const goal: ForecastGoal = { goal_id: 'g', name: 'Home', currency: 'USD', deadline: '2027-04-30', target: '100', current: '10', projected: '60', shortfall: '40', achieved_date: null, status: 'shortfall', history: [{ date: '2026-10-12', amount: '10' }, { date: '2027-04-30', amount: '60' }], cycles: [] }
const data: FundingForecast = { start_date: '2026-10-12', end_date: '2027-04-30', currency: 'USD', scenario: null, rates: [], opening_accounts: [], timeline: [{ date: '2026-10-12', balance: '100', free_funds: '90', accounts: {}, pockets: {}, event_ids: ['purchase'] }], events: [{ id: 'purchase', date: '2026-10-12', kind: 'expense', status: 'projected', description: 'Food', amount: '10', currency: 'USD', from_account_id: null, to_account_id: null, funded: '10', shortfall: '0' }], goals: [goal], warnings: [], budget_periods: [], history_recommendations: [], ending_balance: '60', free_funds: '10' }
describe('Funding forecast', () => {
  it('shows the deadline shortfall and opens the savings chart', async () => {
    const { user } = renderWithProviders(<GoalForecastDetails goal={goal} />)
    expect(screen.queryByLabelText(t('funding.goalChart'))).not.toBeInTheDocument()
    await user.click(screen.getByText(t('funding.showGoalChart')))
    expect(screen.getByLabelText(t('funding.goalChart'))).toBeInTheDocument()
    expect(screen.getByText('$40.00')).toBeInTheDocument()
  })
  it('does not present an unvalued projection as zero or achieved', () => {
    renderWithProviders(<GoalForecastDetails goal={{ ...goal, status: 'unknown', projected: null, shortfall: null, reason: 'missing_fx' }} />)
    expect(screen.getAllByText(t('funding.unknown'), { exact: false }).length).toBeGreaterThan(0)
    expect(screen.queryByText(t('funding.funded'))).not.toBeInTheDocument()
  })
  it('explains the selected date without posting transactions', async () => {
    renderWithProviders(<FundingDetails data={data} selectedDate="2026-10-12" />)
    expect(screen.getByText('Food')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2026-10-12')).toBeInTheDocument()
    expect(screen.getByText('Food').closest('li')).toHaveTextContent('Projected')
  })
  it('hides editing controls from a read-only member', async () => {
    const { user } = renderWithProviders(<ForecastSettings />)
    await user.click(screen.getByText(t('funding.settings')))
    expect(screen.queryByRole('button', { name: t('common.add') })).not.toBeInTheDocument()
  })
})


describe('Goal chart uncertainty states', () => {
  it('explains missing points and distinguishes pocket plan from funded savings', () => {
    renderWithProviders(<GoalForecastDetails defaultOpen goal={{ ...goal, status: 'unknown', projected: null, shortfall: null,
      history: [{ date: '2026-10-12', amount: '10', allocated_amount: '10' },
                { date: '2027-04-30', amount: null, allocated_amount: '50' }] }} />)
    expect(screen.getByText(t('funding.goalChartUncertain'))).toBeInTheDocument()
    expect(screen.getByText(t('funding.goalPocketPlan'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('funding.goalChart'))).toBeInTheDocument()
  })
  it('shows an explicit empty state when no amount can be valued', () => {
    renderWithProviders(<GoalForecastDetails defaultOpen goal={{ ...goal, status: 'unknown', projected: null, shortfall: null,
      history: [{ date: '2026-10-12', amount: null, allocated_amount: null }] }} />)
    expect(screen.getByText(t('funding.goalChartEmpty'))).toBeInTheDocument()
    expect(screen.queryByLabelText(t('funding.goalChart'))).not.toBeInTheDocument()
  })
})


describe('Goal chart calendar and isolated points', () => {
  it('spaces dates by elapsed time rather than row order', async () => {
    const { container } = renderWithProviders(<GoalForecastDetails defaultOpen goal={{ ...goal,
      history: [{ date: '2026-01-01', amount: '10' }, { date: '2026-01-02', amount: '20' },
                { date: '2026-07-01', amount: '30' }] }} />)
    await waitFor(() => expect(container.querySelector('path.recharts-line-curve')).toBeTruthy())
    const path = container.querySelector('path.recharts-line-curve')!.getAttribute('d')!
    const x = [...path.matchAll(/[ML](-?[\d.]+),/g)].map(m => Number(m[1]))
    const distinct = [...new Set(x)]
    expect(distinct.length).toBe(3)
    expect(distinct[1] - distinct[0]).toBeLessThan((distinct[2] - distinct[0]) / 100)
  })
  it('renders a dot for one known amount and does not bridge unknown dates', async () => {
    const { container } = renderWithProviders(<GoalForecastDetails defaultOpen goal={{ ...goal,
      history: [{ date: '2026-10-12', amount: '10' }, { date: '2027-04-30', amount: null }] }} />)
    await waitFor(() => expect(container.querySelector('.recharts-line-dot')).toBeTruthy())
    expect(container.querySelectorAll('.recharts-line-dot')).toHaveLength(1)
    expect(screen.getByText(t('funding.goalChartUncertain'))).toBeInTheDocument()
  })
})


describe('Goal chart display context', () => {
  it('shows only the forecast summary in the goals list', () => {
    const { container } = renderWithProviders(<GoalForecastDetails goal={goal} view="summary" />)
    expect(screen.queryByLabelText(t('funding.goalChart'))).not.toBeInTheDocument()
    expect(container.querySelector('details')).toBeNull()
    expect(screen.getByText(t('funding.shortfall'))).toBeInTheDocument()
    expect(screen.queryByText(t('funding.showGoalChart'))).not.toBeInTheDocument()
  })
  it('shows the chart directly on the goal page without disclosure controls', () => {
    const { container } = renderWithProviders(<GoalForecastDetails goal={goal} view="chart" />)
    expect(screen.getByLabelText(t('funding.goalChart'))).toBeInTheDocument()
    expect(container.querySelector('details')).toBeNull()
    expect(screen.queryByText(t('funding.hideGoalChart'))).not.toBeInTheDocument()
  })
})
