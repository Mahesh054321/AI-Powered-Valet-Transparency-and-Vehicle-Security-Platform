/**
 * Valet tickets.
 *
 * The status filter uses the projected vocabulary the API reports
 * (CREATED / IN_CUSTODY / READY_FOR_PICKUP / COMPLETED / CANCELLED), which is
 * what a customer understands. The stored values are an implementation detail
 * of the backend and are deliberately not offered here.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchTickets } from '../services/ticketService'
import { EmptyState, ErrorNotice, Panel, Spinner } from '../components/ui'
import { StatusBadge } from '../components/vehicles/StatusBadge'
import { TICKET_STATUS, TICKET_STATUS_LABELS } from '../utils/constants'
import { formatDateTime } from '../utils/format'

const ACTIVE_FILTER = 'ACTIVE'
const FILTERS = [
  { value: ACTIVE_FILTER, label: 'Active' },
  { value: '', label: 'All tickets' },
  ...Object.values(TICKET_STATUS).map((value) => ({
    value,
    label: TICKET_STATUS_LABELS[value],
  })),
]

export function TicketListPage() {
  const [status, setStatus] = useState(ACTIVE_FILTER)

  const query = useQuery({
    queryKey: ['tickets', { status }],
    queryFn: () =>
      fetchTickets({
        status: status && status !== ACTIVE_FILTER ? status : undefined,
        active_only: status === ACTIVE_FILTER ? true : undefined,
        page_size: 50,
      }),
  })

  const tickets = query.data?.items ?? []

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Valet tickets</h1>
          <p className="muted">
            Active tickets are shown by default. Choose a status or all tickets to review history.
          </p>
        </div>
      </header>

      <Panel>
        <div className="btn-row">
          {FILTERS.map((filter) => (
            <button
              key={filter.value || 'all'}
              type="button"
              className={`btn ${status === filter.value ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => setStatus(filter.value)}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </Panel>

      {query.isPending ? (
        <Spinner label="Loading tickets…" />
      ) : query.isError ? (
        <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
      ) : tickets.length === 0 ? (
        <Panel tone="panel--muted">
          <EmptyState
            icon="🎫"
            title="No tickets match this filter"
            body="Try another status, or choose All tickets to review your ticket history."
          />
        </Panel>
      ) : (
        <Panel subtitle={`${query.data?.pagination?.total_items ?? tickets.length} ticket(s)`}>
          <table className="table ticket-list__table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Vehicle</th>
                <th>Issued</th>
                <th>Status</th>
                <th>Zone</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((ticket) => (
                <tr key={ticket.id}>
                  <td data-label="Ticket">
                    <Link className="table__link mono" to={`/tickets/${ticket.id}`}>
                      {ticket.ticket_code}
                    </Link>
                  </td>
                  <td data-label="Vehicle" className="ticket__plate">{ticket.vehicle?.plate_number ?? '—'}</td>
                  <td data-label="Issued">{formatDateTime(ticket.issued_at)}</td>
                  <td data-label="Status">
                    <StatusBadge status={ticket.status} labels={TICKET_STATUS_LABELS} />
                  </td>
                  <td data-label="Zone">{ticket.parking_zone?.code ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  )
}

export default TicketListPage
