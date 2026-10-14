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

const FILTERS = [
  { value: '', label: 'All' },
  ...Object.values(TICKET_STATUS).map((value) => ({
    value,
    label: TICKET_STATUS_LABELS[value],
  })),
]

export function TicketListPage() {
  const [status, setStatus] = useState('')

  const query = useQuery({
    queryKey: ['tickets', { status }],
    queryFn: () => fetchTickets({ status: status || undefined, page_size: 50 }),
  })

  const tickets = query.data?.items ?? []

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Valet tickets</h1>
          <p className="muted">
            Every ticket issued to you, with its current stage in the custody window.
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
            body="Try a different status, or clear the filter to see every ticket on your account."
          />
        </Panel>
      ) : (
        <Panel subtitle={`${query.data?.pagination?.total_items ?? tickets.length} ticket(s)`}>
          <table className="table">
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
                  <td>
                    <Link className="table__link mono" to={`/tickets/${ticket.id}`}>
                      {ticket.ticket_code}
                    </Link>
                  </td>
                  <td className="ticket__plate">{ticket.vehicle?.plate_number ?? '—'}</td>
                  <td>{formatDateTime(ticket.issued_at)}</td>
                  <td>
                    <StatusBadge status={ticket.status} labels={TICKET_STATUS_LABELS} />
                  </td>
                  <td>{ticket.parking_zone?.code ?? '—'}</td>
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
