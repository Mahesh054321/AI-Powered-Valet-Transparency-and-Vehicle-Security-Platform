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
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertBanner, Badge, ConfirmDialog, ErrorNotice, Panel, Spinner } from '../components/ui'
import { useToast } from '../context/ToastContext'
import { normaliseError } from '../services/apiClient'
import { alertGuidance } from '../utils/alertGuidance'
import {
  ALERT_SEVERITIES,
  ALERT_STATUSES,
  ALERT_TYPES,
  alertTypeLabel,
  deleteAlert,
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
  const [searchParams] = useSearchParams()
  const highlightAlertId = searchParams.get('id')
  const [filters, setFilters] = useState({
    status: '',
    type: '',
    severity: '',
    unreadOnly: false,
  })
  const [pendingResolve, setPendingResolve] = useState(null)
  const [resolveNotes, setResolveNotes] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const [selectedAlertIds, setSelectedAlertIds] = useState([])
  const [pendingBulkAction, setPendingBulkAction] = useState(null)
  const [bulkResolveNotes, setBulkResolveNotes] = useState('')
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
    queryClient.invalidateQueries({ queryKey: ['customer-dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['staff-dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['manager-dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['gps'] })
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
      const apiError = normaliseError(error)
      setPendingResolve(null)
      setNotice({
        tone: 'warning',
        title: 'Could not close the alert',
        body: apiError.message,
        requestId: apiError.requestId,
      })
      invalidate()
    },
  })

  const remove = useMutation({
    mutationFn: (alertId) => deleteAlert(alertId),
    onSuccess: () => {
      setPendingDelete(null)
      setNotice({
        tone: 'info',
        title: 'Alert deleted',
        body: 'The resolved alert was removed.',
      })
      invalidate()
    },
    onError: (error) => {
      const apiError = normaliseError(error)
      setPendingDelete(null)
      setNotice({
        tone: 'warning',
        title: 'Could not delete the alert',
        body: apiError.message,
        requestId: apiError.requestId,
      })
      invalidate()
    },
  })

  const bulkResolve = useMutation({
    mutationFn: async ({ alertIds, notes }) => {
      const results = await Promise.allSettled(
        alertIds.map((alertId) => resolveAlert(alertId, { notes, dismiss: false })),
      )
      return results.map((result, index) => ({
        alertId: alertIds[index],
        result,
      }))
    },
    onSuccess: (results) => {
      const succeeded = results
        .filter(({ result }) => result.status === 'fulfilled')
        .map(({ alertId }) => alertId)
      const failures = results.filter(({ result }) => result.status === 'rejected')
      const firstError = failures[0]?.result.reason

      setPendingBulkAction(null)
      setBulkResolveNotes('')
      setSelectedAlertIds((current) => current.filter((id) => !succeeded.includes(id)))
      setNotice({
        tone: failures.length ? 'warning' : 'info',
        title: `Resolved ${succeeded.length} of ${results.length} selected alerts`,
        body: failures.length
          ? `${failures.length} alert(s) could not be resolved. ${normaliseError(firstError).message} Failed alerts remain selected so you can retry.`
          : 'Each resolution was recorded in the audit log.',
      })
      invalidate()
    },
    onError: (error) => {
      setPendingBulkAction(null)
      setNotice({
        tone: 'warning',
        title: 'Could not resolve the selected alerts',
        body: normaliseError(error).message,
      })
    },
  })

  const bulkDelete = useMutation({
    mutationFn: async (alertIds) => {
      const results = await Promise.allSettled(alertIds.map((alertId) => deleteAlert(alertId)))
      return results.map((result, index) => ({
        alertId: alertIds[index],
        result,
      }))
    },
    onSuccess: (results) => {
      const succeeded = results
        .filter(({ result }) => result.status === 'fulfilled')
        .map(({ alertId }) => alertId)
      const failures = results.filter(({ result }) => result.status === 'rejected')
      const firstError = failures[0]?.result.reason

      setPendingBulkAction(null)
      setSelectedAlertIds((current) => current.filter((id) => !succeeded.includes(id)))
      setNotice({
        tone: failures.length ? 'warning' : 'info',
        title: `Deleted ${succeeded.length} of ${results.length} selected alerts`,
        body: failures.length
          ? `${failures.length} alert(s) could not be deleted. ${normaliseError(firstError).message} Failed alerts remain selected so you can retry.`
          : 'The selected resolved alerts were permanently removed.',
      })
      invalidate()
    },
    onError: (error) => {
      setPendingBulkAction(null)
      setNotice({
        tone: 'warning',
        title: 'Could not delete the selected alerts',
        body: normaliseError(error).message,
      })
    },
  })

  const canResolve = summary.data?.can_resolve === true
  const alerts = feed.data?.alerts ?? []
  const selectableAlerts = canResolve
    ? alerts.filter((alert) => alert.is_open || alert.status === 'RESOLVED')
    : []
  const selectedAlerts = selectableAlerts.filter((alert) =>
    selectedAlertIds.includes(alert.alert_id),
  )
  const selectedOpenAlerts = selectedAlerts.filter((alert) => alert.is_open)
  const selectedResolvedAlerts = selectedAlerts.filter((alert) => alert.status === 'RESOLVED')
  const allSelectableSelected =
    selectableAlerts.length > 0 &&
    selectableAlerts.every((alert) => selectedAlertIds.includes(alert.alert_id))
  const bulkActionPending = bulkResolve.isPending || bulkDelete.isPending
  const set = (key) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setFilters((current) => ({ ...current, [key]: value }))
    setSelectedAlertIds([])
  }
  const toggleAlert = (alertId) => {
    setSelectedAlertIds((current) =>
      current.includes(alertId)
        ? current.filter((id) => id !== alertId)
        : [...current, alertId],
    )
  }
  const toggleAllVisible = () => {
    const visibleIds = selectableAlerts.map((alert) => alert.alert_id)
    setSelectedAlertIds((current) =>
      allSelectableSelected
        ? current.filter((id) => !visibleIds.includes(id))
        : [...new Set([...current, ...visibleIds])],
    )
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
          <p className="muted">
            Only managers can resolve alerts. Resolve an alert before deleting it; deletion is permanent.
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
          {notice.requestId && (
            <p className="muted mono">Request ID: {notice.requestId}</p>
          )}
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
        {canResolve && (
          <div className="alert-bulk-actions" aria-live="polite">
            <span>
              {selectedAlerts.length
                ? `${selectedAlerts.length} alert(s) selected`
                : 'Select alerts to resolve or delete them together'}
            </span>
            <div className="alert-bulk-actions__buttons">
              {selectedOpenAlerts.length > 0 && (
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={() =>
                    setPendingBulkAction({
                      type: 'resolve',
                      alertIds: selectedOpenAlerts.map((alert) => alert.alert_id),
                    })
                  }
                  disabled={bulkActionPending}
                >
                  Resolve selected ({selectedOpenAlerts.length})
                </button>
              )}
              {selectedResolvedAlerts.length > 0 && (
                <button
                  type="button"
                  className="btn btn--danger-quiet btn--sm"
                  onClick={() =>
                    setPendingBulkAction({
                      type: 'delete',
                      alertIds: selectedResolvedAlerts.map((alert) => alert.alert_id),
                    })
                  }
                  disabled={bulkActionPending}
                >
                  Delete selected ({selectedResolvedAlerts.length})
                </button>
              )}
              {selectedAlerts.length > 0 && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setSelectedAlertIds([])}
                  disabled={bulkActionPending}
                >
                  Clear selection
                </button>
              )}
            </div>
          </div>
        )}
        <div className="alert-table-scroll">
        <div
          className={`mgr-table mgr-table--7 alert-table${canResolve ? ' alert-table--selectable' : ''}`}
          role="table"
        >
          <div className="mgr-table__row mgr-table__row--head" role="row">
            {canResolve && (
              <span role="columnheader" data-label="Select">
                <input
                  type="checkbox"
                  aria-label="Select all eligible alerts on this page"
                  checked={allSelectableSelected}
                  disabled={selectableAlerts.length === 0 || bulkActionPending}
                  onChange={toggleAllVisible}
                />
              </span>
            )}
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
            alerts.map((alert) => {
              const custodyId = alert.custody_session_id || alert.custody_id
              const vehicleId = alert.vehicle_id || alert.vehicle?.id
              const isHighlighted = highlightAlertId && alert.alert_id === highlightAlertId
              const isSelected = selectedAlertIds.includes(alert.alert_id)

              return (
                <div
                  className={`mgr-table__row ${alert.is_read ? '' : 'mgr-table__row--unread'} ${isHighlighted ? 'is-highlighted' : ''} ${isSelected ? 'mgr-table__row--selected' : ''}`}
                  role="row"
                  key={alert.alert_id}
                  style={isHighlighted ? { outline: '2px solid var(--primary, #3b82f6)', background: 'var(--surface-muted)' } : undefined}
                >
                  {canResolve && (
                    <span role="cell" data-label="Select">
                      <input
                        type="checkbox"
                        aria-label={`Select alert for bulk action: ${alert.message}`}
                        checked={isSelected}
                        disabled={
                          bulkActionPending ||
                          (!alert.is_open && alert.status !== 'RESOLVED')
                        }
                        onChange={() => toggleAlert(alert.alert_id)}
                      />
                    </span>
                  )}
                  <span role="cell" data-label="Severity">
                    <Badge tone={severityTone(alert.severity)}>{alert.severity}</Badge>
                  </span>
                  <span role="cell" data-label="Type">
                    <Badge tone={TYPE_TONE[alert.type] ?? 'badge--muted'}>
                      {alertTypeLabel(alert.type)}
                    </Badge>
                  </span>
                  <span role="cell" data-label="Message" className="alert-table__message">
                    <strong className="alert-table__primary-message">{alert.message}</strong>
                    {alertGuidance(alert.type) && (
                      <span className="alert-table__guidance">
                        <strong>Next step:</strong> {alertGuidance(alert.type)}
                      </span>
                    )}
                    {alert.source?.type && (
                      <span className="mgr-table__muted">
                        Source: {alert.source.type.replace(/_/g, ' ').toLowerCase()}
                      </span>
                    )}
                  </span>
                  <span role="cell" data-label="Vehicle">{alert.vehicle?.plate_number ?? '—'}</span>
                  <span role="cell" data-label="Raised" className="mgr-table__muted">
                    {relativeTime(alert.created_at)}
                  </span>
                  <span role="cell" data-label="Status">
                    <Badge tone={alert.is_open ? 'badge--warning' : 'badge--muted'}>
                      {statusLabel(alert.status)}
                    </Badge>
                    {!alert.is_read && (
                      <span className="alert-unread-dot" title="Unread" aria-label="Unread" />
                    )}
                  </span>
                  <span role="cell" data-label="Actions" className="alert-actions">
                    {custodyId && (
                      <Link
                        to={`/tracking/${custodyId}`}
                        className="btn btn--ghost btn--sm"
                        title="View Live Tracking on Map"
                      >
                        View on Map
                      </Link>
                    )}
                    {vehicleId && (
                      <Link
                        to={`/vehicles/${vehicleId}`}
                        className="btn btn--ghost btn--sm"
                        title="View Vehicle Details"
                      >
                        View Vehicle
                      </Link>
                    )}
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
                    {canResolve && alert.status === 'RESOLVED' && (
                      <button
                        type="button"
                        className="btn btn--danger-quiet btn--sm"
                        onClick={() => setPendingDelete(alert)}
                        disabled={remove.isPending}
                      >
                        Delete
                      </button>
                    )}
                  </span>
                </div>
              )
            })
          )}
        </div>
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

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete resolved alert?"
        confirmLabel="Delete alert"
        busy={remove.isPending}
        onConfirm={() => remove.mutate(pendingDelete.alert_id)}
        onCancel={() => setPendingDelete(null)}
      >
        <p>
          This permanently removes the resolved alert. The action will be
          recorded in the audit log.
        </p>
        <p>
          <strong>{pendingDelete?.message}</strong>
        </p>
      </ConfirmDialog>
      <ConfirmDialog
        open={pendingBulkAction?.type === 'resolve'}
        title="Resolve selected alerts?"
        confirmLabel="Resolve selected alerts"
        busy={bulkResolve.isPending}
        onConfirm={() =>
          bulkResolve.mutate({
            alertIds: pendingBulkAction.alertIds,
            notes: bulkResolveNotes,
          })
        }
        onCancel={() => {
          setPendingBulkAction(null)
          setBulkResolveNotes('')
        }}
      >
        <p>
          Resolve {pendingBulkAction?.alertIds.length} selected open alert(s)?
          Each resolution will be recorded in the audit log.
        </p>
        <label className="field">
          <span>Resolution note (optional, applied to each alert)</span>
          <textarea
            rows={3}
            value={bulkResolveNotes}
            maxLength={500}
            onChange={(event) => setBulkResolveNotes(event.target.value)}
            placeholder="What was done, or why no action was needed."
          />
        </label>
      </ConfirmDialog>

      <ConfirmDialog
        open={pendingBulkAction?.type === 'delete'}
        title="Delete selected resolved alerts?"
        confirmLabel="Delete selected alerts"
        busy={bulkDelete.isPending}
        onConfirm={() => bulkDelete.mutate(pendingBulkAction.alertIds)}
        onCancel={() => setPendingBulkAction(null)}
      >
        <p>
          Permanently delete {pendingBulkAction?.alertIds.length} selected resolved alert(s)?
          This cannot be undone. Each deletion will be recorded in the audit log.
        </p>
      </ConfirmDialog>
    </div>
  )
}

export default AlertsPage
