/**
 * Tests for the manager dashboard and its charts.
 *
 * Recharts is stubbed out: like Leaflet it measures real DOM geometry and draws
 * nothing under jsdom, so asserting on rendered bars would assert on nothing.
 * The stubs record what they were handed instead.
 *
 * That is the right division of labour anyway. The plotting is a library's job
 * and is already tested by the library. What this dashboard is responsible for
 * is *whether* to plot at all — and that decision is plain logic, tested here
 * directly, which is the requirement the brief states as "do not create
 * meaningless charts".
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ManagerDashboardPage } from './ManagerDashboardPage'
import {
  AlertsChart,
  ChartFrame,
  CustodyDurationChart,
  DamageChart,
  RetrievalTimeChart,
  VehiclesPerDayChart,
} from '../components/manager/Charts'
import { DamageTable } from '../components/manager/Tables'
import {
  chartIsDrawable,
  downloadManagerReport,
  fetchManagerDashboard,
  formatMetric,
} from '../services/managerService'
import { buildToken, renderWithProviders } from '../test/utils'

vi.mock('recharts', () => {
  // Named components rather than an arrow-returning-arrow factory, so the
  // display-name rule is satisfied and a failure points at a real component.
  const BarChart = ({ data }) => <div data-chart="bar" data-points={JSON.stringify(data ?? [])} />
  const LineChart = ({ data }) => <div data-chart="line" data-points={JSON.stringify(data ?? [])} />
  const Container = ({ children }) => <div data-chart="container">{children}</div>
  return {
    ResponsiveContainer: Container,
    BarChart,
    LineChart,
    Bar: () => null,
    Line: () => null,
    Cell: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
  }
})

vi.mock('../services/managerService', async () => {
  const actual = await vi.importActual('../services/managerService')
  return {
    ...actual,
    fetchManagerDashboard: vi.fn(),
    downloadManagerReport: vi.fn(),
  }
})

function series(n, extra = {}) {
  return Array.from({ length: n }, (_, i) => ({
    date: `2026-08-${String(i + 1).padStart(2, '0')}`,
    value: i + 1,
    ...extra,
  }))
}

function payload(overrides = {}) {
  return {
    filters: { date_from: '2026-08-01', date_to: '2026-08-31', status: null, severity: null, vehicle_id: null },
    filter_options: {
      statuses: ['PARKED', 'READY_FOR_PICKUP'],
      severities: ['CRITICAL', 'LOW'],
      vehicles: [{ id: 'v1', label: 'UP85CA1234 · Maruti Swift' }],
    },
    provenance: { contains_generated_history: false, generated_tickets: 0, note: 'No generated history.' },
    metrics: {
      vehicles_today: { label: 'Vehicles checked in today', value: 12, unit: 'vehicles', windowed: false },
      active_sessions: { label: 'Active custody sessions', value: 3, unit: 'sessions', windowed: false },
      completed_sessions: { label: 'Completed sessions', value: 340, unit: 'sessions', windowed: true },
      pending_pickups: { label: 'Pending pickups', value: 1, unit: 'vehicles', windowed: false },
      open_alerts: { label: 'Open security alerts', value: 4, unit: 'alerts', windowed: false },
      potential_damage: { label: 'Potential damage cases', value: 9, unit: 'findings', windowed: true },
      avg_retrieval_minutes: { label: 'Average retrieval time', value: 14.7, unit: 'minutes', windowed: true, n: 320 },
      avg_custody_hours: { label: 'Average custody duration', value: 5.7, unit: 'hours', windowed: true, n: 320 },
    },
    charts: {
      vehicles_per_day: { title: 'Vehicles per day', unit: 'vehicles', kind: 'bar', series: series(31), n: 31, min_points: 2, sufficient: true, note: 'Per calendar day.' },
      retrieval_time: { title: 'Average retrieval time', unit: 'minutes', kind: 'line', series: series(28), n: 28, min_points: 5, sufficient: true, note: 'Measured, not predicted.', records: 320 },
      security_alerts: { title: 'Security alerts', unit: 'alerts', kind: 'bar', series: [{ week: '2026-08-03', total: 4, open: 2, by_severity: { HIGH: 3, LOW: 1 } }], n: 4, min_points: 10, sufficient: false, note: 'Per week.' },
      damage_detections: { title: 'Damage detections', unit: 'findings', kind: 'bar', series: series(12), n: 60, min_points: 10, sufficient: true, note: 'By outcome.', by_region: [{ region: 'FRONT_BUMPER', count: 12 }] },
      custody_duration: { title: 'Custody duration', unit: 'sessions', kind: 'bar', series: [{ bucket: '4-6h', value: 90 }], n: 320, min_points: 10, sufficient: true, note: 'Distribution.' },
    },
    tables: {
      recent_tickets: [
        { ticket_id: 't1', ticket_code: 'VT-000001', custody_status: 'PARKED', issued_at: '2026-08-20T10:00:00Z', vehicle: { plate_number: 'UP85CA1234' }, customer: { name: 'Aisha Verma' }, zone_code: 'ZONE-A', duration_hours: 5.2, is_open: true },
      ],
      active_vehicles: [
        { custody_session_id: 'c1', custody_status: 'PARKED', vehicle: { plate_number: 'UP85CA1234' }, customer: { name: 'Aisha Verma' }, zone_code: 'ZONE-A', hours_in_custody: 2.1, open_alerts: 1 },
      ],
      security_alerts: [
        { id: 'a1', alert_type: 'GEOFENCE_BREACH', severity: 'HIGH', status: 'OPEN', title: 'Left zone', reason: 'Outside boundary', occurred_at: '2026-08-20T10:00:00Z', is_open: true, vehicle: { plate_number: 'UP85CA1234' } },
      ],
      damage_cases: [
        { id: 'd1', damage_type: 'SCRATCH', region: 'FRONT_BUMPER', severity: 'MINOR', confidence: 0.81, verification_status: 'UNVERIFIED', vehicle: { plate_number: 'UP85CA1234' }, captured_at: '2026-08-20T10:00:00Z' },
      ],
      pending_disputes: [
        { custody_session_id: 'c-disputed', ticket_code: 'VT-000002', vehicle: { plate_number: 'UP85CA9876' }, customer: { name: 'Ravi Kumar' } },
      ],
    },
    ...overrides,
  }
}

describe('chartIsDrawable', () => {
  it('refuses a series the backend marked insufficient', () => {
    expect(chartIsDrawable({ sufficient: false, series: series(2), min_points: 5 })).toBe(false)
  })

  it('refuses a series shorter than its stated minimum even if unmarked', () => {
    // Belt and braces: if a future payload omits `sufficient`, the length check
    // still stops a two-point line from being drawn as a trend.
    expect(chartIsDrawable({ series: series(2), min_points: 5 })).toBe(false)
    expect(chartIsDrawable({ series: series(6), min_points: 5 })).toBe(true)
  })

  it('refuses a missing or malformed chart', () => {
    expect(chartIsDrawable(null)).toBe(false)
    expect(chartIsDrawable({ sufficient: true })).toBe(false)
  })
})

describe('ChartFrame', () => {
  it('says how much the chart is made of', () => {
    render_({ title: 'Vehicles per day', unit: 'vehicles', series: series(31), n: 31, min_points: 2, sufficient: true })

    expect(screen.getByText(/31 vehicles/)).toBeInTheDocument()
  })

  it('reports the underlying record count when it differs from the point count', () => {
    // A chart can have 28 daily points each resting on several cars. Showing
    // only the point count would hide how thin each point is.
    render_({
      title: 'Average retrieval time', unit: 'minutes', series: series(28),
      n: 28, min_points: 5, sufficient: true, records: 320,
    })

    expect(screen.getByText(/across 320 records/)).toBeInTheDocument()
  })

  it('refuses to draw below the minimum and says what is needed', () => {
    render_({
      title: 'Security alerts', unit: 'alerts', series: series(2),
      n: 2, min_points: 10, sufficient: false,
    })

    expect(screen.getByText(/Not enough data to chart/)).toBeInTheDocument()
    expect(screen.getByText(/at least 10 are needed/)).toBeInTheDocument()
    expect(screen.getByText(/Widen the date range/)).toBeInTheDocument()
  })

  it('still shows the sample size when it refuses to draw', () => {
    render_({ title: 'Security alerts', unit: 'alerts', series: [], n: 0, min_points: 10, sufficient: false })

    // Both the stats line and the refusal message mention the count, which is
    // deliberate: the reader should not have to hunt for why it was skipped.
    expect(screen.getAllByText(/0 alerts/).length).toBeGreaterThan(0)
    expect(screen.getByText(/below the 10-point minimum/)).toBeInTheDocument()
  })

  it('always prints the methodology note', () => {
    render_({
      title: 'Damage detections', unit: 'findings', series: series(12),
      n: 60, min_points: 10, sufficient: true, note: 'By verification outcome.',
    })

    expect(screen.getByText('By verification outcome.')).toBeInTheDocument()
  })
})

function render_(chart) {
  return render(
    <ChartFrame chart={chart}>
      <div data-chart="plot" />
    </ChartFrame>
  )
}

describe('chart components survive a missing chart', () => {
  // Regression: these read `chart.series` to build their plot, and JSX children
  // are evaluated before `ChartFrame`'s own guard can run. An undefined chart
  // therefore threw during the parent's render and blanked the entire page -
  // which happens on any payload that omits a section.
  const cases = [
    ['VehiclesPerDayChart', VehiclesPerDayChart],
    ['RetrievalTimeChart', RetrievalTimeChart],
    ['AlertsChart', AlertsChart],
    ['DamageChart', DamageChart],
    ['CustodyDurationChart', CustodyDurationChart],
  ]

  it.each(cases)('%s renders nothing rather than throwing', (_name, Component) => {
    expect(() => render(<Component chart={undefined} />)).not.toThrow()
  })

  it('a histogram with one bucket still draws', () => {
    // The refusal must key off the sample size, not the bucket count: a
    // custody-duration distribution can rest on hundreds of records and still
    // fall in a single two-hour bucket.
    render(
      <CustodyDurationChart
        chart={{
          title: 'Custody duration', unit: 'sessions', series: [{ bucket: '4-6h', value: 320 }],
          n: 320, min_points: 10, sufficient: true, note: 'Distribution.',
        }}
      />
    )

    expect(screen.queryByText(/Not enough data to chart/)).not.toBeInTheDocument()
    expect(screen.getByText(/320 sessions/)).toBeInTheDocument()
  })
})

describe('formatMetric', () => {
  it('renders a measurement with its unit', () => {
    expect(formatMetric({ value: 14.7, unit: 'minutes' })).toBe('14.7 minutes')
    expect(formatMetric({ value: 12, unit: 'vehicles' })).toBe('12 vehicles')
  })

  it('says "No data" rather than printing a null', () => {
    // An untrained predictor or an empty range must not render as "0 minutes",
    // which would read as a measured result of zero.
    expect(formatMetric({ value: null, unit: 'minutes' })).toBe('No data')
  })
})

describe('ManagerDashboardPage', () => {
  beforeEach(() => {
    vi.mocked(fetchManagerDashboard).mockReset()
    vi.mocked(downloadManagerReport).mockReset()
  })

  it('renders every metric card', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText('Vehicles checked in today')).toBeInTheDocument()
    )
    expect(screen.getByText('Active custody sessions')).toBeInTheDocument()
    expect(screen.getByText('Open security alerts')).toBeInTheDocument()
    // Also a chart title, so it legitimately appears twice.
    expect(screen.getAllByText('Average retrieval time').length).toBeGreaterThan(0)
  })

  it('labels each metric with the period it covers', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getAllByText('right now').length).toBeGreaterThan(0)
    )
    expect(screen.getAllByText(/selected range/).length).toBeGreaterThan(0)
  })

  it('renders all five chart sections', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() => expect(screen.getByText('Vehicles per day')).toBeInTheDocument())
    // Several of these titles are also metric card labels, so they appear twice.
    expect(screen.getAllByText('Average retrieval time').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Security alerts').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Damage detections').length).toBeGreaterThan(0)
    expect(screen.getByText('Custody duration')).toBeInTheDocument()
  })

  it('refuses to draw the alerts chart when the range is too narrow', async () => {
    // The payload marks security_alerts insufficient (n=4, min 10).
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText(/Not enough data to chart/)).toBeInTheDocument()
    )
  })

  it('warns when the dataset contains generated history', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(
      payload({
        provenance: {
          contains_generated_history: true,
          generated_tickets: 1157,
          note: 'This dataset includes synthetic operational history.',
        },
      })
    )
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(
        screen.getByText('This dataset includes synthetic history')
      ).toBeInTheDocument()
    )
  })

  it('does not show the synthetic warning for real data', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText('Vehicles checked in today')).toBeInTheDocument()
    )
    expect(
      screen.queryByText('This dataset includes synthetic history')
    ).not.toBeInTheDocument()
  })

  it('renders the manager tables, including the pending dispute queue', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() => expect(screen.getByText('Recent tickets')).toBeInTheDocument())
    expect(screen.getByText('Active vehicles')).toBeInTheDocument()
    expect(screen.getByText('Damage cases')).toBeInTheDocument()
    expect(screen.getByText('Pending custody disputes')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /review dispute/i })).toHaveAttribute(
      'href',
      '/condition/c-disputed',
    )
    expect(screen.getAllByText('UP85CA1234').length).toBeGreaterThan(0)
  })

  it('passes the filters through to the request', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() => expect(fetchManagerDashboard).toHaveBeenCalled())

    const filters = vi.mocked(fetchManagerDashboard).mock.calls[0][0]
    expect(filters.dateFrom).toBeTruthy()
    expect(filters.dateTo).toBeTruthy()
  })

  it('re-requests when a filter changes', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    // Wait for the filter bar to actually render, not merely for the query to
    // have been issued: the page shows a spinner until the data resolves, and
    // the controls do not exist yet.
    await waitFor(() =>
      expect(screen.getByDisplayValue('All severities')).toBeInTheDocument()
    )
    expect(fetchManagerDashboard).toHaveBeenCalledTimes(1)

    // The label wraps a span and a select, which testing-library's label
    // matcher does not resolve here; the select is identified by the option
    // only it offers.
    fireEvent.change(screen.getByDisplayValue('All severities'), {
      target: { value: 'CRITICAL' },
    })

    await waitFor(() => expect(fetchManagerDashboard).toHaveBeenCalledTimes(2))
    expect(vi.mocked(fetchManagerDashboard).mock.calls[1][0].severity).toBe('CRITICAL')
  })

  it('downloads a CSV report and reports the provenance', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    vi.mocked(downloadManagerReport).mockResolvedValue({
      filename: 'valet-operations-20260831.csv',
      containsGeneratedHistory: true,
    })
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Download CSV/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Download CSV/i }))

    await waitFor(() => expect(downloadManagerReport).toHaveBeenCalled())
    expect(vi.mocked(downloadManagerReport).mock.calls[0][1]).toBe('csv')
    await waitFor(() =>
      expect(screen.getByText(/includes synthetic operational history/)).toBeInTheDocument()
    )
  })

  it('reports a failed export rather than failing silently', async () => {
    vi.mocked(fetchManagerDashboard).mockResolvedValue(payload())
    vi.mocked(downloadManagerReport).mockRejectedValue(new Error('Network unavailable'))
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Download JSON/i })).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: /Download JSON/i }))

    await waitFor(() => expect(screen.getByText('Export failed')).toBeInTheDocument())
    expect(screen.getByText(/Network unavailable/)).toBeInTheDocument()
  })

  it('shows an unreachable backend', async () => {
    vi.mocked(fetchManagerDashboard).mockRejectedValue({
      code: 'NETWORK_ERROR',
      message: 'Could not reach the API.',
    })
    renderWithProviders(<ManagerDashboardPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText('Could not reach the API.')).toBeInTheDocument(),
      { timeout: 5000 }
    )
  })
})

describe('DamageTable', () => {
  it('labels an unverified finding potential, never confirmed damage', () => {
    // The detector flagged it; nobody has confirmed it. Calling it damage
    // would overstate what the algorithm established.
    render_damage([{
      id: 'd1', damage_type: 'SCRATCH', region: 'FRONT_BUMPER', severity: 'MINOR',
      confidence: 0.81, verification_status: 'UNVERIFIED', vehicle: { plate_number: 'UP85CA1234' },
    }])

    expect(screen.getByText(/SCRATCH \(potential\)/)).toBeInTheDocument()
    expect(screen.getByText('awaiting review')).toBeInTheDocument()
  })

  it('does not add "potential" to a confirmed finding', () => {
    render_damage([{
      id: 'd2', damage_type: 'DENT', region: 'HOOD', severity: 'MODERATE',
      confidence: 0.9, verification_status: 'CONFIRMED', verified_by: 'manager@example.com',
      vehicle: { plate_number: 'UP85CA1234' },
    }])

    expect(screen.getByText('DENT')).toBeInTheDocument()
    expect(screen.queryByText(/potential/)).not.toBeInTheDocument()
    expect(screen.getByText('manager@example.com')).toBeInTheDocument()
  })

  it('says so when there is nothing to show', () => {
    render_damage([])
    expect(screen.getByText('No damage findings in the selected range.')).toBeInTheDocument()
  })
})

function render_damage(rows) {
  return render(<DamageTable rows={rows} />)
}
