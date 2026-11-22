/**
 * Check-in wizard tests.
 *
 * The point of these tests is that the workflow is driven by real API calls in
 * the right order, not by mock buttons that only advance local state. Each
 * service is mocked at the module boundary so the assertions are about which
 * endpoint the wizard reached and with what payload.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CheckInPage } from './CheckInPage'
import { clearSession, buildToken, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/directoryService', () => ({
  fetchCustomers: vi.fn(),
  fetchParkingZones: vi.fn(),
}))
vi.mock('../services/vehicleService', () => ({
  fetchVehicles: vi.fn(),
  createVehicle: vi.fn(),
}))
vi.mock('../services/ticketService', () => ({
  issueTicket: vi.fn(),
  checkInTicket: vi.fn(),
}))
vi.mock('../services/uploadService', () => ({
  uploadDropOffPhoto: vi.fn(),
}))

import { fetchCustomers, fetchParkingZones } from '../services/directoryService'
import { createVehicle, fetchVehicles } from '../services/vehicleService'
import { checkInTicket, issueTicket } from '../services/ticketService'

const CUSTOMER = { id: 'c-1', full_name: 'Aisha Verma', email: 'aisha@example.com', status: 'ACTIVE' }
const VEHICLE = { id: 'v-1', plate_number: 'UP85CA1234', make: 'Tata', model: 'Nexon', status: 'REGISTERED' }
const ZONE = { id: 'z-1', code: 'ZONE-A', name: 'North Court', available_slots: 4, capacity: 5 }

function stubLookups() {
  vi.mocked(fetchCustomers).mockResolvedValue({ items: [CUSTOMER], total: 1 })
  vi.mocked(fetchParkingZones).mockResolvedValue({ items: [ZONE], total: 1 })
  vi.mocked(fetchVehicles).mockResolvedValue({ items: [VEHICLE], pagination: {} })
}

function renderWizard() {
  clearSession()
  return renderWithProviders(<CheckInPage />, {
    route: '/checkin',
    token: buildToken({ role: 'VALET_OPERATOR', email: 'operator@valet.local' }),
  })
}

/** Walk the wizard as far as the handover button. */
async function reachHandover(user) {
  await user.click(await screen.findByRole('button', { name: /aisha verma/i }))
  await user.click(await screen.findByRole('button', { name: /UP85CA1234/i }))
  await user.click(await screen.findByRole('button', { name: /ZONE-A/i }))
  return screen.findByRole('button', { name: /create ticket and start custody/i })
}

