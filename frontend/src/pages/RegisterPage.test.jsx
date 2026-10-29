import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RegisterPage } from './RegisterPage'
import { clearSession, renderWithProviders } from '../test/utils.jsx'
import { apiPost } from '../services/apiClient'

// Mock the module, not the namespace: RegisterPage holds a direct binding.
vi.mock('../services/apiClient', async (importActual) => {
  const actual = await importActual()
  return { ...actual, apiPost: vi.fn() }
})

/**
 * Fill the form one field at a time.
 *
 * Sequential `await` rather than `Promise.all`: user-event serialises
 * interactions through a pointer lock, so typing into several fields
 * concurrently races and the values land in the wrong inputs.
 */
async function fill(user, overrides = {}) {
  const values = {
    fullName: 'Aisha Verma',
    email: 'aisha@example.com',
    phone: '',
    password: 'Str0ng#Pass',
    confirm: 'Str0ng#Pass',
    ...overrides,
  }

  await user.type(screen.getByPlaceholderText('Aisha Verma'), values.fullName)
  await user.type(screen.getByPlaceholderText('you@example.com'), values.email)
  if (values.phone) {
    await user.type(screen.getByPlaceholderText('+91 98765 43210'), values.phone)
  }
  // The password and confirm inputs share a placeholder; fill them in order.
  const [passwordInput, confirmInput] = screen.getAllByPlaceholderText('••••••••')
  await user.type(passwordInput, values.password)
  await user.type(confirmInput, values.confirm)
}

const submit = (user) =>
  user.click(screen.getByRole('button', { name: /create account/i }))

describe('RegisterPage', () => {
  beforeEach(() => {
    clearSession()
    vi.mocked(apiPost).mockReset()
  })

  it('blocks submission and explains each invalid field', async () => {
    const user = userEvent.setup()
    renderWithProviders(<RegisterPage />, { route: '/register' })

    await fill(user, {
      fullName: 'A',
      email: 'not-an-email',
      password: 'short',
      confirm: 'different',
    })
    await submit(user)

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your full name.')).toBeInTheDocument()
    expect(screen.getByText(/Password needs/)).toBeInTheDocument()
    expect(screen.getByText('The two passwords do not match.')).toBeInTheDocument()
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('requires a letter and a digit in the password', async () => {
    const user = userEvent.setup()
    renderWithProviders(<RegisterPage />, { route: '/register' })

    await fill(user, { password: 'allletters', confirm: 'allletters' })
    await submit(user)

    expect(await screen.findByText(/a digit/)).toBeInTheDocument()
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('posts the payload the backend expects, without a role', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockResolvedValue({ user: { role: 'CUSTOMER' } })

    renderWithProviders(<RegisterPage />, { route: '/register' })
    await fill(user)
    await submit(user)

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1))
    const [path, payload] = vi.mocked(apiPost).mock.calls[0]

    expect(path).toBe('/v1/auth/register')
    expect(payload).toEqual({
      email: 'aisha@example.com',
      password: 'Str0ng#Pass',
      full_name: 'Aisha Verma',
    })
    // The whole point: no role is ever sent, so none can be escalated.
    expect(payload).not.toHaveProperty('role')
  })

  it('sends the phone number only when one was given', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockResolvedValue({ user: {} })

    renderWithProviders(<RegisterPage />, { route: '/register' })
    await fill(user, { phone: '+91 98765 43210' })
    await submit(user)

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1))
    expect(vi.mocked(apiPost).mock.calls[0][1].phone).toBe('+91 98765 43210')
  })

  it('rejects a malformed phone number before submitting', async () => {
    const user = userEvent.setup()
    renderWithProviders(<RegisterPage />, { route: '/register' })

    await fill(user, { phone: 'abc' })
    await submit(user)

    expect(await screen.findByText(/valid phone number/i)).toBeInTheDocument()
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('shows a loading state while the request is in flight', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockReturnValue(new Promise(() => {})) // never settles

    renderWithProviders(<RegisterPage />, { route: '/register' })
    await fill(user)
    await submit(user)

    expect(await screen.findByRole('button', { name: /creating account…/i })).toBeDisabled()
  })

  it('surfaces a duplicate-email conflict on the email field', async () => {
    const user = userEvent.setup()
    vi.mocked(apiPost).mockRejectedValue(
      Object.assign(new Error('rejected'), {
        apiError: {
          status: 409,
          code: 'CONFLICT',
          message: 'An account with that email address already exists.',
          details: { field: 'email' },
          requestId: 'rid-1',
        },
      }),
    )

    renderWithProviders(<RegisterPage />, { route: '/register' })
    await fill(user)
    await submit(user)

    const message = 'An account with that email address already exists.'

    // It legitimately appears twice: inline under the email field (scoped by
    // `details.field`) and again in the error banner. Assert both rather than
    // letting the ambiguity fail the query.
    const emailField = screen.getByPlaceholderText('you@example.com').closest('label')
    expect(await within(emailField).findByText(message)).toBeInTheDocument()
    expect(screen.getAllByText(message)).toHaveLength(2)
    expect(screen.getByText('CONFLICT')).toBeInTheDocument()
  })

  it('links back to sign-in', () => {
    renderWithProviders(<RegisterPage />, { route: '/register' })

    expect(screen.getByRole('link', { name: /sign in instead/i })).toHaveAttribute(
      'href',
      '/login',
    )
  })

  it('states plainly that registration creates a customer account', () => {
    // An academic reviewer should be able to see the privilege rule in the UI.
    renderWithProviders(<RegisterPage />, { route: '/register' })

    // "customer" is wrapped in a <strong>, so match the text node that
    // precedes it rather than the whole sentence.
    expect(screen.getByText(/Public registration creates a/i)).toBeInTheDocument()
    expect(screen.getByText(/created by an administrator/i)).toBeInTheDocument()
  })
})
