import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { renderWithProviders, t } from '@/test/utils'
import GoalsPage from './goals'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (s: string) => s }) }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: false }) }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'Asia/Makassar' }))
const { fixture } = vi.hoisted(() => ({ fixture: ['House', 'School'].map((name, index) => ({
  id: String(index), name, status: 'active', percentage: 25, tracking_type: 'pockets',
  currency: 'USD', target_amount: 100, current_amount: 25, target_date: '2027-04-30',
})) }))
vi.mock('@/lib/api', () => ({
  goals: { list: vi.fn().mockResolvedValue(fixture) },
  accounts: { list: vi.fn().mockResolvedValue([]) }, assets: { list: vi.fn().mockResolvedValue([]) },
  assetGroups: { list: vi.fn().mockResolvedValue([]) }, currencies: { list: vi.fn().mockResolvedValue([]) },
  forecast: { calculate: vi.fn().mockResolvedValue({ goals: fixture.map(g => ({
    goal_id: g.id, name: g.name, currency: 'USD', target: '100', current: '25', projected: '75',
    shortfall: '25', deadline: g.target_date, achieved_date: null, status: 'shortfall',
    history: [{ date: '2026-10-12', amount: '25' }, { date: '2027-04-30', amount: '75' }], cycles: [],
  })) }) },
}))

describe('Goal chart dialog', () => {
  it('opens only the selected chart, closes with Escape and restores focus to the button', async () => {
    const { user, container } = renderWithProviders(<GoalsPage />)
    const trigger = await screen.findByRole('button', { name: `${t('funding.showGoalChart')}: School` })
    expect(screen.queryByLabelText(t('funding.goalChart'))).not.toBeInTheDocument()
    expect(container.querySelector('details')).toBeNull()
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'School' })
    expect(within(dialog).getAllByLabelText(t('funding.goalChart'))).toHaveLength(1)
    expect(dialog.querySelector('details')).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus()
    expect(within(dialog).queryByText('House')).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    await user.click(screen.getByRole('button', { name: `${t('funding.showGoalChart')}: House` }))
    expect(screen.getByRole('dialog', { name: 'House' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'School' })).not.toBeInTheDocument()
  })
})
