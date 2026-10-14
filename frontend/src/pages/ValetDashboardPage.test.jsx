/**
 * Tests for the valet operator dashboard.
 *
 * The services are mocked: a page test that let the queries fire would make
 * real network calls against nothing and fail for reasons unrelated to what is
 * being checked.
 *
 * What these tests hold the page to:
 *
 * - **all five sections render**, each with its own empty state;
 * - **the badge vocabulary comes from the payload**, so the UI cannot drift
 *   from the backend's meaning;
 * - **buttons follow `capabilities`** — an operator must never be shown an
 *   action the backend will refuse, because a button that 403s tells them the
 *   action is available and then refuses them in front of a customer;
 * - **important actions go through a confirmation dialog** that names the
 *   vehicle and customer, rather than a bare "are you sure?";
 * - **the page says it polls**, because there is no push channel here.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { ValetDashboardPage } from './ValetDashboardPage'
import {
  acknowledgeAlert,
  completePickup,
  fetchStaffDashboard,
  moveVehicle,
  resolveAlert,
} from '../services/staffDashboardService'
import { buildToken, renderWithProviders } from '../test/utils'

vi.mock('../services/staffDashboardService', async () => {
  const actual = await vi.importActual('../services/staffDashboardService')
  return {
    ...actual,
    fetchStaffDashboard: vi.fn(),
    moveVehicle: vi.fn(),
    completePickup: vi.fn(),
    acknowledgeAlert: vi.fn(),
    resolveAlert: vi.fn(),
  }
})

vi.mock('../services/inspectionService', () => ({
  capturePickup: vi.fn(),
}))

const LABELS = {
  IN_CUSTODY: 'In custody',
  MOVING: 'Moving',
  READY: 'Ready',
  ALERT: 'Alert',
  COMPLETED: 'Completed',
}

function row(overrides = {}) {
  return {
    custody_session_id: 'custody-1',
    ticket_id: 'ticket-1',
    ticket_code: 'VT-202609-K7M3QD',
    custody_status: 'PARKED',
    display_status: 'IN_CUSTODY',
    display_label: 'In custody',
    vehicle: { plate_number: 'UP85CA1234', make: 'Maruti', model: 'Swift' },
    customer: { name: 'Aisha Verma', email: 'aisha@example.com', phone: '+91 90000 00001' },
    zone: { code: 'ZONE-A', name: 'Main yard' },
    gps: null,
    gps_has_data: false,
    open_alert_count: 0,
    alarming: false,
    ...overrides,
  }
}

function dashboard(overrides = {}) {
  return {
    delivery: 'polled',
    display_status_labels: LABELS,
    capabilities: {
      can_move_vehicle: true,
      can_check_in: true,
      can_complete_pickup: true,
      can_capture_inspection: true,
      can_verify_damage: false,
      can_triage_alerts: false,
    },
    incoming: {
      state: 'ok',
      count: 1,
      items: [
        {
          ticket_id: 'ticket-9',
          ticket_code: 'VT-202609-ARRIVING',
          issued_at: new Date(Date.now() - 20 * 60000).toISOString(),
          vehicle: { plate_number: 'UP85XY9876', make: 'Hyundai', model: 'i20' },
          customer: { name: 'Rohit Shah' },
        },
      ],
    },
    active_vehicles: { state: 'ok', count: 1, moving: 0, alarming: 0, items: [row()] },
    pending_pickups: { state: 'empty', reason: 'No vehicles are staged for handover.', count: 0, items: [] },
    retrieval_queue: {
      state: 'ok',
      count: 1,
      waiting: 1,
      delivery: 'polled',
      delivery_note: 'This queue is read on a short poll rather than pushed.',
      items: [
        {
          queue_position: 1,
          custody_session_id: 'custody-2',
          custody_status: 'RETRIEVAL_REQUESTED',
          requested_at: new Date(Date.now() - 4 * 60000).toISOString(),
          vehicle: { plate_number: 'UP85QR5555', make: 'Tata', model: 'Nexon' },
          zone: { code: 'ZONE-B' },
          estimated_minutes: 9,
          in_progress: false,
        },
      ],
    },
    security_alerts: {
      state: 'ok',
      count: 1,
      highest_severity: 'HIGH',
      items: [
        {
          id: 'alert-1',
          alert_type: 'GEOFENCE_BREACH',
          severity: 'HIGH',
          status: 'OPEN',
          title: 'Vehicle left its zone',
          reason: 'Last fix was outside the configured boundary.',
          occurred_at: new Date(Date.now() - 6 * 60000).toISOString(),
        },
      ],
    },
    inspections: {
      state: 'ok',
      count: 1,
      awaiting_pickup_photos: 1,
      awaiting_review: 0,
      items: [
        {
          custody_session_id: 'custody-1',
          vehicle: { plate_number: 'UP85CA1234' },
          drop_off_captured: true,
          drop_off_photo_count: 4,
          pickup_captured: false,
          pickup_photo_count: 0,
          comparison_ready: false,
          unverified_detections: 0,
          needs_pickup_photos: true,
          needs_review: false,
        },
      ],
    },
    zones: {
      state: 'ok',
      count: 2,
      items: [
        { id: 'z1', code: 'ZONE-A', name: 'Main yard', capacity: 20, current_occupancy: 7, available_slots: 13, occupancy_percent: 35, parkable: true },
        { id: 'z2', code: 'ZONE-B', name: 'Overflow', capacity: 10, current_occupancy: 10, available_slots: 0, occupancy_percent: 100, parkable: false },
      ],
    },
    ...overrides,
  }
}

describe('ValetDashboardPage sections', () => {
  beforeEach(() => {
    vi.mocked(fetchStaffDashboard).mockReset()
    vi.mocked(moveVehicle).mockReset()
    vi.mocked(completePickup).mockReset()
    vi.mocked(acknowledgeAlert).mockReset()
    vi.mocked(resolveAlert).mockReset()
  })

  it('renders all five required sections', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() => expect(screen.getByText('Active vehicles')).toBeInTheDocument())

    expect(screen.getByText('Pending pickups')).toBeInTheDocument()
    expect(screen.getByText('Security alerts')).toBeInTheDocument()
    expect(screen.getByText('Vehicle inspections')).toBeInTheDocument()
    expect(screen.getByText('Retrieval queue')).toBeInTheDocument()
  })

  it('reads the screen from the composed endpoint', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() => expect(fetchStaffDashboard).toHaveBeenCalled())
  })

  it('shows the vehicle, customer and zone on an active row', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    // The plate appears twice by design: once in the active table and once in
    // the inspections table, which is the same car in two different lists.
    await waitFor(() => expect(screen.getAllByText('UP85CA1234').length).toBeGreaterThan(0))
    expect(screen.getByText('Aisha Verma')).toBeInTheDocument()
    expect(screen.getAllByText('ZONE-A').length).toBeGreaterThan(0)
  })

  it('uses the labels the backend sent rather than its own vocabulary', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    // "In custody" is the backend's label for IN_CUSTODY. If the page had its
    // own map, this is where the two would start to disagree.
    await waitFor(() => expect(screen.getByText('In custody')).toBeInTheDocument())
    // The underlying custody state stays visible beside the summary badge.
    expect(screen.getByText('PARKED')).toBeInTheDocument()
  })

  it('shows arrivals separately from cars already in custody', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() => expect(screen.getByText('Incoming vehicles')).toBeInTheDocument())
    expect(screen.getByText('VT-202609-ARRIVING')).toBeInTheDocument()
  })

  it('says a car with no GPS fix has none, rather than implying a clean position', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() => expect(screen.getByText('No fix recorded')).toBeInTheDocument())
  })

  it('says plainly that it polls rather than claiming to be live', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByText(/It is polled, not pushed/)).toBeInTheDocument()
    )
  })

  it('renders an empty section as an answer, not as a fault', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(
        screen.getByText('No vehicles are staged for handover.')
      ).toBeInTheDocument()
    )
  })

  it('flags a section whose sub-query failed', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(
      dashboard({
        zones: { state: 'unavailable', reason: 'Zone occupancy could not be read.' },
      })
    )
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByText('Zone occupancy could not be read.')).toBeInTheDocument()
    )
  })

  it('reports an unreachable backend', async () => {
    vi.mocked(fetchStaffDashboard).mockRejectedValue({
      code: 'NETWORK_ERROR',
      message: 'Could not reach the API.',
    })
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByText('Could not reach the API.')).toBeInTheDocument(),
      { timeout: 5000 }
    )
  })
})

describe('ValetDashboardPage capabilities', () => {
  beforeEach(() => {
    vi.mocked(fetchStaffDashboard).mockReset()
    vi.mocked(acknowledgeAlert).mockReset()
  })

  it('hides alert triage from a role that cannot perform it', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByText('Vehicle left its zone')).toBeInTheDocument()
    )
    // A button here would 403. Telling an operator an action is available and
    // then refusing them in front of a customer is worse than not offering it.
    expect(screen.queryByRole('button', { name: /Acknowledge/i })).not.toBeInTheDocument()
    expect(screen.getByText('read-only for your role')).toBeInTheDocument()
  })

  it('offers alert triage to a role that can perform it', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(
      dashboard({
        capabilities: {
          can_move_vehicle: true,
          can_check_in: true,
          can_complete_pickup: true,
          can_capture_inspection: true,
          can_verify_damage: true,
          can_triage_alerts: true,
        },
      })
    )
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_MANAGER' }),
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Acknowledge/i })).toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: /Resolve/i })).toBeInTheDocument()
  })
})

describe('ValetDashboardPage confirmations', () => {
  beforeEach(() => {
    vi.mocked(fetchStaffDashboard).mockReset()
    vi.mocked(moveVehicle).mockReset()
    vi.mocked(completePickup).mockReset()
    vi.mocked(acknowledgeAlert).mockReset()
  })

  it('confirms before marking a vehicle ready, naming the car and customer', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    vi.mocked(moveVehicle).mockResolvedValue({ custody_session: {} })
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Mark ready/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Mark ready/i }))

    // The dialog must say what is about to happen. A bare "are you sure?" on a
    // handover is decoration rather than a check.
    await waitFor(() =>
      expect(screen.getByText('Mark this vehicle ready?')).toBeInTheDocument()
    )
    expect(screen.getAllByText('UP85CA1234').length).toBeGreaterThan(1)
    expect(screen.getAllByText('Aisha Verma').length).toBeGreaterThan(1)

    // Nothing has been sent yet - the action waits on the confirmation.
    expect(moveVehicle).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))

    await waitFor(() =>
      expect(moveVehicle).toHaveBeenCalledWith('custody-1', 'READY_FOR_PICKUP')
    )
  })

  it('cancelling the dialog sends nothing', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(dashboard())
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Mark ready/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Mark ready/i }))

    await waitFor(() =>
      expect(screen.getByText('Mark this vehicle ready?')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }))

    await waitFor(() =>
      expect(screen.queryByText('Mark this vehicle ready?')).not.toBeInTheDocument()
    )
    expect(moveVehicle).not.toHaveBeenCalled()
  })

  it('confirms a completed pickup and warns it cannot be undone here', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(
      dashboard({
        pending_pickups: {
          state: 'ok',
          count: 1,
          items: [row({ custody_status: 'READY_FOR_PICKUP', display_status: 'READY' })],
        },
      })
    )
    vi.mocked(completePickup).mockResolvedValue({ ticket: {} })
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_OPERATOR' }),
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Complete pickup/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Complete pickup/i }))

    await waitFor(() =>
      expect(screen.getByText('Complete this pickup?')).toBeInTheDocument()
    )
    expect(screen.getByText(/cannot be undone from this screen/)).toBeInTheDocument()

    // The row button and the dialog's confirm button share the label, so the
    // click is scoped to the dialog: clicking the row button again would only
    // re-open the confirmation.
    const dialog = screen.getByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /Complete pickup/ }))

    await waitFor(() => expect(completePickup).toHaveBeenCalled())
  })

  it('confirms before acknowledging an alert', async () => {
    vi.mocked(fetchStaffDashboard).mockResolvedValue(
      dashboard({
        capabilities: {
          can_move_vehicle: true,
          can_check_in: true,
          can_complete_pickup: true,
          can_capture_inspection: true,
          can_verify_damage: true,
          can_triage_alerts: true,
        },
      })
    )
    vi.mocked(acknowledgeAlert).mockResolvedValue({ alert: {} })
    renderWithProviders(<ValetDashboardPage />, {
      token: buildToken({ role: 'VALET_MANAGER' }),
    })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Acknowledge/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Acknowledge/i }))

    await waitFor(() =>
      expect(screen.getByText('Acknowledge this alert?')).toBeInTheDocument()
    )
    expect(acknowledgeAlert).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))

    await waitFor(() => expect(acknowledgeAlert).toHaveBeenCalledWith('alert-1'))
  })
})
