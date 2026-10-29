import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  ConditionStatusCard,
  CurrentLocationCard,
  EstimatedRetrievalCard,
  SecurityStatusCard,
  VehicleStatusCard,
} from './DashboardCards'

const custody = {
  state: 'ok',
  status: 'PARKED',
  checked_in_at: new Date(Date.now() - 12 * 60000).toISOString(),
  ticket_code: 'VT-202609-K7M3QD',
}

const vehicle = {
  state: 'ok',
  plate_number: 'UP85CA1234',
  make: 'Maruti',
  model: 'Swift',
  status: 'IN_CUSTODY',
}

describe('VehicleStatusCard', () => {
  it('shows the plate, custody status and check-in time', () => {
    render(<VehicleStatusCard vehicle={vehicle} custody={custody} />)

    expect(screen.getByText(/UP85CA1234/)).toBeInTheDocument()
    // "Parked" appears twice by design - once as the badge and once in the
    // custody-status row - so assert on both rather than on one of them.
    expect(screen.getAllByText('Parked')).toHaveLength(2)
    expect(screen.getByText('12 minutes ago')).toBeInTheDocument()
  })

  it('says what is missing rather than rendering blank', () => {
    render(
      <VehicleStatusCard
        vehicle={{ state: 'empty', reason: 'No vehicle is in custody.' }}
        custody={{ state: 'empty' }}
      />
    )

    expect(screen.getByText('No vehicle is in custody.')).toBeInTheDocument()
    // A blank card would be indistinguishable from "nothing is wrong".
    expect(screen.queryByText(/UP85/)).not.toBeInTheDocument()
  })
})

describe('SecurityStatusCard', () => {
  const secure = {
    state: 'ok',
    status: 'SECURE',
    summary: 'Vehicle inside its zone; latest movement verdict NORMAL.',
    movement_status: 'NORMAL',
    open_alert_count: 0,
    alerts: [],
  }

  it('shows the headline posture and the verdict behind it', () => {
    render(<SecurityStatusCard security={secure} />)

    expect(screen.getByText('Secure')).toBeInTheDocument()
    expect(screen.getByText('NORMAL')).toBeInTheDocument()
  })

  it('shows a breach loudly', () => {
    render(
      <SecurityStatusCard
        security={{
          ...secure,
          status: 'BREACH',
          summary: 'A geofence breach needs attention.',
          movement_status: 'BREACH',
          open_alert_count: 1,
          highest_severity: 'HIGH',
          alerts: [
            { id: 'a1', alert_type: 'GEOFENCE_BREACH', severity: 'HIGH', status: 'OPEN' },
          ],
        }}
      />
    )

    expect(screen.getByText('Breach')).toBeInTheDocument()
    expect(screen.getByText('GEOFENCE_BREACH')).toBeInTheDocument()
  })

  it('explains an unknown posture instead of implying all clear', () => {
    render(
      <SecurityStatusCard
        security={{
          state: 'ok',
          status: 'UNKNOWN',
          summary: 'No telemetry has been recorded, so there is no security verdict yet.',
          movement_status: null,
          open_alert_count: 0,
        }}
      />
    )

    expect(screen.getByText('Unknown')).toBeInTheDocument()
    // The summary and the movement row both say "no telemetry", which is the
    // point: neither may quietly imply all clear.
    expect(screen.getAllByText(/No telemetry/).length).toBeGreaterThanOrEqual(2)
  })
})

