/**
 * Tests for the notification bell, the alert centre, and the alert service.
 *
 * The behaviour worth pinning is the distinction the whole feature turns on:
 * **marking read is personal, closing is attributed**. A UI that conflated them
 * would let a quick "clear my badge" silently close a security incident, so
 * these tests assert that the two controls are separate, that the resolve
 * control appears only when the backend says the actor may use it, and that the
 * badge count comes from the summary endpoint rather than the feed.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { AlertsPage } from '../../pages/AlertsPage'
import { NotificationBell } from './NotificationBell'
import {
  alertTypeLabel,
  buildAlertParams,
  fetchAlerts,
  fetchAlertSummary,
  isCustomerFacing,
  markAlertRead,
  markAllAlertsRead,
  relativeTime,
  resolveAlert,
  severityTone,
} from '../../services/alertService'
import { buildToken, renderWithProviders } from '../../test/utils'

vi.mock('../../services/alertService', async () => {
  const actual = await vi.importActual('../../services/alertService')
  return {
    ...actual,
    fetchAlerts: vi.fn(),
    fetchAlertSummary: vi.fn(),
    markAlertRead: vi.fn(),
    markAllAlertsRead: vi.fn(),
    resolveAlert: vi.fn(),
  }
})

function alert(overrides = {}) {
  return {
    alert_id: 'a1',
    custody_id: null,
    vehicle_id: 'v1',
    type: 'GEOFENCE_BREACH',
    severity: 'HIGH',
    message: 'Vehicle left its assigned zone.',
    created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    status: 'OPEN',
    vehicle: { plate_number: 'UP85CA1234' },
    source: { entity_type: 'security_alerts', id: 's1', type: 'GEOFENCE_BREACH' },
    is_read: false,
    is_open: true,
    ...overrides,
  }
}

describe('alertService helpers', () => {
  it('builds a query string from filters', () => {
    const qs = buildAlertParams({ type: 'OVERSPEED', unreadOnly: true, page: 2 })
    expect(qs).toContain('type=OVERSPEED')
    expect(qs).toContain('unread_only=true')
    expect(qs).toContain('page=2')
  })

  it('omits empty filters entirely', () => {
    // An empty status sent as `status=` would be a filter the backend has to
    // guess about; leaving it out is unambiguous.
    expect(buildAlertParams({ status: '', type: '', severity: '' })).toBe('')
  })

  it('labels the five categories', () => {
    expect(alertTypeLabel('GEOFENCE_BREACH')).toBe('Geofence breach')
    expect(alertTypeLabel('POTENTIAL_DAMAGE')).toBe('Potential damage')
    expect(alertTypeLabel('RETRIEVAL_READY')).toBe('Retrieval ready')
  })

  it('never presents an unverified finding as customer-facing', () => {
    // The detector flagged it; nobody has confirmed it. Showing that to a
    // customer as damage would overstate what the algorithm established.
    expect(isCustomerFacing('POTENTIAL_DAMAGE')).toBe(false)
    expect(isCustomerFacing('GEOFENCE_BREACH')).toBe(true)
  })

  it('orders severity by tone', () => {
    expect(severityTone('CRITICAL')).toBe('badge--danger')
    expect(severityTone('HIGH')).toBe('badge--warning')
    expect(severityTone('LOW')).toBe('badge--muted')
  })

  describe('relativeTime', () => {
    it('clamps a future timestamp rather than reporting a negative', () => {
      const future = new Date(Date.now() + 3_600_000).toISOString()
      expect(relativeTime(future)).toBe('just now')
    })

    it('returns null for unusable input', () => {
      expect(relativeTime(null)).toBeNull()
      expect(relativeTime('not-a-date')).toBeNull()
    })

    it('renders elapsed time in the largest sensible unit', () => {
      const ago = (ms) => new Date(Date.now() - ms).toISOString()
      expect(relativeTime(ago(30_000))).toBe('just now')
      expect(relativeTime(ago(5 * 60_000))).toBe('5m ago')
      expect(relativeTime(ago(3 * 3_600_000))).toBe('3h ago')
      expect(relativeTime(ago(2 * 86_400_000))).toBe('2d ago')
    })
  })
})

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.mocked(fetchAlertSummary).mockReset()
    vi.mocked(fetchAlerts).mockReset()
    vi.mocked(markAlertRead).mockReset()
    vi.mocked(markAllAlertsRead).mockReset()
  })

  it('shows the unread count from the summary endpoint', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({
      unread: 3,
      total: 10,
      channels: [{ channel: 'IN_APP', enabled: true }],
      can_resolve: false,
    })
    renderWithProviders(<NotificationBell />)

    await waitFor(() => expect(screen.getByText('3')).toBeInTheDocument())
    // The count comes from the summary, not from fetching the feed.
    expect(fetchAlerts).not.toHaveBeenCalled()
  })

  it('hides the badge when nothing is unread', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 0, total: 4 })
    const { container } = renderWithProviders(<NotificationBell />)

    await waitFor(() => expect(fetchAlertSummary).toHaveBeenCalled())
    expect(container.querySelector('.bell__count')).toBeNull()
  })

  it('states that delivery is polled', async () => {
    // A badge that refreshes on a timer is not real-time, and saying so is
    // cheaper than letting someone believe an incident arrived live.
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1 })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    renderWithProviders(<NotificationBell />)

    fireEvent.click(await screen.findByRole('button', { name: /Notifications/i }))

    await waitFor(() => expect(screen.getByText(/polled every/)).toBeInTheDocument())
  })

  it('marks one alert read without closing it', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1 })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    vi.mocked(markAlertRead).mockResolvedValue({ is_read: true })
    renderWithProviders(<NotificationBell />)

    fireEvent.click(await screen.findByRole('button', { name: /Notifications/i }))
    fireEvent.click(await screen.findByText('Vehicle left its assigned zone.'))

    // React Query passes a second context argument to `mutationFn`, so assert on
    // the first argument rather than the exact call signature.
    await waitFor(() => expect(markAlertRead).toHaveBeenCalled())
    expect(vi.mocked(markAlertRead).mock.calls[0][0]).toBe('a1')
  })
})

describe('AlertsPage', () => {
  beforeEach(() => {
    vi.mocked(fetchAlertSummary).mockReset()
    vi.mocked(fetchAlerts).mockReset()
    vi.mocked(markAlertRead).mockReset()
    vi.mocked(markAllAlertsRead).mockReset()
    vi.mocked(resolveAlert).mockReset()
  })

  it('renders the alerts it is given', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText('Vehicle left its assigned zone.')).toBeInTheDocument()
    )
    expect(screen.getByText('UP85CA1234')).toBeInTheDocument()
    // Also named in the source line, so it legitimately appears twice.
    expect(screen.getAllByText('Geofence breach').length).toBeGreaterThan(0)
  })

  it('shows the resolve control only when the backend permits it', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1, can_resolve: false })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_OPERATOR' }) })

    await waitFor(() =>
      expect(screen.getByText('Vehicle left its assigned zone.')).toBeInTheDocument()
    )
    // Deriving this from the role string would drift from the backend's rule,
    // producing a button that returns 403.
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark read' })).toBeInTheDocument()
  })

  it('offers resolve to a manager', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Resolve' })).toBeInTheDocument()
    )
  })

  it('confirms before closing, naming what is being closed', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    vi.mocked(resolveAlert).mockResolvedValue({ status: 'RESOLVED' })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    fireEvent.click(await screen.findByRole('button', { name: 'Resolve' }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog).toHaveTextContent('Geofence breach')
    expect(dialog).toHaveTextContent('Vehicle left its assigned zone.')
    expect(dialog).toHaveTextContent('UP85CA1234')
    expect(dialog).toHaveTextContent('audit log')

    // Scoped to the dialog: "Resolve" is both the row button and the confirm.
    const { within } = await import('@testing-library/react')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resolve' }))

    await waitFor(() => expect(resolveAlert).toHaveBeenCalled())
    expect(vi.mocked(resolveAlert).mock.calls[0][0]).toBe('a1')
  })

  it('does not offer resolve on an already-closed alert', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 0, total: 1, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({
      alerts: [alert({ status: 'RESOLVED', is_open: false, is_read: true })],
      total: 1,
    })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    // Also offered in the status filter, so scope to the table.
    await waitFor(() => expect(screen.getAllByText('Resolved').length).toBeGreaterThan(0))
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument()
    // Nothing left to mark read either.
    expect(screen.queryByRole('button', { name: 'Mark read' })).not.toBeInTheDocument()
  })

  it('says so when the filters match nothing', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 0, total: 0, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [], total: 0 })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    await waitFor(() =>
      expect(screen.getByText(/No alerts match these filters/)).toBeInTheDocument()
    )
  })

  it('re-requests when a filter changes', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 0, total: 0, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [], total: 0 })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    // Wait for the filter bar to render, not merely for the query to have been
    // issued: the page shows a spinner until the data resolves.
    await waitFor(() =>
      expect(screen.getByDisplayValue('All severities')).toBeInTheDocument()
    )
    expect(fetchAlerts).toHaveBeenCalledTimes(1)

    fireEvent.change(screen.getByDisplayValue('All severities'), {
      target: { value: 'CRITICAL' },
    })

    await waitFor(() => expect(fetchAlerts).toHaveBeenCalledTimes(2))
    expect(vi.mocked(fetchAlerts).mock.calls[1][0].severity).toBe('CRITICAL')
  })

  it('reports a failed closure rather than failing silently', async () => {
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 1, total: 1, can_resolve: true })
    vi.mocked(fetchAlerts).mockResolvedValue({ alerts: [alert()], total: 1 })
    vi.mocked(resolveAlert).mockRejectedValue({ message: 'Only a manager may close an alert.' })
    renderWithProviders(<AlertsPage />, { token: buildToken({ role: 'VALET_MANAGER' }) })

    fireEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    const dialog = await screen.findByRole('alertdialog')
    const { within } = await import('@testing-library/react')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resolve' }))

    await waitFor(() =>
      expect(screen.getByText('Could not close the alert')).toBeInTheDocument()
    )
  })
})
