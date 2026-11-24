import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StaffAccountsPage } from './StaffAccountsPage'
import {
  createStaffAccount, deactivateAccount, editAccount, listAccounts, reactivateAccount,
  resetAccountPassword,
} from '../services/staffAccountService'

vi.mock('../services/staffAccountService', () => ({
  listAccounts: vi.fn(), createStaffAccount: vi.fn(), editAccount: vi.fn(),
  deactivateAccount: vi.fn(), reactivateAccount: vi.fn(), resetAccountPassword: vi.fn(),
}))

const users = [
  { id: '00000000-0000-4000-8000-000000000001', full_name: 'Admin User', email: 'admin@example.com', role: 'ADMIN', status: 'ACTIVE' },
  { id: '00000000-0000-4000-8000-000000000002', full_name: 'Valet User', email: 'valet@example.com', role: 'VALET_OPERATOR', status: 'ACTIVE' },
]

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><StaffAccountsPage /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  listAccounts.mockResolvedValue({ users, page: 1, total: 2, pages: 1 })
  editAccount.mockResolvedValue({ user: users[1] })
  resetAccountPassword.mockResolvedValue({ user: users[1] })
  deactivateAccount.mockResolvedValue({ user: { ...users[1], status: 'DEACTIVATED' } })
  reactivateAccount.mockResolvedValue({ user: users[1] })
  createStaffAccount.mockResolvedValue({ user: users[1] })
})

describe('admin account management', () => {
  it('shows the directory, protects Admin, and allows editing a valet', async () => {
    mount()
    expect(await screen.findByText('Valet User')).toBeTruthy()
    expect(screen.getByText('Protected')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const input = screen.getByDisplayValue('Valet User')
    fireEvent.change(input, { target: { value: 'Updated Valet' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(editAccount).toHaveBeenCalledWith(users[1].id, expect.objectContaining({ fullName: 'Updated Valet' })))
  })
  it('requires confirmation for delete and uses soft delete API', async () => {
    mount()
    await screen.findByText('Valet User')
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText(/records are preserved/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Delete / Deactivate' }))
    await waitFor(() => expect(deactivateAccount).toHaveBeenCalledWith(users[1].id))
  })
})
