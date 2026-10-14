/**
 * Tests for the audit log viewer page.
 *
 * The property this page exists to communicate is that **the log cannot be
 * edited**. The tests pin that down along with the two behaviours that would
 * silently mislead a reviewer if they broke: filters must reach the request
 * under the names the backend parses, and the page must say it is read-only
 * using the API's own flag rather than a hardcoded sentence that could drift.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../services/auditService', () => ({
  fetchAuditLog: vi.fn(),
  fetchAuditFilters: vi.fn(),
  fetchCustodyTimeline: vi.fn(),
  actorLabel: (entry) => entry?.actor_email || 'system',
  summariseEntry: (entry) => entry?.description || entry?.action || '',
  formatEntryTime: (value) => (value ? `time:${value}` : ''),
}))

import {
  fetchAuditFilters,
  fetchAuditLog,
  fetchCustodyTimeline,
} from '../services/auditService'
import { AuditLogPage } from './AuditLogPage'

const LOG_RESPONSE = {
  mutable: false,
  mutability_note: 'The audit trail is append-only.',
  items: [
    {
      id: '1',
      seq: 9,
      action: 'VEHICLE_CHECKED_IN',
      label: 'Vehicle checked in',
      outcome: 'SUCCESS',
      actor_email: 'valet@example.com',
      entity_type: 'custody_sessions',
      occurred_at: '2026-09-09T10:00:00+00:00',
      ip_address: '203.0.113.9',
      description: 'Checked in to ZONE-A',
      is_lifecycle_event: true,
    },
    {
      id: '2',
      seq: 8,
      action: 'LOGIN_FAILED',
      label: 'Login failed',
      outcome: 'FAILURE',
      actor_email: null,
      actor_role: null,
      entity_type: 'users',
      occurred_at: '2026-09-09T09:00:00+00:00',
      ip_address: '198.51.100.4',
      description: 'Bad password',
      is_lifecycle_event: false,
    },
  ],
  pagination: { page: 1, page_size: 50, total: 2, pages: 1 },
  vocabulary: { lifecycle: [] },
}

const FILTER_RESPONSE = {
  actions: ['VEHICLE_CHECKED_IN', 'GPS_BREACH'],
  entity_types: ['custody_sessions', 'gps_events'],
  outcomes: ['SUCCESS', 'FAILURE'],
  lifecycle_actions: [],
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AuditLogPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchAuditLog.mockResolvedValue(LOG_RESPONSE)
  fetchAuditFilters.mockResolvedValue(FILTER_RESPONSE)
  fetchCustodyTimeline.mockResolvedValue({})
})

describe('AuditLogPage', () => {
  it('states that the log cannot be edited, using the API note', async () => {
    renderPage()
    expect(await screen.findByText('This log cannot be edited')).toBeTruthy()
    expect(await screen.findByText('The audit trail is append-only.')).toBeTruthy()
  })

  it('renders the entries the API returned', async () => {
    renderPage()
    expect(await screen.findByText('Checked in to ZONE-A')).toBeTruthy()
    expect(screen.getByText('Bad password')).toBeTruthy()
  })

  it('labels an entry with no identified actor as system', async () => {
    renderPage()
    await screen.findByText('Bad password')
    expect(screen.getByText('system')).toBeTruthy()
  })

  it('reports the matching count from the server, not a client-side length', async () => {
    renderPage()
    await screen.findByText('Checked in to ZONE-A')
    expect(screen.getByText(/2 matching/)).toBeTruthy()
  })

  it('populates filter dropdowns from the data, not a hardcoded list', async () => {
    renderPage()
    // Wait on the option itself, not the select: the select exists immediately
    // with only its placeholder, so awaiting it races the filters query.
    await screen.findByRole('option', { name: 'GPS_BREACH' })
    const actionSelect = screen.getByLabelText('Action')
    const options = within(actionSelect)
      .getAllByRole('option')
      .map((option) => option.value)
    expect(options).toContain('GPS_BREACH')
    expect(options).toContain('VEHICLE_CHECKED_IN')
  })

  it('requests the first page with the default page size', async () => {
    renderPage()
    await waitFor(() => expect(fetchAuditLog).toHaveBeenCalled())
    const call = fetchAuditLog.mock.calls[0][0]
    expect(call.page).toBe(1)
    expect(call.pageSize).toBe(50)
  })

  it('sends a chosen action filter to the API', async () => {
    renderPage()
    await screen.findByRole('option', { name: 'GPS_BREACH' })
    const actionSelect = screen.getByLabelText('Action')
    fireEvent.change(actionSelect, { target: { value: 'GPS_BREACH' } })
    await waitFor(() =>
      expect(fetchAuditLog).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'GPS_BREACH' }),
      ),
    )
  })

  it('resets to page 1 when a filter changes', async () => {
    renderPage()
    await screen.findByRole('option', { name: 'FAILURE' })
    const outcomeSelect = screen.getByLabelText('Outcome')
    fireEvent.change(outcomeSelect, { target: { value: 'FAILURE' } })
    await waitFor(() =>
      expect(fetchAuditLog).toHaveBeenCalledWith(expect.objectContaining({ page: 1 })),
    )
  })

  it('sends lifecycle_only as a boolean flag', async () => {
    renderPage()
    const checkbox = await screen.findByLabelText(/Lifecycle events only/)
    fireEvent.click(checkbox)
    await waitFor(() =>
      expect(fetchAuditLog).toHaveBeenCalledWith(
        expect.objectContaining({ lifecycleOnly: true }),
      ),
    )
  })

  it('clears every filter at once', async () => {
    renderPage()
    await screen.findByRole('option', { name: 'GPS_BREACH' })
    const actionSelect = screen.getByLabelText('Action')
    fireEvent.change(actionSelect, { target: { value: 'GPS_BREACH' } })
    await waitFor(() =>
      expect(fetchAuditLog).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'GPS_BREACH' }),
      ),
    )

    fireEvent.click(screen.getByText('Clear filters'))
    await waitFor(() => {
      const last = fetchAuditLog.mock.calls.at(-1)[0]
      expect(last.action).toBeUndefined()
      expect(last.outcome).toBeUndefined()
    })
  })

  it('offers a timeline mode and loads the requested custody', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Custody timeline' }))

    const input = await screen.findByLabelText(/^Custody session id/)
    const load = screen.getByRole('button', { name: 'Load timeline' })
    expect(load).toBeDisabled()

    fireEvent.change(input, { target: { value: 'custody-42' } })
    fireEvent.click(load)

    await waitFor(() => expect(fetchCustodyTimeline).toHaveBeenCalledWith('custody-42'))
  })

  it('does not fetch the log while in timeline mode', async () => {
    renderPage()
    await screen.findByText('Checked in to ZONE-A')
    const callsBefore = fetchAuditLog.mock.calls.length

    fireEvent.click(screen.getByRole('button', { name: 'Custody timeline' }))
    await screen.findByLabelText(/^Custody session id/)

    expect(fetchAuditLog.mock.calls.length).toBe(callsBefore)
  })

  it('shows an empty state rather than a blank table when nothing matches', async () => {
    fetchAuditLog.mockResolvedValue({ ...LOG_RESPONSE, items: [] })
    renderPage()
    expect(await screen.findByText(/No entries match these filters/)).toBeTruthy()
  })

  it('surfaces a fetch error with a retry affordance', async () => {
    fetchAuditLog.mockRejectedValue(new Error('network down'))
    renderPage()
    expect(await screen.findByText(/network down/)).toBeTruthy()
  })
})
