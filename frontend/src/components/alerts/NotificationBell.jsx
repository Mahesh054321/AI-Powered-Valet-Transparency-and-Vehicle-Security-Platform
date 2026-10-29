/**
 * The notification bell: an unread count in the top bar, opening a panel of
 * the alerts addressed to the signed-in user.
 *
 * **It polls, and says so.** There is no push transport here; `refetchInterval`
 * is a timer. The panel labels the delivery mode rather than implying the
 * count is live, because a badge that updates every fifteen seconds is not
 * real-time and should not be mistaken for one during a demonstration.
 *
 * **The count comes from `/feed/summary`, not from the feed.** Fetching every
 * alert to render one number would be wasteful, and the endpoint exists so the
 * badge costs one indexed count.
 *
 * **Marking read is per person.** The call changes nothing about the alert
 * itself, so clearing your own badge cannot clear a colleague's queue.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { Badge } from '../ui'
import { useToast } from '../../context/ToastContext'
import {
  alertTypeLabel,
  fetchAlertSummary,
  fetchAlerts,
  markAllAlertsRead,
  markAlertRead,
  relativeTime,
  severityTone,
} from '../../services/alertService'

const POLL_MS = 15_000

export function NotificationBell() {
  const [open, setOpen] = useState(false)
  const bellRef = useRef(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // Click outside and Escape key listener to cleanly dismiss notifications
  useEffect(() => {
    if (!open) return undefined

    function handleClickOutside(event) {
      if (bellRef.current && !bellRef.current.contains(event.target)) {
        setOpen(false)
      }
    }

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const summary = useQuery({
    queryKey: ['alert-summary'],
    queryFn: fetchAlertSummary,
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: false,
    staleTime: 5_000,
  })

  // Only fetched once the panel is open: there is no reason to pull the feed
  // on every poll when the badge needs only the count.
  const feed = useQuery({
    queryKey: ['alert-feed', { unreadOnly: true }],
    queryFn: () => fetchAlerts({ unreadOnly: true, pageSize: 10 }),
    enabled: open,
    refetchInterval: open ? POLL_MS : false,
  })

  const toast = useToast()

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['alert-summary'] })
    queryClient.invalidateQueries({ queryKey: ['alert-feed'] })
  }

  /**
   * Marking as read needs no success toast: the unread dot disappearing and the
   * badge count dropping is the confirmation, and announcing it would be noise
   * on an action taken repeatedly. A *failure* does need one, because the badge
   * staying put is indistinguishable from the click not registering.
   */
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

  const handleAlertClick = (alert) => {
    markOne.mutate(alert.alert_id)
    setOpen(false)
    navigate(`/alerts?id=${alert.alert_id}`)
  }

  return (
    <div className="bell" ref={bellRef}>
      <button
        type="button"
        className="bell__button"
        aria-label={`Notifications${unread ? `: ${unread} unread` : ''}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 && (
          // Capped display: "99+" tells you it is a lot without claiming a
          // precise figure the badge is not recomputing on every render.
          <span className="bell__count">{unread > 99 ? '99+' : unread}</span>
        )}
      </button>

      {open && (
        <div className="bell__panel" role="dialog" aria-label="Notifications">
          <header className="bell__head">
            <strong>Notifications</strong>
            <div style={{ display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending || unread === 0}
              >
                Mark all read
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm btn--icon"
                onClick={() => setOpen(false)}
                aria-label="Close notifications"
              >
                ✕
              </button>
            </div>
          </header>

          {feed.isPending ? (
            <p className="bell__empty">Loading…</p>
          ) : alerts.length === 0 ? (
            <p className="bell__empty">Nothing unread.</p>
          ) : (
            <ul className="bell__list">
              {alerts.map((alert) => {
                const custodyId = alert.custody_session_id || alert.custody_id
                const vehicleId = alert.vehicle_id || alert.vehicle?.id
                return (
                  <li className="bell__item" key={alert.alert_id}>
                    <div
                      className="bell__item-button"
                      role="button"
                      tabIndex={0}
                      onClick={() => handleAlertClick(alert)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          handleAlertClick(alert)
                        }
                      }}
                    >
                      <span className="bell__item-top">
                        <Badge tone={severityTone(alert.severity)}>{alert.severity}</Badge>
                        <span className="bell__item-type">{alertTypeLabel(alert.type)}</span>
                        <span className="bell__item-time">{relativeTime(alert.created_at)}</span>
                      </span>
                      <span className="bell__item-message">{alert.message}</span>
                      {alert.vehicle?.plate_number && (
                        <span className="bell__item-vehicle">{alert.vehicle.plate_number}</span>
                      )}
                      <div
                        className="bell__item-actions"
                        style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {custodyId && (
                          <Link
                            to={`/tracking/${custodyId}`}
                            className="btn btn--ghost btn--xs"
                            onClick={() => {
                              markOne.mutate(alert.alert_id)
                              setOpen(false)
                            }}
                          >
                            View on Map
                          </Link>
                        )}
                        {vehicleId && (
                          <Link
                            to={`/vehicles/${vehicleId}`}
                            className="btn btn--ghost btn--xs"
                            onClick={() => {
                              markOne.mutate(alert.alert_id)
                              setOpen(false)
                            }}
                          >
                            View Vehicle
                          </Link>
                        )}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}

          <footer className="bell__foot">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                setOpen(false)
                navigate('/alerts')
              }}
            >
              Open alert centre
            </button>
            {/* Stated rather than implied. A poll is not a push, and calling it
                live would be a small claim that is still false. */}
            <span className="bell__mode">polled every {POLL_MS / 1000}s</span>
          </footer>
        </div>
      )}
    </div>
  )
}

export default NotificationBell
