/**
 * One valet ticket: the digital ticket card, its custody timeline, and the
 * actions available at this stage.
 *
 * Which actions appear is driven by the ticket's projected status, and every
 * one of them calls the live API. A valet sees the handover controls; a
 * customer sees the ticket and nothing more, because the backend refuses those
 * calls from a customer token anyway.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  cancelTicket,
  completeTicket,
  fetchTicket,
  updateCustodyStatus,
} from '../services/ticketService'
import { normaliseError } from '../services/apiClient'
import { DataRow, ErrorNotice, Field, Panel, Spinner } from '../components/ui'
import { TicketCard } from '../components/tickets/TicketCard'
import { RetrievalEstimateCard } from '../components/retrieval/RetrievalEstimateCard'
import {
  fetchRetrievalPrediction,
  requestRetrievalPrediction,
} from '../services/retrievalService'
import { StatusBadge } from '../components/vehicles/StatusBadge'
import { CUSTODY_STATUS_LABELS } from '../utils/constants'
import { formatDateTime } from '../utils/format'
import { useHasRole } from '../hooks/useAuth'
import { useToast } from '../context/ToastContext'
import { ROLES } from '../utils/constants'

const VALET_STAFF = [ROLES.VALET_OPERATOR, ROLES.VALET_MANAGER, ROLES.ADMIN]

/** The next custody states worth offering, by current state. */
const NEXT_STATES = {
  CHECKED_IN: ['PARKED', 'IN_TRANSIT'],
  PARKED: ['IN_TRANSIT', 'RETRIEVAL_REQUESTED', 'READY_FOR_PICKUP'],
  IN_TRANSIT: ['PARKED', 'RETRIEVAL_IN_PROGRESS', 'READY_FOR_PICKUP'],
  RETRIEVAL_REQUESTED: ['RETRIEVAL_IN_PROGRESS', 'READY_FOR_PICKUP'],
  RETRIEVAL_IN_PROGRESS: ['READY_FOR_PICKUP'],
  READY_FOR_PICKUP: ['RETRIEVAL_IN_PROGRESS'],
  DISPUTED: [],
  CHECKED_OUT: [],
}

