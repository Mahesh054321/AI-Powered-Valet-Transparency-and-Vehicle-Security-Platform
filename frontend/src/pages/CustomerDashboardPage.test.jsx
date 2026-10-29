/**
 * Tests for the customer dashboard page.
 *
 * The service is mocked — a page test that let the query fire would make real
 * network calls against nothing and fail for reasons that have nothing to do
 * with what is being checked.
 *
 * What these tests hold the page to:
 *
 * - **an empty dashboard explains itself** rather than rendering blank cards;
 * - **the Request Vehicle button is disabled for the same reasons the backend
 *   would refuse**, so the customer is never shown a button that 409s;
 * - **the confirmation after a request shows the estimate**, which is the whole
 *   point of the interaction.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { CustomerDashboardPage } from './CustomerDashboardPage'
import { fetchDashboard, requestVehicle } from '../services/dashboardService'
import { buildToken, renderWithProviders } from '../test/utils'

vi.mock('../services/dashboardService', async () => {
  const actual = await vi.importActual('../services/dashboardService')
  return {
    ...actual,
    fetchDashboard: vi.fn(),
    requestVehicle: vi.fn(),
  }
})

function dashboard(overrides = {}) {
  return {
    has_active_custody: true,
    custody_session_id: 'custody-1',
    custody: {
      state: 'ok',
      status: 'PARKED',
      checked_in_at: new Date(Date.now() - 15 * 60000).toISOString(),
      ticket_code: 'VT-202609-K7M3QD',
    },
    vehicle: {
      state: 'ok',
      plate_number: 'UP85CA1234',
      make: 'Maruti',
      model: 'Swift',
      status: 'IN_CUSTODY',
    },
    zone: { state: 'ok', code: 'ZONE-A', name: 'Main yard', capacity: 20, current_occupancy: 5 },
    location: {
      state: 'ok',
      latitude: 27.4935,
      longitude: 77.6745,
      movement_status: 'NORMAL',
      recorded_at: new Date().toISOString(),
    },
    security: {
      state: 'ok',
      status: 'SECURE',
      summary: 'Vehicle inside its zone; latest movement verdict NORMAL.',
      movement_status: 'NORMAL',
      open_alert_count: 0,
      alerts: [],
    },
    condition: {
      state: 'ok',
      status: 'CLEAN',
      summary: 'No damage detected.',
      total_detections: 0,
      unverified_count: 0,
      confirmed_count: 0,
      pickup_completed: false,
      detector_disclaimer: 'Classical OpenCV differencing, not a trained classifier.',
    },
    retrieval: { state: 'empty', reason: 'No retrieval estimate yet.' },
    actions: { state: 'ok', can_request: true, custody_status: 'PARKED' },
    ...overrides,
  }
}

describe('CustomerDashboardPage', () => {
  beforeEach(() => {
    vi.mocked(fetchDashboard).mockReset()
    vi.mocked(requestVehicle).mockReset()
  })

  it('renders all five cards from live data', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() => expect(screen.getByText('Vehicle Status')).toBeInTheDocument())

    expect(screen.getByText('Security Status')).toBeInTheDocument()
    expect(screen.getByText('Current Location')).toBeInTheDocument()
    expect(screen.getByText('Estimated Retrieval')).toBeInTheDocument()
    expect(screen.getByText('Condition Status')).toBeInTheDocument()
    expect(screen.getByText(/UP85CA1234/)).toBeInTheDocument()
  })

  it('reads the dashboard from the API rather than hardcoding it', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() => expect(fetchDashboard).toHaveBeenCalled())
  })

  it('explains an empty dashboard instead of showing blank cards', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue({
      has_active_custody: false,
      custody: { state: 'empty', reason: 'No vehicle is currently in valet custody.' },
      actions: { state: 'empty', can_request: false },
    })
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() =>
      expect(screen.getByText('No vehicle in custody')).toBeInTheDocument()
    )
    expect(
      screen.getByText('No vehicle is currently in valet custody.')
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /View my vehicles/i })).toBeInTheDocument()
    // No cards at all, rather than five empty ones that read as "all clear".
    expect(screen.queryByText('Vehicle Status')).not.toBeInTheDocument()
  })

  it('enables Request Vehicle when the backend says it may be used', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Request Vehicle/i })).toBeEnabled()
    )
  })

  it('disables Request Vehicle when a retrieval is already running', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(
      dashboard({
        custody: { state: 'ok', status: 'RETRIEVAL_REQUESTED' },
        actions: {
          state: 'empty',
          can_request: false,
          reason: 'A retrieval is already in progress for this vehicle.',
        },
      })
    )
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Retrieval in progress/i })
      ).toBeDisabled()
    )
    expect(
      screen.getByText('A retrieval is already in progress for this vehicle.')
    ).toBeInTheDocument()
  })

  it('requests the vehicle and shows the estimate it was given', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(dashboard())
    vi.mocked(requestVehicle).mockResolvedValue({
      message: 'Vehicle requested. A valet has been notified.',
      estimate_available: true,
      estimated_minutes: 8,
      queue_position: 2,
      recommended_dispatch: 'Now',
    })
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Request Vehicle/i })).toBeEnabled()
    )
    fireEvent.click(screen.getByRole('button', { name: /Request Vehicle/i }))

    await waitFor(() => expect(screen.getByText('Vehicle requested')).toBeInTheDocument())
    expect(requestVehicle).toHaveBeenCalledWith('custody-1')
    // The three figures the brief names, straight from the response.
    expect(screen.getByText(/Estimated Retrieval: 8 minutes/)).toBeInTheDocument()
    expect(screen.getByText(/Queue Position: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Recommended Dispatch: Now/)).toBeInTheDocument()
  })

  it('still confirms the request when no estimate could be produced', async () => {
    // An untrained predictor must not look like a failed request: the customer
    // did ask, and a valet was told.
    vi.mocked(fetchDashboard).mockResolvedValue(dashboard())
    vi.mocked(requestVehicle).mockResolvedValue({
      message: 'Vehicle requested and a valet has been notified.',
      estimate_available: false,
      estimate_error: 'No retrieval model is loaded.',
    })
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Request Vehicle/i })).toBeEnabled()
    )
    fireEvent.click(screen.getByRole('button', { name: /Request Vehicle/i }))

    await waitFor(() => expect(screen.getByText('Vehicle requested')).toBeInTheDocument())
    expect(screen.getByText(/No estimate could be produced/)).toBeInTheDocument()
  })

  it('shows an API failure with a way to retry', async () => {
    vi.mocked(fetchDashboard).mockRejectedValue({
      code: 'NETWORK_ERROR',
      message: 'Could not reach the API.',
    })
    renderWithProviders(<CustomerDashboardPage />, { token: buildToken() })

    // React Query retries before surfacing a failure, which takes longer than
    // waitFor's one-second default.
    await waitFor(
      () => expect(screen.getByText('Could not reach the API.')).toBeInTheDocument(),
      { timeout: 5000 }
    )
  })
})
