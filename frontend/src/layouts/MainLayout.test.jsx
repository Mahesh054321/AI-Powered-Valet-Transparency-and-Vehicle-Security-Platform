import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MainLayout } from './MainLayout'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'
import { readStoredToken } from '../utils/tokenStorage'
import { apiPost } from '../services/apiClient'

// Mock the module, not the namespace: authService holds a direct binding.
vi.mock('../services/apiClient', async (importActual) => {
  const actual = await importActual()
  return { ...actual, apiPost: vi.fn() }
})

function tokenFor(role) {
  return buildToken({ role, email: `${role.toLowerCase()}@valet.local` })
}

/** Render the layout for a given role. `null` means signed out. */
function renderAs(role) {
  clearSession()
  return renderWithProviders(<MainLayout />, {
    route: '/status',
    token: role ? tokenFor(role) : null,
  })
}

describe('MainLayout navigation', () => {
  it('shows only customer screens to a customer', () => {
    renderAs('CUSTOMER')

    expect(screen.getByRole('link', { name: /system status/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /customer dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /live tracking/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /valet dashboard/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /audit log/i })).not.toBeInTheDocument()
  })

  it('shows operator screens to a valet operator but not manager screens', () => {
    renderAs('VALET_OPERATOR')

    expect(screen.getByRole('link', { name: /valet dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /vehicle check-in/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /manager dashboard/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /reports/i })).not.toBeInTheDocument()
  })

  it('shows the full operations menu to a manager', () => {
    renderAs('VALET_MANAGER')

    expect(screen.getByRole('link', { name: /manager dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /reports/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /audit log/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /my vehicles/i })).not.toBeInTheDocument()
  })

  it('displays the signed-in identity and role', () => {
    renderAs('ADMIN')

    expect(screen.getByText('admin@valet.local')).toBeInTheDocument()
    expect(screen.getByText('Administrator')).toBeInTheDocument()
  })

  it('always surfaces the telemetry provenance label', () => {
    renderAs('CUSTOMER')

    expect(screen.getByText(/simulated telemetry/i)).toBeInTheDocument()
  })

  it('offers a sign-in link when there is no session', () => {
    renderAs(null)

    expect(screen.getByRole('link', { name: /sign in/i })).toBeInTheDocument()
  })
})


describe('MainLayout sign-out', () => {
  beforeEach(() => {
    vi.mocked(apiPost).mockReset()
  })

  it('revokes the token on the server, not just locally', async () => {
    // Regression: the button used to call the raw context action, which
    // discarded the token here while leaving it valid until its own expiry.
    const user = userEvent.setup()
    vi.mocked(apiPost).mockResolvedValue({ message: 'Signed out.' })
    renderAs('CUSTOMER')

    await user.click(screen.getByRole('button', { name: /sign out/i }))

    // One argument, not (path, undefined): authService calls apiPost with the
    // path alone, so the assertion must not invent a second parameter.
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/v1/auth/logout'))
    expect(readStoredToken()).toBeNull()
  })

  it('still clears the local session when the API is unreachable', async () => {
    // Being unable to reach the server is not a reason to keep a token on disk.
    const user = userEvent.setup()
    vi.mocked(apiPost).mockRejectedValue(new Error('network down'))
    renderAs('CUSTOMER')

    await user.click(screen.getByRole('button', { name: /sign out/i }))

    await waitFor(() => expect(readStoredToken()).toBeNull())
  })
})