describe('CurrentLocationCard', () => {
  const zone = { state: 'ok', code: 'ZONE-A', name: 'Main yard', capacity: 20, current_occupancy: 7 }

  it('shows the zone, coordinates and how fresh the fix is', () => {
    render(
      <CurrentLocationCard
        location={{
          state: 'ok',
          latitude: 27.4935,
          longitude: 77.6745,
          movement_status: 'NORMAL',
          recorded_at: new Date(Date.now() - 3 * 60000).toISOString(),
        }}
        zone={zone}
      />
    )

    expect(screen.getByText('ZONE-A')).toBeInTheDocument()
    expect(screen.getByText('27.49350, 77.67450')).toBeInTheDocument()
    expect(screen.getByText('7 / 20')).toBeInTheDocument()
  })

  it('always states simulated telemetry as simulated', () => {
    // A simulated track must never read as a hardware position.
    render(
      <CurrentLocationCard
        location={{
          state: 'ok',
          latitude: 27.49,
          longitude: 77.67,
          telemetry_note: 'This track contains SIMULATED telemetry.',
        }}
        zone={zone}
      />
    )

    expect(screen.getByText(/SIMULATED telemetry/)).toBeInTheDocument()
  })

  it('says so when no fix has been recorded', () => {
    render(
      <CurrentLocationCard
        location={{ state: 'empty', reason: 'No GPS fix has been recorded yet.' }}
        zone={{ state: 'empty' }}
      />
    )

    expect(screen.getByText('No GPS fix has been recorded yet.')).toBeInTheDocument()
  })
})

describe('EstimatedRetrievalCard', () => {
  const estimate = {
    state: 'ok',
    estimated_minutes: 8,
    min_wait_minutes: 6,
    max_wait_minutes: 10,
    queue_position: 2,
    recommended_dispatch: 'Now',
    predicted_ready_at: new Date(Date.now() + 8 * 60000).toISOString(),
    disclaimer: 'Trained on synthetic data. Indicative only.',
    prediction: { requested_at: new Date(Date.now() - 60000).toISOString() },
  }

  it('shows the three figures the brief names', () => {
    render(<EstimatedRetrievalCard retrieval={estimate} />)

    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('minutes')).toBeInTheDocument()
    expect(screen.getByText('Queue position')).toBeInTheDocument()
    expect(screen.getByText('Recommended dispatch')).toBeInTheDocument()
    expect(screen.getByText('Now')).toBeInTheDocument()
  })

  it('shows the estimate as a range, not a false point value', () => {
    render(<EstimatedRetrievalCard retrieval={estimate} />)

    expect(screen.getByText('(6–10 min)')).toBeInTheDocument()
  })

  it('always renders the synthetic-data caveat', () => {
    render(<EstimatedRetrievalCard retrieval={estimate} />)

    expect(screen.getByText('Trained on synthetic data. Indicative only.')).toBeInTheDocument()
    expect(screen.getByText('Synthetic-data model')).toBeInTheDocument()
  })

  it('explains that no estimate exists yet', () => {
    render(
      <EstimatedRetrievalCard
        retrieval={{
          state: 'empty',
          reason: 'No retrieval estimate yet. One is generated when you request the vehicle.',
        }}
      />
    )

    expect(screen.getByText(/No retrieval estimate yet/)).toBeInTheDocument()
  })
})

describe('ConditionStatusCard', () => {
  it('shows a clean report as clean', () => {
    render(
      <ConditionStatusCard
        condition={{
          state: 'ok',
          status: 'CLEAN',
          summary: 'No damage detected against the drop-off photographs.',
          total_detections: 0,
          unverified_count: 0,
          confirmed_count: 0,
          pickup_completed: false,
          detector_disclaimer: 'Findings are produced by classical OpenCV image differencing.',
        }}
      />
    )

    expect(screen.getByText('No damage')).toBeInTheDocument()
    expect(screen.getByText(/classical OpenCV image differencing/)).toBeInTheDocument()
  })

  it('flags unverified findings as needing review, not as damage', () => {
    // An unverified finding is potential damage. Calling it damage would
    // overstate what the detector actually established.
    render(
      <ConditionStatusCard
        condition={{
          state: 'ok',
          status: 'REVIEW',
          summary: '2 unverified finding(s) awaiting confirmation.',
          total_detections: 2,
          unverified_count: 2,
          confirmed_count: 0,
          pickup_completed: true,
        }}
      />
    )

    expect(screen.getByText('Needs review')).toBeInTheDocument()
    expect(screen.getByText('2 unverified finding(s) awaiting confirmation.')).toBeInTheDocument()
  })
})
