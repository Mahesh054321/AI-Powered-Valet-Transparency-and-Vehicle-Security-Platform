/**
 * Customer dashboard.
 *
 * One composed snapshot drives every card, so the cards cannot disagree with
 * each other. The page's job is to render that snapshot honestly:
 *
 * - a section whose `state` is `empty` shows the backend's own reason in words,
 *   never a blank card, because blank reads as "nothing is wrong";
 * - the **Request Vehicle** button is disabled for exactly the reasons the
 *   backend would refuse the request, so the customer is never shown an enabled
 *   button that returns 409;
 * - after a request, the estimate is shown immediately from the response rather
 *   than waiting on a refetch, so the confirmation is not briefly blank.
 *
 * The dashboard refreshes on a short poll while a retrieval is active, since
 * that is the only period when the numbers move. Nothing on this page is static.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useToast } from '../context/ToastContext'
import { normaliseError } from '../services/apiClient'
import {
  ConditionStatusCard,
  CurrentLocationCard,
  EstimatedRetrievalCard,
  SecurityStatusCard,
  VehicleStatusCard,
} from '../components/dashboard/DashboardCards'
import { AlertBanner, ErrorNotice, Spinner } from '../components/ui'
import {
  custodyLabel,
  fetchDashboard,
  requestVehicle,
} from '../services/dashboardService'

/** Refresh interval while a retrieval is in flight; the numbers move then. */
const ACTIVE_POLL_MS = 10_000
/** Refresh interval otherwise — still short enough to notice a check-in. */
const IDLE_POLL_MS = 30_000

const RETRIEVAL_ACTIVE = ['RETRIEVAL_REQUESTED', 'RETRIEVAL_IN_PROGRESS']

export function CustomerDashboardPage() {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['customer-dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: (q) => {
      const status = q.state.data?.custody?.status
      return RETRIEVAL_ACTIVE.includes(status) ? ACTIVE_POLL_MS : IDLE_POLL_MS
    },
  })

  const toast = useToast()


  const request = useMutation({
    mutationFn: (custodySessionId) => requestVehicle(custodySessionId),
    onSuccess: (data) => {
      // The customer pressed a button and the page changed underneath them;
      // without this the only signal is a status badge moving further down.
      toast.success(
        'Pickup requested',
        data?.estimated_minutes != null
          ? `A valet has been notified. Estimated retrieval in about ${data.estimated_minutes} minutes.`
          : 'A valet has been notified. You will see the status change here.',
      )
      // Refetch so the cards reflect the new custody status and the stored
      // estimate, rather than only the estimate in the mutation response.
      queryClient.invalidateQueries({ queryKey: ['customer-dashboard'] })
    },
    onError: (error) => toast.error('Could not request your vehicle', normaliseError(error).message),
  })

  if (query.isPending) return <Spinner label="Loading your dashboard…" />
  if (query.isError) {
    return <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
  }

  const data = query.data
  const actions = data.actions ?? {}
  const retrievalActive = RETRIEVAL_ACTIVE.includes(data.custody?.status)

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>My Vehicle</h1>
          <p className="muted">
            Everything about the vehicle you have with us, read live from the
            platform.
          </p>
        </div>
        <div className="btn-row">
          <Link className="btn btn--ghost" to="/tickets">
            My tickets
          </Link>
          {data.custody_session_id && (
            <Link className="btn btn--ghost" to={`/tracking/${data.custody_session_id}`}>
              Live tracking
            </Link>
          )}
        </div>
      </header>

      {/* The mutation's own error, shown at the top: it is the one message on
          this page the customer is most likely to need to act on. */}
      {request.isError && (
        <ErrorNotice
          error={request.error}
          onRetry={() =>
            data.custody_session_id && request.mutate(data.custody_session_id)
          }
        />
      )}

      {request.isSuccess && request.data && (
        <AlertBanner tone="success" title="Vehicle requested">
          <p>{request.data.message}</p>
          {request.data.estimate_available ? (
            <p className="alert-banner__figures">
              <strong>Estimated Retrieval: {request.data.estimated_minutes} minutes</strong>
              {' · '}
              Queue Position: {request.data.queue_position}
              {' · '}
              Recommended Dispatch: {request.data.recommended_dispatch}
            </p>
          ) : (
            <p>
              No estimate could be produced
              {request.data.estimate_error ? `: ${request.data.estimate_error}` : ''}. Your
              request is recorded and a valet has been notified.
            </p>
          )}
        </AlertBanner>
      )}

      {!data.has_active_custody ? (
        <section className="dash-empty">
          <h2>No vehicle in custody</h2>
          <p className="muted">{data.custody?.reason}</p>
          <p className="muted">
            Once a valet checks your vehicle in, its location, security status,
            condition report and retrieval estimate will appear here.
          </p>
          <Link className="btn btn--primary" to="/vehicles">
            View my vehicles
          </Link>
        </section>
      ) : (
        <>
          <section className="dash-request">
            <div>
              <h2>Need your vehicle?</h2>
              <p className="muted">
                {actions.reason ??
                  `Current status: ${custodyLabel(data.custody?.status)}. Requesting tells a valet to bring it to the handover point.`}
              </p>
            </div>
            <button
              type="button"
              className="btn btn--primary btn--lg"
              disabled={!actions.can_request || request.isPending}
              onClick={() => request.mutate(data.custody_session_id)}
            >
              {request.isPending
                ? 'Requesting…'
                : retrievalActive
                  ? 'Retrieval in progress'
                  : 'Request Vehicle'}
            </button>
          </section>

          {/* Order is the priority a customer actually reads in: what is my car
              doing, is it safe, where is it, is it damaged, when do I get it
              back. The retrieval estimate is last because it is also the least
              certain — a prototype model on synthetic data — and a figure that
              weak in provenance should not outrank a measurement. */}
          <div className="dash-grid">
            <VehicleStatusCard vehicle={data.vehicle} custody={data.custody} />
            <SecurityStatusCard security={data.security} />
            <CurrentLocationCard location={data.location} zone={data.zone} />
            <ConditionStatusCard condition={data.condition} />
            <EstimatedRetrievalCard retrieval={data.retrieval} />
          </div>

          <p className="dash-footnote">
            Refreshed automatically. The retrieval estimate comes from a baseline
            model trained on synthetic data and is indicative only.
          </p>
        </>
      )}
    </div>
  )
}

export default CustomerDashboardPage