describe('CheckInPage', () => {
  beforeEach(() => {
    vi.mocked(fetchCustomers).mockReset()
    vi.mocked(fetchParkingZones).mockReset()
    vi.mocked(fetchVehicles).mockReset()
    vi.mocked(createVehicle).mockReset()
    vi.mocked(issueTicket).mockReset()
    vi.mocked(checkInTicket).mockReset()
  })

  it('loads the customer directory from the API on mount', async () => {
    stubLookups()
    renderWizard()

    expect(await screen.findByRole('button', { name: /aisha verma/i })).toBeInTheDocument()
    expect(fetchCustomers).toHaveBeenCalled()
  })

  it('lists all ten workflow steps', () => {
    stubLookups()
    renderWizard()

    for (const step of [
      'Select customer',
      'Select or register vehicle',
      'Capture vehicle details',
      'Capture drop-off images',
      'Create valet ticket',
      'Assign parking zone',
      'Start custody session',
      'Generate ticket number',
      'Generate QR code',
      'Show digital ticket',
    ]) {
      expect(screen.getByText(step)).toBeInTheDocument()
    }
  })

  it('does not offer a vehicle until a customer is chosen', async () => {
    stubLookups()
    renderWizard()

    await screen.findByRole('button', { name: /aisha verma/i })
    expect(screen.queryByRole('button', { name: /UP85CA1234/i })).not.toBeInTheDocument()
    // Vehicles are fetched per customer, so the call has not happened yet.
    expect(fetchVehicles).not.toHaveBeenCalled()
  })

  it('fetches that customer\'s vehicles once one is selected', async () => {
    const user = userEvent.setup()
    stubLookups()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /aisha verma/i }))

    await waitFor(() => expect(fetchVehicles).toHaveBeenCalledWith({ owner_id: 'c-1', page_size: 50 }))
  })

  it('shows a newly registered vehicle in the selected customer list', async () => {
    const user = userEvent.setup()
    stubLookups()
    const registered = {
      id: 'v-2',
      plate_number: 'UP85CA9999',
      make: 'Hyundai',
      model: 'Creta',
      status: 'REGISTERED',
    }
    vi.mocked(fetchVehicles)
      .mockResolvedValueOnce({ items: [VEHICLE], pagination: {} })
      .mockResolvedValue({ items: [VEHICLE, registered], pagination: {} })
    vi.mocked(createVehicle).mockResolvedValue({ vehicle: registered })
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /aisha verma/i }))
    await user.click(await screen.findByRole('button', { name: /register new/i }))
    await user.type(screen.getByLabelText(/^registration number/i), registered.plate_number)
    await user.type(screen.getByLabelText(/^make/i), registered.make)
    await user.type(screen.getByLabelText(/^model/i), registered.model)
    await user.click(screen.getByRole('button', { name: /register and select/i }))

    const vehicleButton = await screen.findByRole('button', { name: /UP85CA9999/i })
    expect(vehicleButton).toHaveAttribute('aria-pressed', 'true')
    expect(createVehicle).toHaveBeenCalledWith(
      expect.objectContaining({ ownerId: CUSTOMER.id }),
    )
  })

  it('offers no handover until a vehicle and a zone are chosen', async () => {
    const user = userEvent.setup()
    stubLookups()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /aisha verma/i }))
    await user.click(await screen.findByRole('button', { name: /UP85CA1234/i }))

    expect(
      screen.queryByRole('button', { name: /create ticket and start custody/i }),
    ).not.toBeInTheDocument()
  })

  it('issues the ticket and starts custody in that order on handover', async () => {
    const user = userEvent.setup()
    stubLookups()
    const calls = []
    vi.mocked(issueTicket).mockImplementation(async () => {
      calls.push('issue')
      return { ticket: { id: 't-1', ticket_code: 'VT-202609-ABC123', qr_url: '/media/qr/x.png' } }
    })
    vi.mocked(checkInTicket).mockImplementation(async () => {
      calls.push('checkIn')
      return { ticket: { id: 't-1', ticket_code: 'VT-202609-ABC123', status: 'IN_CUSTODY' } }
    })

    renderWizard()
    const handover = await reachHandover(user)
    await user.click(handover)

    await waitFor(() => expect(calls).toEqual(['issue', 'checkIn']))
    expect(issueTicket).toHaveBeenCalledWith({ vehicleId: 'v-1' })
    expect(checkInTicket).toHaveBeenCalledWith('t-1', expect.objectContaining({ zoneId: 'z-1' }))
  })

  it('sends the drop-off condition with the handover', async () => {
    const user = userEvent.setup()
    stubLookups()
    vi.mocked(issueTicket).mockResolvedValue({ ticket: { id: 't-1', ticket_code: 'VT-1' } })
    vi.mocked(checkInTicket).mockResolvedValue({ ticket: { id: 't-1', status: 'IN_CUSTODY' } })

    renderWizard()
    const handover = await reachHandover(user)

    const panel = screen.getByText('3 · Capture vehicle details').closest('.panel')
    const [odometer, fuel] = within(panel).getAllByRole('textbox')
    await user.type(odometer, '45210')
    await user.type(fuel, '60')

    await user.click(handover)

    await waitFor(() =>
      expect(checkInTicket).toHaveBeenCalledWith(
        't-1',
        expect.objectContaining({ odometer: '45210', fuelLevel: '60', zoneId: 'z-1' }),
      ),
    )
  })

  it('disables a zone that is already full', async () => {
    const user = userEvent.setup()
    stubLookups()
    vi.mocked(fetchParkingZones).mockResolvedValue({
      items: [{ ...ZONE, available_slots: 0 }],
      total: 1,
    })
    renderWizard()

    // The zone panel only appears once a vehicle is chosen.
    await user.click(await screen.findByRole('button', { name: /aisha verma/i }))
    await user.click(await screen.findByRole('button', { name: /UP85CA1234/i }))

    const zoneButton = await screen.findByRole('button', { name: /ZONE-A/i })
    expect(zoneButton).toBeDisabled()
    expect(zoneButton).toHaveTextContent(/full/)

    // With no zone selectable there is nothing to hand over.
    expect(
      screen.queryByRole('button', { name: /create ticket and start custody/i }),
    ).not.toBeInTheDocument()
  })

  it('shows a backend error rather than pretending the handover worked', async () => {
    const user = userEvent.setup()
    stubLookups()
    vi.mocked(issueTicket).mockRejectedValue(
      Object.assign(new Error('rejected'), {
        apiError: {
          status: 409,
          code: 'CONFLICT',
          message: 'UP85CA1234 is already in custody.',
        },
      }),
    )

    renderWizard()
    const handover = await reachHandover(user)
    await user.click(handover)

    expect(await screen.findByText('UP85CA1234 is already in custody.')).toBeInTheDocument()
    expect(screen.getByText('CONFLICT')).toBeInTheDocument()
    expect(checkInTicket).not.toHaveBeenCalled()
  })
})
