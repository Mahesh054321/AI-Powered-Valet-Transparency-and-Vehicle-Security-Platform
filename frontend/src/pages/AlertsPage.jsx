/**
 * Alert centre: the full feed with filters, and the actions an actor is
 * entitled to take.
 *
 * **The backend decides what is visible.** There is no client-side role filter
 * here, because hiding rows that were already sent is cosmetic — the scoping
 * has to happen in the query, and it does. What this page adds is presentation
 * and the actions the actor may perform.
 *
 * **Actions come from `can_resolve`, not from the role string.** The summary
 * endpoint reports whether this actor may close alerts, so the UI cannot drift
 * from the backend's own rule. A button that returns 403 is worse than no
 * button: it tells someone the action is available and then refuses them.
 *
 * **Read and resolved are different controls.** Marking read is a personal
 * acknowledgement; resolving is a decision that gets attributed and audited.
 * Keeping them visually separate stops a quick "clear my badge" from becoming
 * an unreviewed closure of a security incident.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertBanner, Badge, ConfirmDialog, ErrorNotice, Panel, Spinner } from '../components/ui'
import { useToast } from '../context/ToastContext'
import {
  ALERT_SEVERITIES,
  ALERT_STATUSES,
  ALERT_TYPES,
  alertTypeLabel,
  fetchAlerts,
  fetchAlertSummary,
  markAlertRead,
  markAllAlertsRead,
  relativeTime,
  resolveAlert,
  severityTone,
} from '../services/alertService'

const POLL_MS = 15_000

const TYPE_TONE = {
  GEOFENCE_BREACH: 'badge--danger',
  SUSPICIOUS_MOVEMENT: 'badge--warning',
  OVERSPEED: 'badge--info',
  POTENTIAL_DAMAGE: 'badge--warning',
  RETRIEVAL_READY: 'badge--success',
}

function statusLabel(status) {
  return (
    ALERT_STATUSES.find((entry) => entry.value === status)?.label ??
    status?.charAt(0).toUpperCase() + status?.slice(1).toLowerCase()
  )
}

export function AlertsPage() {
  const [filters, setFilters] = useState({
    status: '',
    type: '',
    severity: '',
    unreadOnly: false,
  })
  const [pendingResolve, setPendingResolve] = useState(null)
  const [resolveNotes, setResolveNotes] = useState('')
  const [notice, setNotice] = useState(null)
  const queryClient = useQueryClient()

  const summary = useQuery({
    queryKey: ['alert-summary'],
    queryFn: fetchAlertSummary,
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: false,
  })

  const feed = useQuery({
    queryKey: ['alert-feed', filters],
    queryFn: () => fetchAlerts({ ...filters, pageSize: 50 }),
    refetchInterval: POLL_MS,
    // Keep the previous list while new filters load, so the table does not
    // blank and reflow on every dropdown change.
    placeholderData: (previous) => previous,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['alert-summary'] })
    queryClient.invalidateQueries({ queryKey: ['alert-feed'] })
  }

  /**
   * Marking as read needs no success toast — the row losing its unread dot and
   * the badge count dropping is the confirmation, and announcing a click taken
   * a dozen times in a row would be noise. A failure does need one, because a
   * badge that refuses to clear is indistinguishable from a click that never
   * registered.
   */
  const toast = useToast()

  const markOne = useMutation({
    mutationFn: markAlertRead,
    onSuccess: invalidate,
    onError: () => toast.error('Could not mark that alert as read'),
  })

  const markAll = useMutation({
    mutationFn: markAllAlertsRead,
    onSuccess: invalidate,
    onError: () => toast.error('Could not mark alerts as read'),
  })

  const resolve = useMutation({
    mutationFn: ({ alertId, notes, dismiss }) => resolveAlert(alertId, { notes, dismiss }),
    onSuccess: (data) => {
      setPendingResolve(null)
      setNotice({
        tone: 'info',
        title: data.status === 'DISMISSED' ? 'Alert dismissed' : 'Alert resolved',
        body: 'Recorded in the audit log with your name against it.',
      })
      invalidate()
    },
    onError: (error) => {
      setPendingResolve(null)
      setNotice({
        tone: 'warning',
        title: 'Could not close the alert',
        body: error?.message ?? 'The request was refused.',
      })
    },
  })

  const canResolve = summary.data?.can_resolve === true
  const alerts = feed.data?.alerts ?? []
  const set = (key) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setFilters((current) => ({ ...current, [key]: value }))
  }

  if (feed.isPending) {
    return (
      <div className="page">
        <Spinner label="Loading alerts…" />
      </div>
    )
  }

  if (feed.isError) {
    return (
      <div className="page">
        <ErrorNotice error={feed.error} onRetry={() => feed.refetch()} />
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Alert Centre</h1>
          <p className="muted">
            {feed.data?.total ?? 0} alert(s) visible to you
            {summary.data ? ` · ${summary.data.unread} unread` : ''}. Delivery is polled, not pushed.
          </p>
        </div>
        <div className="page__actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending || (summary.data?.unread ?? 0) === 0}
          >
            Mark all read
          </button>
        </div>
      </header>

      {notice && (
        <AlertBanner tone={notice.tone} title={notice.title} onDismiss={() => setNotice(null)}>
          <p>{notice.body}</p>
        </AlertBanner>
      )}

      {/* Every alert here has already passed the backend's role scoping; these
          controls narrow the view, they do not decide it. */}
      <div className="filter-bar">
        <label className="filter-bar__field">
          <span>Type</span>
          <select value={filters.type} onChange={set('type')}>
            <option value="">All types</option>
            {ALERT_TYPES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label className="filter-bar__field">
          <span>Severity</span>
          <select value={filters.severity} onChange={set('severity')}>
            <option value="">All severities</option>
            {ALERT_SEVERITIES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label className="filter-bar__field">
          <span>Status</span>
          <select value={filters.status} onChange={set('status')}>
            <option value="">Any status</option>
            {ALERT_STATUSES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label className="filter-bar__check">
          <input
            type="checkbox"
            checked={filters.unreadOnly}
            onChange={set('unreadOnly')}
          />
          <span>Unread only</span>
        </label>
      </div>

      <Panel
        title="Alerts"
        subtitle="Most severe first, then most recent"
        actions={<Badge tone="badge--muted">{alerts.length} shown</Badge>}
      >
        <div className="mgr-table mgr-table--7" role="table">
          <div className="mgr-table__row mgr-table__row--head" role="row">
            <span role="columnheader">Severity</span>
            <span role="columnheader">Type</span>
            <span role="columnheader">Message</span>
            <span role="columnheader">Vehicle</span>
            <span role="columnheader">Raised</span>
            <span role="columnheader">Status</span>
            <span role="columnheader">Actions</span>
          </div>

          {alerts.length === 0 ? (
            <p className="section__empty">
              No alerts match these filters. Widen the selection, or there is
              simply nothing outstanding — both look the same from here, and
              that is the point of an empty state that says so.
            </p>
          ) : (
            alerts.map((alert) => (
              <div
                className={`mgr-table__row ${alert.is_read ? '' : 'mgr-table__row--unread'}`}
                role="row"
                key={alert.alert_id}
              >
                <span role="cell">
                  <Badge tone={severityTone(alert.severity)}>{alert.severity}</Badge>
                </span>
                <span role="cell">
                  <Badge tone={TYPE_TONE[alert.type] ?? 'badge--muted'}>
                    {alertTypeLabel(alert.type)}
                  </Badge>
                </span>
                <span role="cell">
                  {alert.message}
                  {alert.source?.type && (
                    <span className="mgr-table__muted">
                      Source: {alert.source.type.replace(/_/g, ' ').toLowerCase()}
                    </span>
                  )}
                </span>
                <span role="cell">{alert.vehicle?.plate_number ?? '—'}</span>
                <span role="cell" className="mgr-table__muted">
                  {relativeTime(alert.created_at)}
                </span>
                <span role="cell">
                  <Badge tone={alert.is_open ? 'badge--warning' : 'badge--muted'}>
                    {statusLabel(alert.status)}
                  </Badge>
                  {!alert.is_read && (
                    <span className="alert-unread-dot" title="Unread" aria-label="Unread" />
                  )}
                </span>
                <span role="cell" className="alert-actions">
                  {!alert.is_read && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => markOne.mutate(alert.alert_id)}
                      disabled={markOne.isPending}
                    >
                      Mark read
                    </button>
                  )}
                  {/* Only rendered when the backend says this actor may close
                      alerts. Guessing from the role here is how a button ends
                      up returning 403. */}
                  {canResolve && alert.is_open && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setPendingResolve(alert)}
                    >
                      Resolve
                    </button>
                  )}
                </span>
              </div>
            ))
          )}
        </div>
      </Panel>

      <p className="dash-footnote">
        Marking an alert read records that <em>you</em> saw it; it does not close
        it or change anyone else&apos;s view. Closing an alert is a manager
        action, is attributed to you, and is written to the audit log.
      </p>

      <ConfirmDialog
        open={Boolean(pendingResolve)}
        title="Close this alert?"
        confirmLabel="Resolve"
        busy={resolve.isPending}
        onConfirm={() =>
          resolve.mutate({
            alertId: pendingResolve.alert_id,
            notes: resolveNotes,
            dismiss: false,
          })
        }
        onCancel={() => {
          setPendingResolve(null)
          setResolveNotes('')
        }}
      >
        {/* The dialog names what is being closed. A bare "are you sure?" on a
            security incident is decoration rather than a check. */}
        <p>
          <strong>
            {alertTypeLabel(pendingResolve?.type)} · {pendingResolve?.severity}
          </strong>
        </p>
        <p>{pendingResolve?.message}</p>
        {pendingResolve?.vehicle?.plate_number && (
          <p className="muted">Vehicle {pendingResolve.vehicle.plate_number}</p>
        )}
        <label className="field">
          <span>Resolution note (optional)</span>
          <textarea
            rows={3}
            value={resolveNotes}
            maxLength={500}
            onChange={(event) => setResolveNotes(event.target.value)}
            placeholder="What was done, or why no action was needed."
          />
        </label>
        <p className="muted">Resolving records your name in the audit log.</p>
      </ConfirmDialog>
    </div>
  )
}

export default AlertsPage
