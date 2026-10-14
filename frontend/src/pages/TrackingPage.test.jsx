/**
 * Tests for the live tracking dashboard.
 *
 * Leaflet is stubbed out: it measures real DOM geometry and drives browser APIs
 * that jsdom does not implement, so a real `MapContainer` renders nothing
 * useful here. The stubs assert on the *props* handed to them instead, which is
 * what this page is responsible for - the boundary ring it passes, the marker it
 * places, and whether it labels simulated telemetry.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { TrackingPage } from './TrackingPage'
import {
  acknowledgeAlert,
  fetchAlerts,
  fetchSimulatorScenarios,
  fetchTracking,
  resolveAlert,
  simulateTrack,
} from '../services/geofenceService'
import { fetchModelInfo } from '../services/securityMlService'
import { fetchTickets } from '../services/ticketService'
import { buildToken, renderWithProviders } from '../test/utils'

vi.mock('../services/geofenceService', () => ({
  acknowledgeAlert: vi.fn(),
  fetchAlerts: vi.fn(),
  fetchSimulatorScenarios: vi.fn(),
  fetchTracking: vi.fn(),
  resolveAlert: vi.fn(),
  simulateTrack: vi.fn(),
}))

vi.mock('../services/ticketService', () => ({
  fetchTickets: vi.fn(),
}))

vi.mock('../services/securityMlService', () => ({
  fetchModelInfo: vi.fn(),
}))

// Stand-ins that record what the page asked the map to draw.
const drawn = { polygons: [], markers: [], polylines: [] }

vi.mock('react-leaflet', () => {
  const capture = (bucket) =>
    function Stub(props) {
      drawn[bucket].push(props)
      return null
    }
  return {
    MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
    TileLayer: () => null,
    Polygon: capture('polygons'),
    Polyline: capture('polylines'),
    Circle: () => null,
    Marker: capture('markers'),
    Tooltip: ({ children }) => <span>{children}</span>,
      useMap: () => ({
      setView: vi.fn(),
      fitBounds: vi.fn(),
      invalidateSize: vi.fn(),
      getContainer: () => document.createElement('div'),
    }),
  }
})

vi.mock('leaflet', () => ({
  default: {
    divIcon: (options) => ({ ...options, __isDivIcon: true }),
  },
}))

const CUSTODY_ID = 'custody-1'

function view(overrides = {}) {
  return {
    custody_session_id: CUSTODY_ID,
    custody_status: 'PARKED',
    retrieval_active: false,
    movement_expected: false,
    vehicle: {
      id: 'v-1',
      plate_number: 'UP85CA1234',
      make: 'Tata',
      model: 'Nexon',
      color: 'Silver',
    },
    zone: {
      id: 'z-1',
      code: 'ZONE-A',
      name: 'Zone A - Covered Parking',
      geofence_polygon: [
        [77.673, 27.492],
        [77.676, 27.492],
        [77.676, 27.495],
        [77.673, 27.495],
      ],
      centre: { latitude: 27.4935, longitude: 77.6745 },
      radius_metres: 100,
      buffer_metres: 25,
      effective_radius_metres: 100,
      status: 'ACTIVE',
    },
    latest_fix: {
      id: 'f-9',
      latitude: 27.4991,
      longitude: 77.6745,
      speed_kmph: 5,
      recorded_at: '2026-09-08T10:06:00+00:00',
      source: 'SIMULATED',
      is_simulated: true,
      is_within_zone: false,
      distance_from_zone_metres: 400,
      movement_status: 'BREACH',
    },
    last_updated_at: '2026-09-08T10:06:00+00:00',
    movement_status: 'BREACH',
    fix_count: 11,
    status_counts: { NORMAL: 5, WARNING: 1, SUSPICIOUS: 1, BREACH: 4 },
    sources: ['SIMULATED'],
    has_simulated_telemetry: true,
    has_hardware_telemetry: false,
    telemetry_note:
      'This track contains SIMULATED telemetry generated for development and must not be presented as hardware data.',
    track: [
      {
        id: 'f-1',
        latitude: 27.4935,
        longitude: 77.6745,
        movement_status: 'NORMAL',
        source: 'SIMULATED',
        is_simulated: true,
      },
      {
        id: 'f-9',
        latitude: 27.4991,
        longitude: 77.6745,
        movement_status: 'BREACH',
        source: 'SIMULATED',
        is_simulated: true,
      },
    ],
    alerts: [],
    open_alert_count: 1,
    ...overrides,
  }
}

function alertsPayload(alerts = []) {
  return { alerts, total: alerts.length, page: 1, page_size: 50 }
}

function openAlert(overrides = {}) {
  return {
    id: 'a-1',
    alert_type: 'GEOFENCE_BREACH',
    severity: 'HIGH',
    status: 'OPEN',
    title: 'Vehicle outside its assigned parking zone',
    reason: 'Position is 400 m beyond the zone boundary, outside the 25 m GPS tolerance.',
    confidence: 0.95,
    occurred_at: '2026-09-08T10:05:00+00:00',
    evidence: { distance_from_boundary_metres: 400 },
    ...overrides,
  }
}

function renderPage({ role = 'VALET_MANAGER', path = `/tracking/${CUSTODY_ID}` } = {}) {
  return renderWithProviders(
    <Routes>
      <Route path="/tracking" element={<TrackingPage />} />
      <Route path="/tracking/:custodyId" element={<TrackingPage />} />
    </Routes>,
    { route: path, token: buildToken({ role }) },
  )
}

beforeEach(() => {
  drawn.polygons.length = 0
  drawn.markers.length = 0
  drawn.polylines.length = 0
  vi.mocked(fetchModelInfo).mockReset()
  // Default to an untrained deployment: the panel must degrade to "rule engine
  // only" rather than throwing, which is the state most checkouts are in.
  vi.mocked(fetchModelInfo).mockResolvedValue({
    available: false,
    reason: 'No model artifact configured.',
  })
  vi.mocked(fetchTracking).mockReset()
  vi.mocked(fetchAlerts).mockReset()
  vi.mocked(fetchSimulatorScenarios).mockReset()
  vi.mocked(simulateTrack).mockReset()
  vi.mocked(acknowledgeAlert).mockReset()
  vi.mocked(resolveAlert).mockReset()
  vi.mocked(fetchTickets).mockReset()
  vi.mocked(fetchSimulatorScenarios).mockResolvedValue({
    scenarios: [
      { id: 'parked', label: 'Parked in bay' },
      { id: 'unauthorised', label: 'Unauthorised movement' },
    ],
  })
})

describe('TrackingPage', () => {
  it('shows the vehicle, its zone and the current verdict', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()

    expect(await screen.findByText('UP85CA1234')).toBeInTheDocument()
    await waitFor(() => expect(fetchTracking).toHaveBeenCalledWith(CUSTODY_ID))
    expect(screen.getAllByText('Geofence breach').length).toBeGreaterThan(0)
    expect(screen.getByText(/Zone A - Covered Parking/)).toBeInTheDocument()
  })

  it('draws the stored geofence ring, not a circle derived from the radius', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()
    await screen.findByText('UP85CA1234')

    expect(drawn.polygons.length).toBeGreaterThan(0)
    // GeoJSON [lon, lat] must be swapped to Leaflet's [lat, lng] exactly once.
    expect(drawn.polygons[0].positions[0]).toEqual([27.492, 77.673])
  })

  it('marks the current position and every flagged fix on the track', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()
    await screen.findByText('UP85CA1234')

    // One flagged fix plus the current-position marker.
    expect(drawn.markers.length).toBe(2)
    expect(drawn.polylines.length).toBe(1)
    expect(drawn.polylines[0].positions).toHaveLength(2)
  })

  it('labels simulated telemetry prominently and per reading', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()

    expect(await screen.findByText('Simulated telemetry')).toBeInTheDocument()
    expect(
      screen.getByText(
        /must not be presented as hardware data/,
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByText(/This reading is/).length).toBeGreaterThan(0)
  })

  it('does not show the banner for hardware telemetry', async () => {
    vi.mocked(
      fetchTracking,
    ).mockResolvedValue(
      view({
        has_simulated_telemetry: false,
        sources: ['HARDWARE'],
        telemetry_note: null,
        track: view().track.map((fix) => ({ ...fix, source: 'HARDWARE', is_simulated: false })),
        latest_fix: { ...view().latest_fix, source: 'HARDWARE', is_simulated: false },
      }),
    )
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()
    await screen.findByText('UP85CA1234')

    expect(screen.queryByText('Simulated telemetry')).not.toBeInTheDocument()
  })

  it('reports the fix count, sources and the rule-based provenance', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()

    expect(await screen.findByText('Fixes recorded')).toBeInTheDocument()
    // "Simulated" appears in both the current reading's source and the summary's
    // source list; both should be labelled.
    expect(screen.getAllByText('Simulated').length).toBeGreaterThanOrEqual(2)
    // The provenance note must say the verdicts come from a stated rule set.
    // It used to read "not a trained model", which stopped being true once the
    // prototype classifier was added - what still holds is that the model may
    // only raise a verdict, never lower it.
    const note = document.querySelector('.tracking-layout__note')
    expect(note).not.toBeNull()
    expect(note.textContent).toContain('stated rule set')
    expect(note.textContent).toContain('may raise a verdict but never lower it')
    expect(note.textContent).toContain('geofence breach always alerts')
  })

  it('offers a session picker when no custody session is chosen', async () => {
    vi.mocked(fetchTickets).mockResolvedValue({
      items: [
        {
          ticket_code: 'VT-202609-ABC123',
          vehicle: { plate_number: 'UP85CA1234' },
          custody_session: { id: CUSTODY_ID, status: 'PARKED', dropped_off_at: null },
        },
      ],
    })

    renderPage({ path: '/tracking' })

    expect(await screen.findByText('Choose a vehicle to track')).toBeInTheDocument()
    expect(screen.getByText('VT-202609-ABC123 · PARKED')).toBeInTheDocument()
  })

  it('says so plainly when there is nothing in custody', async () => {
    vi.mocked(fetchTickets).mockResolvedValue({ items: [] })

    renderPage({ path: '/tracking' })

    expect(await screen.findByText('No vehicles in custody')).toBeInTheDocument()
  })

  it('shows the alerts raised during the custody window', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload([openAlert()]))

    renderPage()

    expect(
      await screen.findByText('Position is 400 m beyond the zone boundary, outside the 25 m GPS tolerance.'),
    ).toBeInTheDocument()
  })

  it('lets a manager acknowledge an alert', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload([openAlert()]))
    vi.mocked(acknowledgeAlert).mockResolvedValue({ ...openAlert(), status: 'ACKNOWLEDGED' })

    renderPage({ role: 'VALET_MANAGER' })
    fireEvent.click(await screen.findByRole('button', { name: /Acknowledge/i }))

    await waitFor(() => expect(acknowledgeAlert).toHaveBeenCalledWith('a-1'))
  })

  it('lets a manager dismiss an alert as a false positive', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload([openAlert()]))
    vi.mocked(resolveAlert).mockResolvedValue({ ...openAlert(), status: 'DISMISSED' })

    renderPage({ role: 'VALET_MANAGER' })
    fireEvent.click(await screen.findByRole('button', { name: /Dismiss as false positive/i }))

    await waitFor(() =>
      expect(resolveAlert).toHaveBeenCalledWith('a-1', { notes: undefined, dismiss: true }),
    )
  })

  it('hides triage controls from a valet operator but still shows the alerts', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload([openAlert()]))

    renderPage({ role: 'VALET_OPERATOR' })

    expect(
      await screen.findByText('Position is 400 m beyond the zone boundary, outside the 25 m GPS tolerance.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Acknowledge/i })).not.toBeInTheDocument()
    expect(
      screen.getByText('Only a manager or administrator can act on an alert.'),
    ).toBeInTheDocument()
  })

  it('shows the simulator to staff who may post telemetry', async () => {
    // A valet operator can record telemetry on the backend, so the panel is
    // theirs too. Hiding it here would contradict the API's own role rules.
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage({ role: 'VALET_OPERATOR' })

    expect(await screen.findByText('Development telemetry simulator')).toBeInTheDocument()
  })

  it('hides the simulator from a customer, who cannot post telemetry', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage({ role: 'CUSTOMER' })
    await screen.findByText('UP85CA1234')

    expect(screen.queryByText('Development telemetry simulator')).not.toBeInTheDocument()
  })

  it('generates simulated telemetry and says what it did', async () => {
    vi.mocked(fetchTracking).mockResolvedValue(view())
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())
    vi.mocked(simulateTrack).mockResolvedValue({
      source: 'SIMULATED',
      is_simulated: true,
      fix_count: 8,
      status_counts: { NORMAL: 4, BREACH: 3, SUSPICIOUS: 1 },
      alerts_raised: [{ alert_type: 'GEOFENCE_BREACH' }],
      notes: ['Every fix in this track is SIMULATED telemetry generated for development.'],
    })

    renderPage({ role: 'VALET_MANAGER' })

    fireEvent.click(await screen.findByRole('button', { name: /Generate and ingest/i }))

    await waitFor(() =>
      expect(simulateTrack).toHaveBeenCalledWith(CUSTODY_ID, {
        scenario: 'unauthorised',
        seed: 42,
      }),
    )
    expect(
      await screen.findByText(/Every fix in this track is SIMULATED telemetry/),
    ).toBeInTheDocument()
  })

  it('surfaces an API failure with a retry rather than a blank map', async () => {
    const failure = Object.assign(new Error('boom'), {
      response: { status: 500, data: { error: { code: 'INTERNAL', message: 'Server exploded' } } },
    })
    vi.mocked(fetchTracking).mockRejectedValue(failure)
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()

    expect(await screen.findByText('Server exploded', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Try again/i })).toBeInTheDocument()
  })

  it('explains itself when no position has been recorded', async () => {
    vi.mocked(
      fetchTracking,
    ).mockResolvedValue(view({ latest_fix: null, track: [], movement_status: null, zone: null }))
    vi.mocked(fetchAlerts).mockResolvedValue(alertsPayload())

    renderPage()

    expect(await screen.findByText(/No position has been recorded yet/)).toBeInTheDocument()
    expect(screen.getByText(/no zone assigned/i)).toBeInTheDocument()
  })
})
