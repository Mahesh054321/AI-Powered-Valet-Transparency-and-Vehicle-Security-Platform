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
  upgradeTicketQr,
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
import { requestVehicle } from '../services/dashboardService'
import { fetchConditionReport } from '../services/inspectionService'
import { getTicketPickupState } from '../utils/ticketPickupState'

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
  const isCustomer = useHasRole([ROLES.CUSTOMER])

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

  const upgradeQr = useMutation({
    mutationFn: () => upgradeTicketQr(id),
    onSuccess: () => {
      toast.success('QR upgraded', 'Scan the new QR for ticket details. Replace old printed codes.')
      refresh()
    },
    onError: (error) => toast.error('Could not upgrade QR', normaliseError(error).message),
  })

  const pickup = useMutation({
    mutationFn: (custodyId) => requestVehicle(custodyId),
    onSuccess: (result) => {
      toast.success('Pickup requested', result?.valet_notification?.alert_created
        ? 'Valet staff have an in-app pickup alert (polled every 15 seconds).'
        : 'Request is in the valet queue; separate alert could not be confirmed.')
      refresh()
      queryClient.invalidateQueries({ queryKey: ['customer-dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['retrieval-estimate'] })
    },
    onError: (error) => toast.error('Pickup request failed', normaliseError(error).message),
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
  const activeCustodySessionId = query.data?.ticket?.custody_session?.id
  const activeCustodyStatus = query.data?.ticket?.custody_session?.status
  const activeTicketStatus = query.data?.ticket?.status
  const isTicketClosed = activeCustodyStatus === 'CHECKED_OUT' ||
    activeTicketStatus === 'COMPLETED' ||
    activeTicketStatus === 'CANCELLED' ||
    Boolean(query.data?.ticket?.actual_pickup_at) ||
    Boolean(query.data?.ticket?.custody_session?.picked_up_at)
  const showRetrievalEstimate = Boolean(activeCustodySessionId) && !isTicketClosed

  // The same owner/role protected report used by the condition page, now
  // previewed right on the QR destination. No comparison is inferred when
  // either image set is missing. An inaccessible report must not block ticket
  // visibility or the pickup action.
  const conditionQuery = useQuery({
    queryKey: ['condition-report', activeCustodySessionId],
    queryFn: () => fetchConditionReport(activeCustodySessionId),
    enabled: Boolean(activeCustodySessionId),
    retry: false,
  })

  const estimateQuery = useQuery({
    queryKey: ['retrieval-estimate', activeCustodySessionId],
    queryFn: () => fetchRetrievalPrediction(activeCustodySessionId),
    enabled: showRetrievalEstimate,
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
      queryClient.invalidateQueries({ queryKey: ['retrieval-estimate', query.data.ticket.custody_session.id] })
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
  const pickupState = getTicketPickupState(ticket)
  // Suppress the button immediately after a successful request, even before
  // the server's updated custody projection has been fetched again.
  const canRequestPickup = isCustomer && pickupState.canRequest &&
    !(pickup.isSuccess && pickup.variables === session?.id)
  const condition = conditionQuery.data
  const findings = condition?.pickup?.detections ?? []
  const comparisonReady = Boolean(condition?.comparison_ready)
  const conditionPreview = findings.slice(0, 3)

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

      <Panel title="Vehicle details" subtitle="QR ticket overview · visible only to the authenticated owner or authorised staff">
        <dl className="data-list">
          <DataRow label="Registration number" value={ticket.vehicle?.plate_number ?? '—'} mono />
          <DataRow label="Make / model" value={[ticket.vehicle?.make, ticket.vehicle?.model].filter(Boolean).join(' · ') || '—'} />
          <DataRow label="Year / colour" value={[ticket.vehicle?.year, ticket.vehicle?.color].filter(Boolean).join(' · ') || '—'} />
          <DataRow label="Custody status" value={session?.status ? CUSTODY_STATUS_LABELS[session.status] ?? session.status : 'Not checked in'} />
          <DataRow label="Pickup estimate" value={isTicketClosed ? (activeTicketStatus === 'CANCELLED' ? 'Not applicable — ticket cancelled' : 'Not applicable — custody completed') : estimateQuery.data?.estimated_minutes != null ? `Approx. ${estimateQuery.data.estimated_minutes} minutes · synthetic-data prototype` : 'No current estimate available'} />
        </dl>
        {ticket.qr_payload && !/^https?:\/\//i.test(ticket.qr_payload) && (
          <div className="btn-row" style={{ marginTop: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="muted">This older QR only contains a ticket number.</span>
            <button type="button" className="btn btn--primary" disabled={upgradeQr.isPending}
              onClick={() => upgradeQr.mutate()}>{upgradeQr.isPending ? 'Upgrading…' : 'Upgrade QR for phone scanning'}</button>
          </div>
        )}
      </Panel>

      {session && (
        <Panel title="Damage detection & vehicle condition" subtitle="Before/after photos and verified or pending findings for this custody session">
          {conditionQuery.isPending && <p className="muted" role="status">Checking captured vehicle condition…</p>}
          {conditionQuery.isError && (
            <p className="muted" role="status">Condition details are temporarily unavailable. You can still open the full report.</p>
          )}
          {condition && (
            <div className="qr-condition">
              <div className="qr-condition__summary" aria-label="Damage assessment summary">
                <div className="qr-condition__stat">
                  <span className="qr-condition__stat-value">{comparisonReady ? findings.length : '—'}</span>
                  <span className="qr-condition__stat-label">Potential findings</span>
                </div>
                <div className="qr-condition__stat">
                  <span className="qr-condition__stat-value">{comparisonReady ? condition.confirmed_count ?? 0 : '—'}</span>
                  <span className="qr-condition__stat-label">Manager confirmed</span>
                </div>
                <div className="qr-condition__stat">
                  <span className="qr-condition__stat-value">{comparisonReady ? condition.unverified_count ?? 0 : '—'}</span>
                  <span className="qr-condition__stat-label">Awaiting review</span>
                </div>
              </div>
              {!condition.drop_off && <p className="muted">Drop-off images have not been captured yet.</p>}
              {condition.drop_off && !condition.pickup && (
                <p className="muted">Drop-off photos are recorded. Damage comparison will be available after pickup photos are captured.</p>
              )}
              {comparisonReady && findings.length === 0 && (
                <p className="muted">No candidate changes were detected in the available photo comparison. This does not guarantee that the vehicle is undamaged.</p>
              )}
              {comparisonReady && findings.length > 0 && (
                <p className="muted">Potential image changes detected. These are not confirmed damage until reviewed by a manager.</p>
              )}
              {(condition.drop_off?.photos?.[0]?.url || condition.pickup?.photos?.[0]?.url) && (
                <div className="qr-condition__photos">
                  {condition.drop_off?.photos?.[0]?.url && (
                    <figure>
                      <img src={condition.drop_off.photos[0].url} alt="Vehicle at drop-off" loading="lazy" />
                      <figcaption>Before · Drop-off</figcaption>
                    </figure>
                  )}
                  {condition.pickup?.photos?.[0]?.url && (
                    <figure>
                      <img src={condition.pickup.photos[0].url} alt="Vehicle at pickup" loading="lazy" />
                      <figcaption>After · Pickup</figcaption>
                    </figure>
                  )}
                </div>
              )}
              {conditionPreview.length > 0 && (
                <div className="qr-condition__findings" aria-label="Damage detection preview">
                  {conditionPreview.map((finding) => (
                    <div key={finding.id} className="qr-condition__finding">
                      <strong>{(finding.damage_type ?? 'POTENTIAL CHANGE').replaceAll('_', ' ')} · {(finding.region ?? 'Unspecified area').replaceAll('_', ' ')}</strong>
                      <span>{finding.verification_status === 'CONFIRMED' ? 'Confirmed by manager' : finding.verification_status === 'REJECTED' ? 'Rejected after review' : 'Potential damage — awaiting review'}</span>
                      {finding.detection_reasoning && <p>{finding.detection_reasoning}</p>}
                      {finding.confidence != null && <small>Image-change score: {Math.round(Number(finding.confidence) * 100)}% (heuristic, not validated accuracy)</small>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <Link className="btn btn--primary qr-condition__link" to={`/inspections/${session.id}`}>
            View Full Before/After Damage Report
          </Link>
        </Panel>
      )}

      {isCustomer && (
        <Panel
          title={pickupState.code === 'completed' ? 'Custody completed' :
            pickupState.code === 'cancelled' ? 'Ticket cancelled' :
              pickupState.code === 'in-progress' ? 'Pickup already requested' :
                pickupState.canRequest ? 'Need Vehicle?' : 'Pickup not available'}
          subtitle={pickupState.canRequest ? 'Request pickup from the current active custody session' : 'Status of your vehicle pickup request'}
        >
          {canRequestPickup ? (
            <div className="qr-pickup">
              <div>
                <strong>Ready to request your car?</strong>
                <p className="muted">{pickupState.message} Valet staff receive the retrieval request through the queue and notification system.</p>
              </div>
              <button type="button" className="btn btn--primary btn--lg" disabled={pickup.isPending}
                onClick={() => pickup.mutate(session.id)}>{pickup.isPending ? 'Sending request…' : 'Need Vehicle · Request Pickup'}</button>
            </div>
          ) : (
            <p className="qr-pickup__closed" role="status">
              {pickup.isSuccess && pickup.variables === session?.id
                ? 'Pickup request sent. Follow the retrieval status; another request is not needed.'
                : pickupState.message}
            </p>
          )}
        </Panel>
      )}

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
          title={showRetrievalEstimate ? 'Retrieval estimate' : 'Pickup summary'}
          subtitle={showRetrievalEstimate
            ? 'How long until the vehicle reaches the handover point'
            : 'Final handover status — historical retrieval estimates are no longer active'}
        >
          {showRetrievalEstimate ? (
            <RetrievalEstimateCard
              estimate={estimateQuery.data ?? null}
              loading={estimateQuery.isFetching && !estimateQuery.data}
              onRequest={() => estimate.mutate()}
              busy={estimate.isPending}
            />
          ) : (
            <div className="retrieval-card retrieval-card--closed" role="status">
              <strong>{activeTicketStatus === 'CANCELLED' ? 'Ticket cancelled' : 'Pickup completed'}</strong>
              <p>
                This ticket is closed. A live retrieval estimate no longer applies.
                {session?.picked_up_at && ` Vehicle handed back on ${formatDateTime(session.picked_up_at)}.`}
              </p>
            </div>
          )}
        </Panel>
      )}

      {session && (
        <Panel
          title="Vehicle condition inspection"
          subtitle="Before/after comparison and damage assessment"
        >
          <div className="btn-row" style={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
            <p className="muted" style={{ margin: 0 }}>
              Review drop-off baseline photos, return pickup photos, OpenCV difference heatmaps, and verified findings.
            </p>
            <Link to={`/inspections/${session.id}`} className="btn btn--primary">
              View Vehicle Condition Report →
            </Link>
          </div>
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
