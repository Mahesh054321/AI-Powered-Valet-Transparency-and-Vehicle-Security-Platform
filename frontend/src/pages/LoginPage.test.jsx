import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { LoginPage } from './LoginPage'
import { useAuth } from '../hooks/useAuth'
import { readStoredRefreshToken, readStoredToken } from '../utils/tokenStorage'
import { clearSession, renderWithProviders } from '../test/utils.jsx'
import { apiPost } from '../services/apiClient'

// Mock the module, not the namespace: LoginPage holds a direct binding to `apiPost`.
vi.mock('../services/apiClient', async (importActual) => {
  const actual = await importActual()
  return { ...actual, apiPost: vi.fn() }
})

describe('LoginPage', () => {
  beforeEach(() => {
    clearSession()
    vi.mocked(apiPost).mockReset()
  })

  it('blocks submission and explains each invalid field', async () => {
    const user = userEvent.setup()
    renderWithProviders(<LoginPage />, { route: '/login' })

    await user.type(screen.getByPlaceholderText('you@example.com'), 'not-an-email')
    await user.type(screen.getByPlaceholderText('••••••••'), 'short')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Passwords are at least 8 characters.')).toBeInTheDocument()
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('posts valid credentials to the auth endpoint', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockResolvedValue({
      access_token: 'token-from-backend',
      user: { role: 'CUSTOMER' },
    })

    renderWithProviders(<LoginPage />, { route: '/login' })
    await user.type(screen.getByPlaceholderText('you@example.com'), 'Customer@valet.local')
    await user.type(screen.getByPlaceholderText('••••••••'), 'Correct-Horse-1')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1))
    expect(apiPost).toHaveBeenCalledWith('/v1/auth/login', {
      email: 'customer@valet.local',
      password: 'Correct-Horse-1',
    })
  })

  it('surfaces the backend error envelope instead of a generic message', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockRejectedValue(
      Object.assign(new Error('rejected'), {
        apiError: {
          status: 401,
          code: 'AUTHENTICATION_ERROR',
          message: 'Email or password is incorrect.',
          requestId: 'rid-99',
        },
      }),
    )

    renderWithProviders(<LoginPage />, { route: '/login' })
    await user.type(screen.getByPlaceholderText('you@example.com'), 'customer@valet.local')
    await user.type(screen.getByPlaceholderText('••••••••'), 'Wrong-Horse-2')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    expect(await screen.findByText('Email or password is incorrect.')).toBeInTheDocument()
    expect(screen.getByText('AUTHENTICATION_ERROR')).toBeInTheDocument()
    expect(screen.getByText(/rid-99/)).toBeInTheDocument()
    expect(readStoredToken()).toBeNull()
  })

  it('stores both tokens on a successful sign-in', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockResolvedValue({
      access_token: 'access-from-backend',
      refresh_token: 'refresh-from-backend',
      user: { role: 'CUSTOMER' },
    })

    renderWithProviders(<LoginPage />, { route: '/login' })
    await user.type(screen.getByPlaceholderText('you@example.com'), 'customer@valet.local')
    await user.type(screen.getByPlaceholderText('••••••••'), 'Correct-Horse-1')
    await user.click(screen.getByRole('button', { name: /^sign in$/i }))

    await waitFor(() => expect(readStoredToken()).toBe('access-from-backend'))
    expect(readStoredRefreshToken()).toBe('refresh-from-backend')
  })

  it('offers no way to pick a role at sign-in', () => {
    // Registration is a separate, customer-only flow; the sign-in form must
    // not expose a role selector or any self-service privilege escalation.
    renderWithProviders(<LoginPage />, { route: '/login' })

    expect(screen.queryByLabelText(/role/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('links to registration instead of offering demo sessions', () => {
    renderWithProviders(<LoginPage />, { route: '/login' })

    expect(screen.getByRole('link', { name: /create a customer account/i })).toHaveAttribute(
      'href',
      '/register',
    )
    expect(screen.queryByText('Demo sessions')).not.toBeInTheDocument()
  })

  it('warns when a previous session was rejected by the API', () => {
    clearSession()

    // Drive the real auth context rather than poking storage: a sibling
    // component signs out with a reason, exactly as the API client does on 401.
    function RejectThenShowLogin() {
      const { signOut, signedOutReason } = useAuth()
      useEffect(() => {
        if (!signedOutReason) signOut({ reason: 'AUTHENTICATION_ERROR' })
      }, [signOut, signedOutReason])
      return <LoginPage />
    }

    renderWithProviders(<RejectThenShowLogin />, { route: '/login' })

    expect(screen.getByText(/rejected the session token/i)).toBeInTheDocument()
    expect(screen.getByText(/AUTHENTICATION_ERROR/)).toBeInTheDocument()
  })
})
