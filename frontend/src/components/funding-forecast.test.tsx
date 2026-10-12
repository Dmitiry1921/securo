import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
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
    expect(screen.getAllByText(t('funding.shortfall'), { exact: false }).length).toBeGreaterThan(0)
    await user.click(screen.getByText(t('funding.shortfall'), { exact: true }).closest('summary')!)
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
