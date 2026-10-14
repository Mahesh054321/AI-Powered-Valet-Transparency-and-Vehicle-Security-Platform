/**
 * The valet ticket list.
 *
 * The point of this page is the status filter, and the thing worth pinning is
 * *which vocabulary* it offers:
 *
 * - **The filter uses the projected statuses** (`CREATED`, `IN_CUSTODY`,
 *   `READY_FOR_PICKUP`, ...), which is what a customer understands. The stored
 *   values (`ISSUED`, `ACTIVE`, `EXPIRED`) are an implementation detail of the
 *   backend, and offering them here would ask the user to filter by a
 *   vocabulary they have never been shown. This is the "keep_map" decision made
 *   visible.
 * - **Changing the filter refetches; "All" sends no status at all**, rather than
 *   an empty string the backend would have to interpret.
 * - **The row links to the ticket**, and absent vehicle or zone data renders an
 *   em dash rather than "undefined".
 * - **The ticket count is visible.** It is passed as a `Panel` subtitle with no
 *   title, which used to be silently dropped by the panel header's render
 *   condition.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TicketListPage } from './TicketListPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/ticketService', async (original) => {
  const actual = await original()
  return { ...actual, fetchTickets: vi.fn() }
})

import { fetchTickets } from '../services/ticketService'

function ticket(overrides = {}) {
  return {
    id: 'ticket-1',
    ticket_code: 'TKT-0001',
    status: 'IN_CUSTODY',
    issued_at: '2026-09-11T09:00:00Z',
    vehicle: { plate_number: 'UP 78 AB 1234' },
    parking_zone: { code: 'ZONE-A' },
    ...overrides,
  }
}

function stubList(items, totalItems = items.length) {
  vi.mocked(fetchTickets).mockResolvedValue({
    items,
    pagination: { total_items: totalItems, page: 1, page_size: 50 },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  stubList([ticket()])
})

function renderPage(role = 'CUSTOMER') {
  clearSession()
  return renderWithProviders(<TicketListPage />, {
    route: '/tickets',
    token: buildToken({ role }),
  })
}

describe('TicketListPage', () => {
  it('renders the ticket row with its vehicle, date and zone', async () => {
    renderPage()

    const link = await screen.findByRole('link', { name: 'TKT-0001' })
    expect(link).toHaveAttribute('href', '/tickets/ticket-1')
    const row = link.closest('tr')
    expect(within(row).getByText('UP 78 AB 1234')).toBeInTheDocument()
    expect(within(row).getByText('ZONE-A')).toBeInTheDocument()
  })

  it('offers the projected statuses and never the stored ones', async () => {
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    // The vocabulary a customer recognises.
    expect(screen.getByRole('button', { name: /^in custody$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^ready for pickup$/i })).toBeInTheDocument()
    // The stored vocabulary must not leak into the filter.
    expect(screen.queryByRole('button', { name: /^issued$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^active$/i })).toBeNull()
  })

  it('shows the labelled status in the row, not the raw enum', async () => {
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    const row = screen.getByRole('link', { name: 'TKT-0001' }).closest('tr')
    expect(within(row).getByText('In custody')).toBeInTheDocument()
  })

  it('shows the ticket count from the pagination envelope', async () => {
    vi.clearAllMocks()
    stubList([ticket()], 23)
    renderPage()

    // Passed as a Panel subtitle with no title; the header must still render.
    await screen.findByRole('link', { name: 'TKT-0001' })
    expect(screen.getByText('23 ticket(s)')).toBeInTheDocument()
  })

  it('refetches with the chosen status when a filter is clicked', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    expect(fetchTickets).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchTickets).mock.calls[0][0]).toMatchObject({ status: undefined })

    await user.click(screen.getByRole('button', { name: /^ready for pickup$/i }))

    await waitFor(() => expect(fetchTickets).toHaveBeenCalledTimes(2))
    expect(vi.mocked(fetchTickets).mock.calls[1][0]).toMatchObject({
      status: 'READY_FOR_PICKUP',
    })
  })

  it('returns to the unfiltered list without a wasted round trip', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    // The unfiltered request carries no status at all - `undefined`, not an
    // empty string the backend would have to interpret.
    expect(vi.mocked(fetchTickets).mock.calls[0][0]).toMatchObject({ status: undefined })

    await user.click(screen.getByRole('button', { name: /^ready for pickup$/i }))
    await waitFor(() => expect(fetchTickets).toHaveBeenCalledTimes(2))

    await user.click(screen.getByRole('button', { name: /^all$/i }))

    // Back to ['tickets', { status: '' }], which is already cached and still
    // fresh, so React Query serves it rather than refetching identical
    // parameters. The rows are the point, not the request count.
    await waitFor(() => expect(fetchTickets).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('link', { name: 'TKT-0001' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^all$/i })).toHaveClass('btn--primary')
  })

  it('marks the active filter so the current selection is visible', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'TKT-0001' })
    expect(screen.getByRole('button', { name: /^all$/i })).toHaveClass('btn--primary')

    await user.click(screen.getByRole('button', { name: /^completed$/i }))
    expect(screen.getByRole('button', { name: /^completed$/i })).toHaveClass('btn--primary')
    expect(screen.getByRole('button', { name: /^all$/i })).toHaveClass('btn--ghost')
  })

  it('renders an em dash for a ticket with no vehicle or zone', async () => {
    vi.clearAllMocks()
    stubList([ticket({ vehicle: null, parking_zone: null })])
    renderPage()

    const row = (await screen.findByRole('link', { name: 'TKT-0001' })).closest('tr')
    expect(within(row).getAllByText('—')).toHaveLength(2)
    expect(within(row).queryByText(/undefined/)).toBeNull()
  })

  it('explains an empty result as a filter problem rather than an empty account', async () => {
    vi.clearAllMocks()
    stubList([])
    renderPage()

    expect(await screen.findByText(/no tickets match this filter/i)).toBeInTheDocument()
    expect(
      screen.getByText(/try a different status, or clear the filter/i),
    ).toBeInTheDocument()
  })

  it('shows a retryable error when the list fails to load', async () => {
    vi.clearAllMocks()
    const failure = new Error('network down')
    failure.response = {
      status: 503,
      data: { error: { code: 'service_unavailable', message: 'Backend offline' } },
      headers: {},
    }
    vi.mocked(fetchTickets).mockRejectedValue(failure)
    renderPage()

    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})