export function TicketDetailPage() {
  const { id } = useParams()
  const queryClient = useQueryClient()
  const isStaff = useHasRole(VALET_STAFF)

  const [pickupOdometer, setPickupOdometer] = useState('')
  const [conditionAccepted, setConditionAccepted] = useState(true)
  const [cancelReason, setCancelReason] = useState('')

  const toast = useToast()

  const query = useQuery({ queryKey: ['ticket', id], queryFn: () => fetchTicket(id) })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['ticket', id] })
    queryClient.invalidateQueries({ queryKey: ['tickets'] })
    queryClient.invalidateQueries({ queryKey: ['vehicles'] })
  }

  /**
   * Each of these changes custody state and is written to the audit log, so each
   * one has to say what happened. `refresh` alone updated the table silently:
   * the row changed under the cursor with no confirmation that the write landed,
   * which on a slow connection reads as the button having done nothing.
   */
  const transition = useMutation({
    mutationFn: (status) => updateCustodyStatus(query.data.ticket.custody_session.id, status),
    onSuccess: (_result, status) => {
      toast.success(
        'Status updated',
        `Custody session moved to ${CUSTODY_STATUS_LABELS[status] ?? status}. Recorded in the audit log.`,
      )
      refresh()
    },
    onError: (error) =>
      toast.error('Could not change status', normaliseError(error).message),
  })

  const complete = useMutation({
    mutationFn: () =>
      completeTicket(id, {
        odometer: pickupOdometer,
        conditionAccepted,
      }),
    onSuccess: () => {
      toast.success(
        'Pickup completed',
        'Custody closed and the ticket completed. Recorded in the audit log.',
      )
      refresh()
    },
    onError: (error) =>
      toast.error('Could not complete the pickup', normaliseError(error).message),
  })

  const cancel = useMutation({
    mutationFn: () => cancelTicket(id, cancelReason || undefined),
    onSuccess: () => {
      toast.warning(
        'Ticket cancelled',
        cancelReason ? `Reason recorded: ${cancelReason}` : 'Cancelled with no reason given.',
      )
      refresh()
    },
    onError: (error) =>
      toast.error('Could not cancel the ticket', normaliseError(error).message),
  })

  // The stored estimate for this session. A 404 here means "nobody has asked
  // yet" rather than a failure, so the error is rendered as an empty state with
  // a button - and retries are off, because re-asking a route that has already
  // said "there is nothing" three times is just noise in the network log.
  const estimateQuery = useQuery({
    queryKey: ['retrieval-estimate', id],
    queryFn: () => fetchRetrievalPrediction(query.data?.ticket?.custody_session?.id),
    enabled: Boolean(query.data?.ticket?.custody_session?.id),
    retry: false,
  })

  const estimate = useMutation({
    mutationFn: () =>
      requestRetrievalPrediction(query.data.ticket.custody_session.id),
    onSuccess: (data) => {
      // The card below re-renders with the figure, but the button that produced
      // it is elsewhere on the page; without this the click looks unanswered.
      toast.success(
        'Retrieval estimate ready',
        data?.estimated_minutes != null
          ? `About ${data.estimated_minutes} minutes. Indicative only — baseline model on synthetic data.`
          : 'Shown on the retrieval card below.',
      )
      queryClient.invalidateQueries({ queryKey: ['retrieval-estimate', id] })
    },
    onError: (error) =>
      toast.error('Could not generate an estimate', normaliseError(error).message),
  })

  if (query.isPending) return <Spinner label="Loading ticket…" />
  if (query.isError) {
    return <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
  }

  const ticket = query.data.ticket
  const session = ticket.custody_session
  const open = ticket.status === 'CREATED' || ticket.status === 'IN_CUSTODY' || ticket.status === 'READY_FOR_PICKUP'
  const nextStates = session ? (NEXT_STATES[session.status] ?? []) : []

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Ticket {ticket.ticket_code}</h1>
          <p className="muted">
            Digital valet ticket. The QR code was generated by the server when the ticket was
            issued.
          </p>
        </div>
        <Link className="btn btn--ghost" to="/tickets">
          All tickets
        </Link>
      </header>

      <TicketCard ticket={ticket} />

      {/* The stored status is shown deliberately: it makes the projection
          visible instead of asking a reviewer to take it on trust. */}
      <Panel title="Custody timeline">
        <dl className="data-list">
          <DataRow
            label="Custody status"
            value={
              session ? (
                <StatusBadge status={session.status} labels={CUSTODY_STATUS_LABELS} />
              ) : (
                'Not started'
              )
            }
          />
          <DataRow label="Dropped off" value={formatDateTime(session?.dropped_off_at)} />
          <DataRow label="Zone assigned" value={formatDateTime(session?.zone_assigned_at)} />
          <DataRow label="Retrieval requested" value={formatDateTime(session?.retrieval_requested_at)} />
          <DataRow label="Ready for pickup" value={formatDateTime(session?.ready_for_pickup_at)} />
          <DataRow label="Picked up" value={formatDateTime(session?.picked_up_at)} />
          <DataRow label="Odometer at drop-off" value={session?.odometer_at_drop_off} />
          <DataRow label="Stored ticket status" value={ticket.stored_status} mono />
        </dl>
      </Panel>

      {session && (
        <Panel
          title="Retrieval estimate"
          subtitle="How long until the vehicle reaches the handover point"
        >
          <RetrievalEstimateCard
            estimate={estimateQuery.data ?? null}
            loading={estimateQuery.isFetching && !estimateQuery.data}
            onRequest={() => estimate.mutate()}
            busy={estimate.isPending}
          />
        </Panel>
      )}

      {isStaff && open && session && nextStates.length > 0 && (
        <Panel title="Move the custody session" subtitle="Valet actions">
          <div className="btn-row">
            {nextStates.map((state) => (
              <button
                key={state}
                type="button"
                className="btn"
                disabled={transition.isPending}
                onClick={() => transition.mutate(state)}
              >
                {CUSTODY_STATUS_LABELS[state] ?? state}
              </button>
            ))}
          </div>
          {transition.isError && (
            <ErrorNotice error={transition.error.apiError ?? normaliseError(transition.error)} />
          )}
        </Panel>
      )}

      {isStaff && (ticket.status === 'READY_FOR_PICKUP' || ticket.status === 'IN_CUSTODY') && (
        <Panel title="Complete the ticket" subtitle="Return the vehicle and close custody">
          <div className="grid grid--2">
            <Field label="Odometer at pickup (km)" hint="Optional">
              <input
                inputMode="numeric"
                value={pickupOdometer}
                onChange={(event) => setPickupOdometer(event.target.value)}
              />
            </Field>
            <Field label="Condition accepted by customer">
              <select
                value={conditionAccepted ? 'yes' : 'no'}
                onChange={(event) => setConditionAccepted(event.target.value === 'yes')}
              >
                <option value="yes">Yes</option>
                <option value="no">No - disputed</option>
              </select>
            </Field>
          </div>
          <div className="btn-row">
            <button
              type="button"
              className="btn btn--primary"
              disabled={complete.isPending}
              onClick={() => complete.mutate()}
            >
              {complete.isPending ? 'Completing…' : 'Complete ticket'}
            </button>
          </div>
          {complete.isError && (
            <ErrorNotice error={complete.error.apiError ?? normaliseError(complete.error)} />
          )}
        </Panel>
      )}

      {isStaff && open && (
        <Panel title="Cancel ticket" tone="panel--muted">
          <Field label="Reason" hint="Optional">
            <input
              value={cancelReason}
              onChange={(event) => setCancelReason(event.target.value)}
              placeholder="Customer changed their mind"
            />
          </Field>
          <div className="btn-row">
            <button
              type="button"
              className="btn btn--danger"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {cancel.isPending ? 'Cancelling…' : 'Cancel ticket'}
            </button>
          </div>
          {cancel.isError && (
            <ErrorNotice error={cancel.error.apiError ?? normaliseError(cancel.error)} />
          )}
        </Panel>
      )}
    </div>
  )
}

export default TicketDetailPage
