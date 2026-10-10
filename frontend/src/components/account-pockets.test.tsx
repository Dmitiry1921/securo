import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { AccountPockets } from './account-pockets'
import { renderWithProviders, t } from '@/test/utils'

const mocks = vi.hoisted(() => ({
  canWrite: true,
  list: vi.fn(),
  create: vi.fn(),
  move: vi.fn(),
  update: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ pockets: mocks, goals: { list: async () => [] } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: mocks.canWrite }) }))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.removeItem('privacyMode')
  mocks.canWrite = true
  mocks.list.mockResolvedValue({
    account_id: 'account',
    currency: 'USD',
    balance: '1000',
    unallocated: '400',
    unallocated_available: '400',
    needs_review: false,
    items: [
      {
        id: 'home',
        name: 'Home',
        balance: '600',
        available: '580',
        reserved: '20',
        archived: false,
        needs_review: false,
        goal_id: null,
      },
    ],
  })
  mocks.create.mockResolvedValue({})
  mocks.move.mockResolvedValue({})
})

it('creates a pocket through the account API', async () => {
  const { user } = renderWithProviders(<AccountPockets accountId="account" closed={false} />)
  await user.click(await screen.findByRole('button', { name: t('pockets.create') }))
  await user.type(screen.getByLabelText(t('common.name')), 'Travel')
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  await waitFor(() =>
    expect(mocks.create).toHaveBeenCalledWith({ account_id: 'account', name: 'Travel', goal_id: null }),
  )
})

it('allocates with a stable request key and shows pending reservations', async () => {
  const { user } = renderWithProviders(<AccountPockets accountId="account" closed={false} />)
  expect(await screen.findByText('$580.00')).toBeInTheDocument()
  expect(screen.getByText(`${t('pockets.reserved')}: $20.00`)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: t('pockets.move') }))
  await user.selectOptions(screen.getByLabelText(t('pockets.to')), 'home')
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '100' } })
  await user.click(screen.getByRole('button', { name: t('common.save') }))
  await waitFor(() =>
    expect(mocks.move).toHaveBeenCalledWith({
      account_id: 'account',
      from_pocket_id: null,
      to_pocket_id: 'home',
      amount: '100',
      idempotency_key: expect.any(String),
    }),
  )
})

it('hides amounts in privacy mode and write controls for a viewer', async () => {
  mocks.canWrite = false
  localStorage.setItem('privacyMode', 'true')
  renderWithProviders(<AccountPockets accountId="account" closed={false} />)
  await screen.findByText('Home')
  expect(screen.queryByText('$580.00')).not.toBeInTheDocument()
  expect(screen.queryByText(/400\.00/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: t('pockets.create') })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: t('pockets.move') })).not.toBeInTheDocument()
})
