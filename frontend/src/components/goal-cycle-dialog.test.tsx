import { screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { GoalCycleDialog } from './goal-cycle-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { Goal } from '@/types'

const mocks = vi.hoisted(() => ({ funding: vi.fn(), history: vi.fn(), complete: vi.fn(), restart: vi.fn() }))
vi.mock('@/lib/api', () => ({ goals: mocks, categories: { list: async () => [] } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
const goal = { id: 'goal', name: 'School', target_amount: 500, currency: 'USD', tracking_type: 'pockets', status: 'active', cycle_number: 1, repeat_frequency: 'quarterly' } as Goal
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.removeItem('privacyMode')
  mocks.funding.mockResolvedValue({ pockets: [{ id: 'pocket', name: 'School', account_name: 'BCA', currency: 'USD', available: '600' }], payments: [{ id: 'paid', description: 'School fees', date: '2026-10-10', amount: '500', currency: 'USD' }] })
  mocks.history.mockResolvedValue([])
  mocks.complete.mockResolvedValue({ ...goal, status: 'completed', next_target_date: '2027-02-01' })
  mocks.restart.mockResolvedValue({ ...goal, cycle_number: 2 })
})

it('records the explicit payment and offers a separate restart', async () => {
  const onClose = vi.fn()
  const { user } = renderWithProviders(<GoalCycleDialog goal={goal} onClose={onClose} />)
  const button = await screen.findByRole('button', { name: t('goals.cycles.payAndComplete') })
  await waitFor(() => expect(button).toBeEnabled())
  await user.click(button)
  await waitFor(() => expect(mocks.complete).toHaveBeenCalledWith('goal', { cycle_number: 1, payment: { pocket_id: 'pocket', amount: 500, date: expect.any(String), category_id: undefined } }))
  expect(mocks.restart).not.toHaveBeenCalled()
  await user.click(await screen.findByRole('button', { name: t('goals.cycles.restart') }))
  await waitFor(() => expect(mocks.restart).toHaveBeenCalledWith('goal', 1))
  expect(onClose).toHaveBeenCalled()
})

it('uses an existing payment without creating a second expense', async () => {
  const { user } = renderWithProviders(<GoalCycleDialog goal={goal} onClose={vi.fn()} />)
  await screen.findByRole('spinbutton')
  await user.selectOptions(screen.getAllByRole('combobox')[0], 'existing')
  await user.selectOptions(screen.getAllByRole('combobox')[1], 'paid')
  await user.click(screen.getByRole('button', { name: t('goals.complete') }))
  await waitFor(() => expect(mocks.complete).toHaveBeenCalledWith('goal', { cycle_number: 1, transaction_id: 'paid' }))
})

it('prevents charging an empty pocket', async () => {
  mocks.funding.mockResolvedValue({ pockets: [{ id: 'pocket', name: 'School', account_name: 'BCA', currency: 'USD', available: '0' }], payments: [] })
  renderWithProviders(<GoalCycleDialog goal={goal} onClose={vi.fn()} />)
  await screen.findByRole('spinbutton')
  expect(screen.getByRole('button', { name: t('goals.cycles.payAndComplete') })).toBeDisabled()
})
