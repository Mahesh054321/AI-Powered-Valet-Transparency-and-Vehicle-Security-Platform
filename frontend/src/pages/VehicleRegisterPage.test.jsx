/**
 * Vehicle registration tests.
 *
 * The form's client-side validation is explicitly a courtesy — the backend is
 * authoritative on plate format and uniqueness. So these tests assert both
 * halves: that obvious mistakes are caught before a request is sent, and that a
 * backend rejection lands next to the field it belongs to rather than as a
 * generic banner the user has to interpret.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VehicleRegisterPage } from './VehicleRegisterPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/vehicleService', () => ({
  createVehicle: vi.fn(),
}))
vi.mock('react-router-dom', async (original) => {
  const actual = await original()
  return { ...actual, useNavigate: () => navigate }
})

import { createVehicle } from '../services/vehicleService'

const navigate = vi.fn()

function renderPage(role = 'CUSTOMER') {
  clearSession()
  return renderWithProviders(<VehicleRegisterPage />, {
    route: '/vehicles/new',
    token: buildToken({ role, email: `${role.toLowerCase()}@valet.local` }),
  })
}

async function fillForm(user, overrides = {}) {
  const values = {
    'Registration number': 'UP85CA1234',
    Make: 'Tata',
    Model: 'Nexon',
    Year: '2022',
    Colour: 'Silver',
    ...overrides,
  }
  for (const [label, value] of Object.entries(values)) {
    // The hint text sits inside the same <label>, so match on the prefix.
    const field = await screen.findByLabelText(new RegExp(`^${label}`, 'i'))
    await user.clear(field)
    if (value) await user.type(field, value)
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('VehicleRegisterPage', () => {
  it('renders the form with the documented fields', () => {
    renderPage()

    expect(
      screen.getByRole('heading', { name: /register a vehicle/i }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/^registration number/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^make/i)).toBeInTheDocument()
    // The uniqueness rule is stated on the page, not left to a 409.
    expect(screen.getByText(/must be unique across active vehicles/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /register vehicle/i })).toBeInTheDocument()
  })

  it('submits the entered values and navigates to the new vehicle', async () => {
    vi.mocked(createVehicle).mockResolvedValue({ vehicle: { id: 'v-99' } })
    const user = userEvent.setup()
    renderPage()

    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    await waitFor(() => expect(createVehicle).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(createVehicle).mock.calls[0][0]
    expect(payload).toMatchObject({
      plateNumber: 'UP85CA1234',
      make: 'Tata',
      model: 'Nexon',
      year: '2022',
      color: 'Silver',
    })

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/vehicles/v-99', { replace: true }))
  })

  it('does not call the API when the plate is missing', async () => {
    const user = userEvent.setup()
    renderPage()

    await fillForm(user, { 'Registration number': '' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(
      await screen.findByText(/enter the registration number/i),
    ).toBeInTheDocument()
    expect(createVehicle).not.toHaveBeenCalled()
  })

  it('does not call the API when the make is missing', async () => {
    const user = userEvent.setup()
    renderPage()

    await fillForm(user, { Make: '' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/enter the make/i)).toBeInTheDocument()
    expect(createVehicle).not.toHaveBeenCalled()
  })

  it('rejects an implausible year', async () => {
    const user = userEvent.setup()
    renderPage()

    await fillForm(user, { Year: '1899' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(
      await screen.findByText(/enter a year between 1950 and 2100/i),
    ).toBeInTheDocument()
    expect(createVehicle).not.toHaveBeenCalled()
  })

  it('accepts the Bharat series format', async () => {
    vi.mocked(createVehicle).mockResolvedValue({ vehicle: { id: 'v-2' } })
    const user = userEvent.setup()
    renderPage()

    // 25BH0987AB is 10 characters and valid; the loose check must not reject it.
    await fillForm(user, { 'Registration number': '25BH0987AB' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    await waitFor(() => expect(createVehicle).toHaveBeenCalledTimes(1))
  })

  it('surfaces a backend uniqueness rejection against the plate field', async () => {
    vi.mocked(createVehicle).mockRejectedValue({
      apiError: {
        status: 409,
        code: 'CONFLICT',
        message: 'A vehicle with this registration is already active.',
        details: { field: 'plate_number' },
      },
    })
    const user = userEvent.setup()
    renderPage()

    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    // The backend names `plate_number`; the form maps it onto `plateNumber`.
    // The message deliberately appears twice: once beside the field it belongs
    // to, once in the summary notice. Asserting the count pins that, so a change
    // that drops the field-level copy is caught rather than silently passing.
    const matches = await screen.findAllByText(/already active/i)
    expect(matches).toHaveLength(2)
    expect(matches[0]).toHaveClass('field__error')
    await waitFor(() => expect(navigate).not.toHaveBeenCalled())
  })

  it('shows a generic notice when the failure names no field', async () => {
    vi.mocked(createVehicle).mockRejectedValue({
      apiError: { status: 503, code: 'SERVICE_UNAVAILABLE', message: 'Try again shortly.' },
    })
    const user = userEvent.setup()
    renderPage()

    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/try again shortly/i)).toBeInTheDocument()
  })

  it('cancelling returns to the vehicle list without saving', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /cancel/i }))

    expect(navigate).toHaveBeenCalledWith('/vehicles')
    expect(createVehicle).not.toHaveBeenCalled()
  })

  it('disables the submit button while saving', async () => {
    let release
    vi.mocked(createVehicle).mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    const user = userEvent.setup()
    renderPage()

    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    const button = await screen.findByRole('button', { name: /saving/i })
    expect(button).toBeDisabled()

    release({ vehicle: { id: 'v-3' } })
    await waitFor(() => expect(navigate).toHaveBeenCalled())
  })

  it('is reachable by staff registering on a customer’s behalf', () => {
    // Same form, different entry point; the page must not assume a customer.
    renderPage('VALET_OPERATOR')

    expect(
      screen.getByRole('heading', { name: /register a vehicle/i }),
    ).toBeInTheDocument()
  })
})
