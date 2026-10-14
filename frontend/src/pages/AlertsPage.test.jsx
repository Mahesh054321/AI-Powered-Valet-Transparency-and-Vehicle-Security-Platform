/**
 * Alert centre tests.
 *
 * The page's two design rules are the ones worth testing, because both are
 * security-relevant rather than cosmetic:
 *
 * 1. **The backend decides what is visible.** There is no client-side role
 *    filter, so these tests assert the page renders whatever the scoped feed
 *    returned and never invents or hides a row.
 * 2. **Actions come from `can_resolve`, not from the role string.** A button
 *    that returns 403 is worse than no button, so the Resolve control is
 *    asserted to appear exactly when the summary says the actor may close
 *    alerts — and to be absent otherwise, even for a manager-looking token.
 *
 * Marking read and resolving are deliberately tested as separate controls: one
 * is a personal acknowledgement, the other is an attributed, audited decision.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AlertsPage } from './AlertsPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/alertService', async (original) => {
  const actual = await original()
  return {
    ...actual,
    fetchAlerts: vi.fn(),
    fetchAlertSummary: vi.fn(),
    markAlertRead: vi.fn(),
    markAllAlertsRead: vi.fn(),
    resolveAlert: vi.fn(),
  }
})

import {
  fetchAlertSummary,
  fetchAlerts,
  markAlertRead,
  markAllAlertsRead,
  resolveAlert,
} from '../services/alertService'

const OPEN_ALERT = {
  alert_id: 'a-1',
  type: 'GEOFENCE_BREACH',
  severity: 'HIGH',
  status: 'OPEN',
  message: 'Vehicle is moving away from the yard',
  is_open: true,
  is_read: false,
  created_at: new Date().toISOString(),
  vehicle: { plate_number: 'UP85CA1234' },
  source: { type: 'GEOFENCE_BREACH' },
}

const READ_RESOLVED_ALERT = {
  alert_id: 'a-2',
  type: 'POTENTIAL_DAMAGE',
  severity: 'MEDIUM',
  status: 'RESOLVED',
  message: 'New damage detected at pickup',
  is_open: false,
  is_read: true,
  created_at: new Date(Date.now() - 3_600_000).toISOString(),
  vehicle: { plate_number: 'UP85CD5678' },
}

function stubFeed(alerts = [OPEN_ALERT, READ_RESOLVED_ALERT], { canResolve = true, unread = 1 } = {}) {
  vi.mocked(fetchAlerts).mockResolvedValue({ alerts, total: alerts.length })
  vi.mocked(fetchAlertSummary).mockResolvedValue({
    unread,
    open: alerts.filter((a) => a.is_open).length,
    can_resolve: canResolve,
  })
}

function renderPage(role = 'VALET_MANAGER') {
  clearSession()
  return renderWithProviders(<AlertsPage />, {
    route: '/alerts',
    token: buildToken({ role, email: `${role.toLowerCase()}@valet.local` }),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AlertsPage: display', () => {
  it('renders every alert the scoped feed returned', async () => {
    stubFeed()
    renderPage()

    expect(await screen.findByText(/vehicle is moving away from the yard/i)).toBeInTheDocument()
    expect(await screen.findByText(/new damage detected at pickup/i)).toBeInTheDocument()
    // Both plates come from the server payload, not from any local lookup.
    expect(screen.getByText('UP85CA1234')).toBeInTheDocument()
    expect(screen.getByText('UP85CD5678')).toBeInTheDocument()
  })

  it('reports the visible and unread counts from the server', async () => {
    stubFeed([OPEN_ALERT], { unread: 3 })
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    expect(screen.getByText(/1 alert\(s\) visible to you/i)).toBeInTheDocument()
    expect(screen.getByText(/3 unread/i)).toBeInTheDocument()
  })

  it('labels delivery as polled rather than pushed', async () => {
    // A standing honesty requirement: the UI must not imply real-time push.
    stubFeed()
    renderPage()

    expect(await screen.findByText(/polled, not pushed/i)).toBeInTheDocument()
  })

  it('shows an unread marker only on unread rows', async () => {
    stubFeed()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row')
    // Row 0 is the header.
    expect(within(rows[1]).getByLabelText('Unread')).toBeInTheDocument()
    expect(within(rows[2]).queryByLabelText('Unread')).toBeNull()
  })

  it('shows an explanatory empty state when nothing matches', async () => {
    stubFeed([], { unread: 0 })
    renderPage()

    expect(await screen.findByText(/no alerts match these filters/i)).toBeInTheDocument()
    expect(screen.getByText(/0 alert\(s\) visible to you/i)).toBeInTheDocument()
  })

  it('shows a loading state before the feed arrives', () => {
    vi.mocked(fetchAlerts).mockReturnValue(new Promise(() => {}))
    vi.mocked(fetchAlertSummary).mockReturnValue(new Promise(() => {}))
    renderPage()

    expect(screen.getByText(/loading alerts/i)).toBeInTheDocument()
  })

  it('shows a retryable error state when the feed fails', async () => {
    vi.mocked(fetchAlerts).mockRejectedValue(new Error('network down'))
    vi.mocked(fetchAlertSummary).mockResolvedValue({ unread: 0, open: 0, can_resolve: false })
    renderPage()

    // The query client retries once before surfacing an error, so this needs a
    // longer window than the default rather than a shorter assertion.
    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})

describe('AlertsPage: filters', () => {
  it('re-queries with the chosen type filter', async () => {
    stubFeed()
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.selectOptions(screen.getByLabelText(/^type$/i), 'GEOFENCE_BREACH')

    await waitFor(() => {
      const lastCall = vi.mocked(fetchAlerts).mock.calls.at(-1)[0]
      expect(lastCall.type).toBe('GEOFENCE_BREACH')
    })
  })

  it('re-queries with the unread-only flag', async () => {
    stubFeed()
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByLabelText(/unread only/i))

    await waitFor(() => {
      const lastCall = vi.mocked(fetchAlerts).mock.calls.at(-1)[0]
      expect(lastCall.unreadOnly).toBe(true)
    })
  })

  it('caps the page size so a large feed cannot swamp the table', async () => {
    stubFeed()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    const lastCall = vi.mocked(fetchAlerts).mock.calls.at(-1)[0]
    expect(lastCall.pageSize).toBe(50)
  })
})

describe('AlertsPage: marking read', () => {
  it('offers "Mark read" only on unread rows', async () => {
    stubFeed()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row')

    expect(within(rows[1]).getByRole('button', { name: /mark read/i })).toBeInTheDocument()
    expect(within(rows[2]).queryByRole('button', { name: /mark read/i })).toBeNull()
  })

  it('marks one alert read and refreshes the feed', async () => {
    stubFeed()
    vi.mocked(markAlertRead).mockResolvedValue({ ok: true })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /mark read/i }))

    // The mutation function is called with the id plus React Query's own
    // context argument, so assert on the id rather than the exact arg list.
    await waitFor(() => expect(markAlertRead).toHaveBeenCalled())
    expect(vi.mocked(markAlertRead).mock.calls[0][0]).toBe('a-1')
    // Invalidation refetches both the feed and the badge count.
    await waitFor(() => expect(vi.mocked(fetchAlerts).mock.calls.length).toBeGreaterThan(1))
  })

  it('reports a failure to mark read, because a silent failure looks like a dead click', async () => {
    stubFeed()
    vi.mocked(markAlertRead).mockRejectedValue(new Error('nope'))
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /mark read/i }))

    expect(
      await screen.findByText(/could not mark that alert as read/i),
    ).toBeInTheDocument()
  })

  it('disables "Mark all read" when nothing is unread', async () => {
    stubFeed([READ_RESOLVED_ALERT], { unread: 0 })
    renderPage()

    const button = await screen.findByRole('button', { name: /mark all read/i })
    expect(button).toBeDisabled()
  })

  it('marks everything read when there is something to clear', async () => {
    stubFeed([OPEN_ALERT], { unread: 2 })
    vi.mocked(markAllAlertsRead).mockResolvedValue({ updated: 2 })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /mark all read/i }))

    await waitFor(() => expect(markAllAlertsRead).toHaveBeenCalledTimes(1))
  })
})

describe('AlertsPage: resolve authority', () => {
  it('shows Resolve when the backend says this actor may close alerts', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    expect(screen.getByRole('button', { name: /^resolve$/i })).toBeInTheDocument()
  })

  it('hides Resolve when the backend withholds the capability', async () => {
    // The token still says VALET_MANAGER; the capability flag is what counts.
    stubFeed([OPEN_ALERT], { canResolve: false })
    renderPage('VALET_MANAGER')

    await screen.findByText(/vehicle is moving away from the yard/i)
    expect(screen.queryByRole('button', { name: /^resolve$/i })).toBeNull()
  })

  it('does not offer to resolve an alert that is already closed', async () => {
    stubFeed([READ_RESOLVED_ALERT], { canResolve: true })
    renderPage()

    await screen.findByText(/new damage detected at pickup/i)
    expect(screen.queryByRole('button', { name: /^resolve$/i })).toBeNull()
  })

  it('asks for confirmation and names what is being closed', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))

    const dialog = await screen.findByRole('alertdialog', { name: /close this alert\?/i })
    // The dialog identifies the incident rather than asking a bare "are you sure?".
    // Both are scoped: the plate also appears in the table row behind it.
    expect(within(dialog).getByText(/UP85CA1234/)).toBeInTheDocument()
    expect(within(dialog).getByText(/geofence breach/i)).toBeInTheDocument()
  })

  it('sends the resolution note and reports the outcome', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    vi.mocked(resolveAlert).mockResolvedValue({ status: 'RESOLVED' })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.type(
      within(dialog).getByLabelText(/resolution note/i),
      'Customer had authorised the move.',
    )
    // The row button and the dialog confirm button share a label; scope to the dialog.
    await user.click(within(dialog).getByRole('button', { name: /^resolve$/i }))

    await waitFor(() => expect(resolveAlert).toHaveBeenCalled())
    expect(vi.mocked(resolveAlert).mock.calls[0][0]).toBe('a-1')
    expect(vi.mocked(resolveAlert).mock.calls[0][1]).toEqual({
      notes: 'Customer had authorised the move.',
      dismiss: false,
    })
    expect(await screen.findByText(/alert resolved/i)).toBeInTheDocument()
  })

  it('always sends dismiss=false, because this page only resolves', async () => {
    /**
     * The service supports dismissal and the page copies a dismissed status back
     * if the server returns one, but there is deliberately no dismiss control
     * here: "we dealt with it" and "we judged this needed no action" are
     * different claims, and offering the weaker one on a security incident
     * invites the wrong one. This pins that the page never sends it.
     */
    stubFeed([OPEN_ALERT], { canResolve: true })
    vi.mocked(resolveAlert).mockResolvedValue({ status: 'RESOLVED' })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).queryByRole('button', { name: /dismiss/i })).toBeNull()

    await user.click(within(dialog).getByRole('button', { name: /^resolve$/i }))

    await waitFor(() => expect(resolveAlert).toHaveBeenCalled())
    expect(vi.mocked(resolveAlert).mock.calls[0][1]).toMatchObject({ dismiss: false })
  })

  it('records the attribution warning inside the confirmation dialog', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))
    const dialog = await screen.findByRole('alertdialog')

    // Closing an alert is attributed, so the dialog says so before you commit.
    expect(within(dialog).getByText(/records your name in the audit log/i)).toBeInTheDocument()
  })

  it('reports a refusal rather than pretending the alert closed', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    vi.mocked(resolveAlert).mockRejectedValue(new Error('Manager role required.'))
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^resolve$/i }))

    expect(await screen.findByText(/could not close the alert/i)).toBeInTheDocument()
    expect(await screen.findByText(/manager role required/i)).toBeInTheDocument()
  })

  it('cancelling the dialog closes it without calling the API', async () => {
    stubFeed([OPEN_ALERT], { canResolve: true })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/vehicle is moving away from the yard/i)
    await user.click(screen.getByRole('button', { name: /^resolve$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(resolveAlert).not.toHaveBeenCalled()
  })

  it('explains that read and resolved are different acts', async () => {
    stubFeed()
    renderPage()

    // The footnote is the documentation a reviewer reads in the product itself.
    expect(
      await screen.findByText(/written to the audit log/i),
    ).toBeInTheDocument()
  })
})
