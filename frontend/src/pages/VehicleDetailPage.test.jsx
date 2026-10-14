/**
 * The vehicle detail page.
 *
 * The behaviour worth pinning is the archive rule, because it is a safety
 * property rather than a display one:
 *
 * - **A vehicle in custody cannot be archived.** The button is disabled and an
 *   explanatory notice says why. Archiving a vehicle that is physically sitting
 *   in the yard would remove it from the active list while it is still somebody's
 *   responsibility.
 * - **An already-archived vehicle offers no archive control at all**, rather than
 *   a disabled button that invites a second click.
 * - **Archiving leaves the page.** It navigates back to the list, because the
 *   record being viewed no longer belongs in the active set.
 * - **The session history fails independently.** A vehicle can load while its
 *   history fails; the page must still render the vehicle and show a retryable
 *   error for the history alone, rather than failing the whole screen.
 * - **Editing prefills the form** from the record and routes the save through
 *   `updateVehicle`.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { VehicleDetailPage } from './VehicleDetailPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/vehicleService', async (original) => {
  const actual = await original()
  return {
    ...actual,
    fetchVehicle: vi.fn(),
    fetchVehicleSessions: vi.fn(),
    updateVehicle: vi.fn(),
    archiveVehicle: vi.fn(),
  }
})

import {
  fetchVehicle,
  fetchVehicleSessions,
  updateVehicle,
  archiveVehicle,
} from '../services/vehicleService'

const VEHICLE_ID = 'vehicle-1'

function stubVehicle(overrides = {}) {
  const vehicle = {
    id: VEHICLE_ID,
    plate_number: 'UP 78 AB 1234',
    normalized_plate: 'UP78AB1234',
    make: 'Maruti',
    model: 'Swift',
    year: 2019,
    color: 'Red',
    notes: 'Roof rack fitted.',
    status: 'REGISTERED',
    created_at: '2026-01-04T10:00:00Z',
    ...overrides,
  }
  vi.mocked(fetchVehicle).mockResolvedValue({ vehicle })
  return vehicle
}

function stubSessions(sessions) {
  vi.mocked(fetchVehicleSessions).mockResolvedValue({ sessions })
}

/** One closed session with its ticket, as the history endpoint returns them. */
function session(overrides = {}) {
  return {
    ticket: { id: 'ticket-1', ticket_code: 'TKT-0001' },
    custody_session: {
      id: 'session-1',
      status: 'CHECKED_OUT',
      dropped_off_at: '2026-09-01T09:00:00Z',
      picked_up_at: '2026-09-01T13:30:00Z',
      ...overrides,
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  stubVehicle()
  stubSessions([session()])
})

function renderPage(role = 'CUSTOMER') {
  clearSession()
  // The page reads its id from the URL, so it must be mounted on a route.
  return renderWithProviders(
    <Routes>
      <Route path="/vehicles/:id" element={<VehicleDetailPage />} />
      <Route path="/vehicles" element={<div>vehicle list route</div>} />
    </Routes>,
    { route: `/vehicles/${VEHICLE_ID}`, token: buildToken({ role }) },
  )
}

describe('VehicleDetailPage', () => {
  it('loads the vehicle named in the URL', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: 'UP 78 AB 1234' })).toBeInTheDocument()
    expect(vi.mocked(fetchVehicle).mock.calls[0][0]).toBe(VEHICLE_ID)
  })

  it('renders the record with its normalised plate alongside the display one', async () => {
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    // Both spellings are shown: the human one and the one the uniqueness index
    // is actually keyed on.
    expect(screen.getByText('UP78AB1234')).toBeInTheDocument()
    expect(screen.getByText('Roof rack fitted.')).toBeInTheDocument()
  })

  it('offers archive for a registered vehicle', async () => {
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    expect(screen.getByRole('button', { name: /archive vehicle/i })).toBeEnabled()
  })

  it('disables archive and explains why for a vehicle in custody', async () => {
    vi.clearAllMocks()
    stubVehicle({ status: 'IN_CUSTODY' })
    stubSessions([])
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    expect(screen.getByRole('button', { name: /archive vehicle/i })).toBeDisabled()
    // The reason is stated, not left to be guessed from a greyed button.
    expect(
      screen.getByText(/cannot be archived until the session is completed or cancelled/i),
    ).toBeInTheDocument()
  })

  it('offers no archive control for an already-archived vehicle', async () => {
    vi.clearAllMocks()
    stubVehicle({ status: 'ARCHIVED' })
    stubSessions([])
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    expect(screen.queryByRole('button', { name: /archive vehicle/i })).toBeNull()
  })

  it('archives and navigates back to the list', async () => {
    vi.mocked(archiveVehicle).mockResolvedValue({})
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    await user.click(screen.getByRole('button', { name: /archive vehicle/i }))

    await waitFor(() => expect(archiveVehicle).toHaveBeenCalledWith(VEHICLE_ID))
    expect(await screen.findByText(/vehicle archived/i)).toBeInTheDocument()
    // The record is gone from the active set, so the detail view is not kept.
    expect(await screen.findByText('vehicle list route')).toBeInTheDocument()
  })

  it('reports an archive refusal rather than navigating away', async () => {
    const failure = new Error('refused')
    failure.response = {
      status: 409,
      data: {
        error: {
          code: 'conflict',
          message: 'The vehicle has an open custody session.',
        },
      },
      headers: {},
    }
    vi.mocked(archiveVehicle).mockRejectedValue(failure)
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    await user.click(screen.getByRole('button', { name: /archive vehicle/i }))

    expect(await screen.findByText(/could not archive the vehicle/i)).toBeInTheDocument()
    // The message appears twice by design: once in the toast and once in the
    // inline ErrorNotice under the button, so it survives the toast expiring.
    expect(await screen.findAllByText(/open custody session/i)).toHaveLength(2)
    // Still on the detail page - the archive did not happen.
    expect(screen.queryByText('vehicle list route')).toBeNull()
  })

  it('prefills the edit form from the record', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    await user.click(screen.getByRole('button', { name: /^edit$/i }))

    expect(await screen.findByText('Edit vehicle')).toBeInTheDocument()
    expect(screen.getByLabelText(/^registration number/i)).toHaveValue('UP 78 AB 1234')
    expect(screen.getByLabelText(/^make/i)).toHaveValue('Maruti')
    // The year is stored as a number and must arrive in the input as a string.
    expect(screen.getByLabelText(/^year/i)).toHaveValue('2019')
  })

  it('saves an edit through updateVehicle and leaves edit mode', async () => {
    vi.mocked(updateVehicle).mockResolvedValue({ vehicle: stubVehicle() })
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    await user.click(screen.getByRole('button', { name: /^edit$/i }))
    await screen.findByText('Edit vehicle')

    await user.clear(screen.getByLabelText(/^colour/i))
    await user.type(screen.getByLabelText(/^colour/i), 'Silver')
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(updateVehicle).toHaveBeenCalled())
    expect(vi.mocked(updateVehicle).mock.calls[0][0]).toBe(VEHICLE_ID)
    expect(vi.mocked(updateVehicle).mock.calls[0][1]).toMatchObject({ color: 'Silver' })
    expect(await screen.findByText(/vehicle updated/i)).toBeInTheDocument()
    // Back to the read-only panel.
    expect(await screen.findByText('Details')).toBeInTheDocument()
  })

  it('leaves edit mode on cancel without saving', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'UP 78 AB 1234' })
    await user.click(screen.getByRole('button', { name: /^edit$/i }))
    await screen.findByText('Edit vehicle')
    await user.click(screen.getByRole('button', { name: /cancel/i }))

    expect(await screen.findByText('Details')).toBeInTheDocument()
    expect(updateVehicle).not.toHaveBeenCalled()
  })

  it('lists previous sessions with a link to each ticket', async () => {
    renderPage()

    const link = await screen.findByRole('link', { name: 'TKT-0001' })
    expect(link).toHaveAttribute('href', '/tickets/ticket-1')
    const row = link.closest('tr')
    expect(within(row).getByText('Checked out')).toBeInTheDocument()
  })

  it('counts the sessions in the panel subtitle', async () => {
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    expect(screen.getByText('1 session on record')).toBeInTheDocument()
  })

  it('pluralises the session count', async () => {
    vi.clearAllMocks()
    stubVehicle()
    stubSessions([
      session(),
      {
        ticket: { id: 'ticket-2', ticket_code: 'TKT-0002' },
        custody_session: { id: 'session-2', status: 'CHECKED_OUT' },
      },
    ])
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    expect(screen.getByRole('link', { name: 'TKT-0002' })).toBeInTheDocument()
    expect(screen.getByText('2 sessions on record')).toBeInTheDocument()
  })

  it('explains an empty history rather than showing a bare panel', async () => {
    vi.clearAllMocks()
    stubVehicle()
    stubSessions([])
    renderPage()

    expect(
      await screen.findByText(/no valet sessions yet/i),
    ).toBeInTheDocument()
  })

  it('still renders the vehicle when only the history fails', async () => {
    vi.clearAllMocks()
    stubVehicle()
    const failure = new Error('history down')
    failure.response = {
      status: 503,
      data: { error: { code: 'service_unavailable', message: 'History unavailable' } },
      headers: {},
    }
    vi.mocked(fetchVehicleSessions).mockRejectedValue(failure)
    renderPage()

    // The vehicle panel is the point of the page; one failed sub-query must not
    // take it down.
    expect(await screen.findByRole('heading', { name: 'UP 78 AB 1234' })).toBeInTheDocument()
    expect(await screen.findByText('Details')).toBeInTheDocument()
  })

  it('shows a retryable error when the vehicle itself fails to load', async () => {
    vi.clearAllMocks()
    const failure = new Error('network down')
    failure.response = {
      status: 404,
      data: { error: { code: 'not_found', message: 'No such vehicle' } },
      headers: {},
    }
    vi.mocked(fetchVehicle).mockRejectedValue(failure)
    vi.mocked(fetchVehicleSessions).mockResolvedValue({ sessions: [] })
    renderPage()

    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})
