/**
 * Unread alert preview. Unlike the detailed Security Alerts screen, this is the
 * popover opened from the topbar. It shows the recorded message, optional
 * supporting detail, and a clearly distinguished suggested next step.
 * Data is polled, not pushed; marking read never resolves an incident.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useToast } from '../../context/ToastContext'
import { alertGuidance } from '../../utils/alertGuidance'
import {
  alertTypeLabel,
  fetchAlertSummary,
  fetchAlerts,
  markAllAlertsRead,
  markAlertRead,
  relativeTime,
} from '../../services/alertService'

const POLL_MS = 15_000
const ICONS = {
  GEOFENCE_BREACH: '◎',
  SUSPICIOUS_MOVEMENT: '↗',
  OVERSPEED: '⇧',
  POTENTIAL_DAMAGE: '◇',
  RETRIEVAL_READY: '✓',
  RETRIEVAL_REQUESTED: '🚗',
}

function previewType(alert) {
  return alert.type || alert.alert_type || 'OTHER'
}

function severityClass(severity) {
  return ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].includes(severity) ? severity.toLowerCase() : 'low'
}

export function NotificationBell() {
  const [open, setOpen] = useState(false)
  const bellRef = useRef(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()

  useEffect(() => {
    if (!open) return undefined
    function onOutside(event) {
      if (bellRef.current && !bellRef.current.contains(event.target)) setOpen(false)
    }
    function onKey(event) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const summary = useQuery({
    queryKey: ['alert-summary'],
    queryFn: fetchAlertSummary,
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: false,
    staleTime: 5_000,
  })
  const feed = useQuery({
    queryKey: ['alert-feed', { unreadOnly: true }],
    queryFn: () => fetchAlerts({ unreadOnly: true, pageSize: 10 }),
    enabled: open,
    refetchInterval: open ? POLL_MS : false,
  })
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['alert-summary'] })
    queryClient.invalidateQueries({ queryKey: ['alert-feed'] })
  }
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
  const unread = summary.data?.unread ?? 0
  const alerts = feed.data?.alerts ?? []

  function readAndOpen(alert) {
    markOne.mutate(alert.alert_id)
    setOpen(false)
    navigate(`/alerts?id=${alert.alert_id}`)
  }

  function readAndGo(alert) {
    markOne.mutate(alert.alert_id)
    setOpen(false)
  }

  return (
    <div className="bell" ref={bellRef}>
      <button
        type="button"
        className={`bell__button${open ? ' bell__button--open' : ''}`}
        aria-label={`Notifications${unread ? `: ${unread} unread` : ''}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 && <span className="bell__count">{unread > 99 ? '99+' : unread}</span>}
      </button>

      {open && (
        <div className="bell__panel" role="dialog" aria-label="Notifications">
          <header className="bell__head">
            <div className="bell__head-copy">
              <span className="bell__eyebrow">SECURITY ACTIVITY</span>
              <h2>Notifications</h2>
              <span className="bell__head-summary">
                {unread === 0 ? 'You are all caught up' : `${unread} unread update${unread === 1 ? '' : 's'}`}
              </span>
            </div>
            <button
              type="button"
              className="bell__close"
              onClick={() => setOpen(false)}
              aria-label="Close notifications"
            >
              ✕
            </button>
          </header>

          <div className="bell__toolbar">
            <span>Latest vehicle activity</span>
            <button
              type="button"
              className="bell__mark-all"
              onClick={() => markAll.mutate()}
              disabled={markAll.isPending || unread === 0}
            >
              {markAll.isPending ? 'Marking…' : 'Mark all read'}
            </button>
          </div>

          {feed.isPending ? (
            <p className="bell__empty">Loading your latest alerts…</p>
          ) : feed.isError ? (
            <div className="bell__empty">
              <p>Could not load notifications.</p>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => feed.refetch()}>
                Try again
              </button>
            </div>
          ) : alerts.length === 0 ? (
            <div className="bell__empty">
              <span className="bell__empty-icon" aria-hidden="true">✓</span>
              <strong>Nothing unread</strong>
              <p>New vehicle and security updates will appear here.</p>
            </div>
          ) : (
            <ul className="bell__list">
              {alerts.map((alert) => {
                const type = previewType(alert)
                const severity = severityClass(alert.severity)
                const custodyId = alert.custody_session_id || alert.custody_id
                const vehicleId = alert.vehicle_id || alert.vehicle?.id
                const message = alert.message || alert.description || 'Activity recorded for this vehicle.'
                const description = alert.description?.trim()
                const reason = alert.reason?.trim()
                const extra = description && description !== message ? description : null
                const recordedReason = reason && reason !== message && reason !== extra ? reason : null
                const guidance = alertGuidance(type)
                return (
                  <li className={`bell__item bell__item--${severity}`} key={alert.alert_id}>
                    <button type="button" className="bell__item-button" onClick={() => readAndOpen(alert)}>
                      <span className="bell__item-header">
                        <span className={`bell__item-icon bell__item-icon--${severity}`} aria-hidden="true">
                          {ICONS[type] ?? '!'}
                        </span>
                        <span className="bell__item-header-content">
                          <span className="bell__item-topline">
                            <strong className="bell__item-type">{alertTypeLabel(type)}</strong>
                            <span className="bell__item-time">{relativeTime(alert.created_at) ?? 'Time unknown'}</span>
                          </span>
                          <span className="bell__item-metadata">
                            <span className={`bell__severity bell__severity--${severity}`}>{alert.severity || 'INFO'}</span>
                            {alert.vehicle?.plate_number && <span className="bell__item-vehicle">{alert.vehicle.plate_number}</span>}
                          </span>
                        </span>
                      </span>
                      <span className="bell__item-message">{message}</span>
                      {extra && <span className="bell__item-detail">{extra}</span>}
                      {recordedReason && (
                        <span className="bell__item-reason"><strong>Recorded reason:</strong> {recordedReason}</span>
                      )}
                      {guidance && (
                        <span className="bell__item-guidance"><strong>Recommended next step</strong>{guidance}</span>
                      )}
                      <span className="bell__view-detail">View full alert details <span aria-hidden="true">↗</span></span>
                    </button>
                    {(custodyId || vehicleId) && (
                      <div className="bell__item-actions">
                        {custodyId && (
                          <Link to={`/tracking/${custodyId}`} onClick={() => readAndGo(alert)}>
                            <span aria-hidden="true">◎</span> View map
                          </Link>
                        )}
                        {vehicleId && (
                          <Link to={`/vehicles/${vehicleId}`} onClick={() => readAndGo(alert)}>
                            <span aria-hidden="true">▣</span> View vehicle
                          </Link>
                        )}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          <footer className="bell__foot">
            <button
              type="button"
              className="bell__open-centre"
              onClick={() => { setOpen(false); navigate('/alerts') }}
            >
              Open security alert centre <span aria-hidden="true">→</span>
            </button>
            <span className="bell__mode">polled every {POLL_MS / 1000}s</span>
          </footer>
        </div>
      )}
    </div>
  )
}

export default NotificationBell
