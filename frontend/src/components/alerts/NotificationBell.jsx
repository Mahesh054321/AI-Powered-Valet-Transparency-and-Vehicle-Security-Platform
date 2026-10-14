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

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
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
  const navigate = useNavigate()
  const queryClient = useQueryClient()

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

  return (
    <div className="bell">
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
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => markAll.mutate()}
              disabled={markAll.isPending || unread === 0}
            >
              Mark all read
            </button>
          </header>

          {feed.isPending ? (
            <p className="bell__empty">Loading…</p>
          ) : alerts.length === 0 ? (
            <p className="bell__empty">Nothing unread.</p>
          ) : (
            <ul className="bell__list">
              {alerts.map((alert) => (
                <li className="bell__item" key={alert.alert_id}>
                  <button
                    type="button"
                    className="bell__item-button"
                    onClick={() => markOne.mutate(alert.alert_id)}
                    // Disabled while the mutation is in flight rather than
                    // optimistically clearing: a count that drops and then
                    // comes back is more confusing than a brief delay.
                    disabled={markOne.isPending}
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
                  </button>
                </li>
              ))}
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
