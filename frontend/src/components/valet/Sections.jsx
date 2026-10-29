/**
 * The remaining operator dashboard sections: security alerts, inspections,
 * the retrieval queue and zone occupancy.
 *
 * Grouped in one module because each is small and none is reused elsewhere;
 * splitting four short components across four files would make the screen
 * harder to read, not easier.
 */

import { Badge } from '../ui'
import { Link } from 'react-router-dom'
import { relativeTime } from '../../services/staffDashboardService'

const SEVERITY_TONE = {
  LOW: 'badge--muted',
  MEDIUM: 'badge--info',
  HIGH: 'badge--warning',
  CRITICAL: 'badge--danger',
}

/**
 * Open security alerts.
 *
 * Triage buttons are rendered from `canTriage`, which the backend publishes.
 * A valet operator may *see* an alert but not close it — acknowledgement and
 * resolution are manager acts. Showing a button that returns 403 would tell an
 * operator the action is available and then refuse them in front of a customer.
 */
export function AlertList({ alerts, canTriage, onAcknowledge, onResolve }) {
  if (!alerts?.length) return null

  return (
    <ul className="valet-alerts">
      {alerts.map((alert) => (
        <li key={alert.id} className="valet-alerts__item">
          <div className="valet-alerts__body">
            <div className="valet-alerts__head">
              <Badge tone={SEVERITY_TONE[alert.severity] ?? 'badge--muted'}>
                {alert.severity}
              </Badge>
              <strong>{alert.title}</strong>
              <Badge tone="badge--muted">{alert.status}</Badge>
            </div>
            <p className="valet-alerts__reason">{alert.reason}</p>
            <p className="valet-alerts__meta muted">
              {alert.alert_type} · {relativeTime(alert.occurred_at) ?? 'time unknown'}
              {alert.acknowledged_by_email ? ` · seen by ${alert.acknowledged_by_email}` : ''}
            </p>
          </div>

          {(canTriage || alert.custody_session_id || alert.custody_id) && (
            <div className="valet-alerts__actions">
              {(alert.custody_session_id || alert.custody_id) && (
                <Link
                  className="btn btn--sm btn--ghost"
                  to={`/tracking/${alert.custody_session_id || alert.custody_id}`}
                  title="View this vehicle on the tracking map"
                >
                  View location
                </Link>
              )}
              {canTriage && alert.status === 'OPEN' && (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  onClick={() => onAcknowledge(alert)}
                >
                  Acknowledge
                </button>
              )}
              {canTriage && (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  onClick={() => onResolve(alert)}
                >
                  Resolve
                </button>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * Condition state per vehicle.
 *
 * The distinction that matters here is between *unverified* findings and
 * confirmed damage. An unverified finding is what the detector thought it saw;
 * calling it damage would overstate what the algorithm established, and the
 * operator is the person who has to go and look.
 */
export function InspectionTable({ rows, onView }) {
  if (!rows?.length) return null

  return (
    <div className="valet-table" role="table">
      <div className="valet-table__row valet-table__row--head" role="row">
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Drop-off</span>
        <span role="columnheader">Pickup</span>
        <span role="columnheader">Findings</span>
        <span role="columnheader">Comparison</span>
      </div>

      {rows.map((row) => (
        <div key={row.custody_session_id} className="valet-table__row" role="row">
          <span role="cell">
            <strong>{row.vehicle?.plate_number ?? '—'}</strong>
          </span>

          <span role="cell">
            {row.drop_off_captured ? (
              <Badge tone="badge--success">{row.drop_off_photo_count} photos</Badge>
            ) : (
              <Badge tone="badge--muted">Not captured</Badge>
            )}
          </span>

          <span role="cell">
            {row.pickup_captured ? (
              <Badge tone="badge--success">{row.pickup_photo_count} photos</Badge>
            ) : (
              // The actionable one: a car handed back with no pickup photos
              // cannot be compared, which is how disputes become unresolvable.
              <Badge tone="badge--warning">Needs photos</Badge>
            )}
          </span>

          <span role="cell">
            {row.unverified_detections > 0 ? (
              <Badge tone="badge--warning">
                {row.unverified_detections} potential
              </Badge>
            ) : (
              <Badge tone="badge--muted">None flagged</Badge>
            )}
          </span>

          <span role="cell">
            {row.comparison_ready ? (
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => onView(row)}
              >
                View comparison
              </button>
            ) : (
              <span className="valet-table__muted">Both sets required</span>
            )}
          </span>
        </div>
      ))}
    </div>
  )
}

/**
 * The retrieval queue: cars a valet still has to go and fetch.
 *
 * Renders the same rows the customer's own dashboard quotes, from the same
 * endpoint, so the two can never disagree about how long a car will take.
 */
export function QueueList({ items, onDispatch }) {
  if (!items?.length) return null

  return (
    <div className="valet-table" role="table">
      <div className="valet-table__row valet-table__row--head" role="row">
        <span role="columnheader">#</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Zone</span>
        <span role="columnheader">Requested</span>
        <span role="columnheader">Estimate</span>
        <span role="columnheader">Action</span>
      </div>

      {items.map((item) => (
        <div key={item.custody_session_id} className="valet-table__row" role="row">
          <span role="cell" className="valet-table__position">
            {item.queue_position}
          </span>
          <span role="cell">
            <strong>{item.vehicle?.plate_number ?? '—'}</strong>
            <span className="valet-table__muted">
              {[item.vehicle?.make, item.vehicle?.model].filter(Boolean).join(' ')}
            </span>
          </span>
          <span role="cell">{item.zone?.code ?? '—'}</span>
          <span role="cell" className="valet-table__muted">
            {relativeTime(item.requested_at) ?? '—'}
          </span>
          <span role="cell">
            {item.estimated_minutes != null ? `${item.estimated_minutes} min` : '—'}
          </span>
          <span role="cell">
            {item.in_progress ? (
              <Badge tone="badge--warning">Being retrieved</Badge>
            ) : (
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => onDispatch(item)}
              >
                Start retrieval
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Zone occupancy, so an operator can see where there is room. */
export function ZoneStrip({ zones }) {
  if (!zones?.length) return null

  return (
    <ul className="valet-zones">
      {zones.map((zone) => (
        <li key={zone.id} className="valet-zones__item">
          <div className="valet-zones__head">
            <strong>{zone.code}</strong>
            <span className="muted">{zone.name}</span>
          </div>
          <div className="valet-zones__bar" aria-hidden="true">
            <span
              className="valet-zones__fill"
              style={{ width: `${Math.min(100, zone.occupancy_percent)}%` }}
            />
          </div>
          <div className="valet-zones__meta">
            <span>
              {zone.current_occupancy} / {zone.capacity}
            </span>
            {zone.parkable ? (
              <Badge tone="badge--success">{zone.available_slots} free</Badge>
            ) : (
              <Badge tone="badge--warning">Not parkable</Badge>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
