/**
 * One security alert, with its reasoning and the triage controls.
 *
 * The `reason` is rendered verbatim from the engine rather than paraphrased: the
 * whole point of an explainable rule set is that a manager can read the sentence
 * the system actually used and decide for themselves. The `evidence` block is
 * shown behind a disclosure so the inputs behind that sentence are one click
 * away without dominating the list.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge } from '../ui'
import {
  ALERT_SEVERITY_TONES,
  ALERT_STATUS_LABELS,
  ALERT_STATUS_TONES,
  alertTypeLabel,
} from '../../utils/constants'

function formatWhen(iso) {
  if (!iso) return 'unknown time'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function evidenceLabel(key) {
  const label = key.replace(/([A-Z])/g, ' $1').replace(/[_-]+/g, ' ').trim()
  return label.charAt(0).toUpperCase() + label.slice(1)
}

function EvidenceValue({ value }) {
  if (value == null) return <span className="muted">Not reported</span>
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'

  if (Array.isArray(value)) {
    return (
      <ul className="alert-evidence__list">
        {value.map((item, index) => (
          <li key={index}><EvidenceValue value={item} /></li>
        ))}
      </ul>
    )
  }

  if (typeof value === 'object') {
    return (
      <dl className="alert-evidence">
        {Object.entries(value).map(([key, item]) => (
          <div className="alert-evidence__row" key={key}>
            <dt>{evidenceLabel(key)}</dt>
            <dd><EvidenceValue value={item} /></dd>
          </div>
        ))}
      </dl>
    )
  }

  return String(value)
}

/**
 * @param {object} props
 * @param {object} props.alert             The alert as returned by the API.
 * @param {boolean} [props.canTriage]      True for managers and administrators.
 * @param {boolean} [props.busy]           Disable the buttons while a request runs.
 * @param {Function} [props.onAcknowledge]
 * @param {Function} [props.onInvestigate]
 * @param {Function} [props.onResolve]     Called with `(alert, { dismiss })`.
 * @param {Function} [props.onReopen]
 */
export function AlertCard({
  alert,
  canTriage = false,
  busy = false,
  onAcknowledge,
  onInvestigate,
  onResolve,
  onReopen,
}) {
  const [showEvidence, setShowEvidence] = useState(false)
  const [notes, setNotes] = useState('')
  const [editing, setEditing] = useState(false)

  const isOpen = alert.status === 'OPEN' || alert.status === 'ACKNOWLEDGED' || alert.status === 'INVESTIGATING' || alert.status === 'REOPENED'
  const confidencePct = alert.confidence != null ? Math.round(Number(alert.confidence) * 100) : null

  return (
    <article className="alert-card">
      <header className="alert-card__head">
        <div className="alert-card__titles">
          <strong>{alert.title}</strong>
          <span className="muted">
            {alertTypeLabel(alert.alert_type)} · {formatWhen(alert.occurred_at)}
          </span>
        </div>
        <div className="alert-card__badges">
          <Badge tone={ALERT_SEVERITY_TONES[alert.severity] ?? 'badge--muted'}>{alert.severity}</Badge>
          <Badge tone={ALERT_STATUS_TONES[alert.status] ?? 'badge--muted'}>
            {ALERT_STATUS_LABELS[alert.status] ?? alert.status}
          </Badge>
        </div>
      </header>

      <p className="alert-card__reason">{alert.reason}</p>

      {((alert.custody_session_id || alert.custody_id) || (alert.vehicle_id || alert.vehicle?.id)) && (
        <div className="alert-card__links">
          {(alert.custody_session_id || alert.custody_id) && (
            <Link
              to={`/tracking/${alert.custody_session_id || alert.custody_id}`}
              className="btn btn--ghost btn--tiny"
              title="View on Live Tracking Map"
            >
              View on Map
            </Link>
          )}
          {(alert.vehicle_id || alert.vehicle?.id) && (
            <Link
              to={`/vehicles/${alert.vehicle_id || alert.vehicle?.id}`}
              className="btn btn--ghost btn--tiny"
              title="View Vehicle Profile"
            >
              View Vehicle
            </Link>
          )}
        </div>
      )}

      <dl className="alert-card__meta">
        {confidencePct != null && (
          <div>
            <dt>Confidence</dt>
            <dd>{confidencePct}%</dd>
          </div>
        )}
        {alert.acknowledged_by_email && (
          <div>
            <dt>Acknowledged</dt>
            <dd>
              {alert.acknowledged_by_email} · {formatWhen(alert.acknowledged_at)}
            </dd>
          </div>
        )}
        {alert.resolved_by_email && (
          <div>
            <dt>{alert.status === 'DISMISSED' ? 'Dismissed' : 'Resolved'}</dt>
            <dd>
              {alert.resolved_by_email} · {formatWhen(alert.resolved_at)}
            </dd>
          </div>
        )}
      </dl>

      {alert.resolution_notes && (
        <p className="alert-card__notes">
          <span className="muted">Notes:</span> {alert.resolution_notes}
        </p>
      )}

      {alert.evidence && (
        <div className="alert-card__evidence">
          <button
            type="button"
            className="btn btn--ghost btn--small"
            onClick={() => setShowEvidence((value) => !value)}
            aria-expanded={showEvidence}
          >
            {showEvidence ? 'Hide' : 'Show'} the inputs behind this
          </button>
          {showEvidence && (
            <div className="alert-card__evidence-body">
              <EvidenceValue value={alert.evidence} />
            </div>
          )}
        </div>
      )}

      {canTriage && isOpen && (
        <footer className="alert-card__actions">
          {editing ? (
            <form
              className="alert-card__resolve"
              onSubmit={(event) => {
                event.preventDefault()
                onResolve?.(alert, { notes: notes.trim() || undefined, dismiss: false })
                setEditing(false)
                setNotes('')
              }}
            >
              <input
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="What did you find?"
                maxLength={1000}
                aria-label="Resolution notes"
              />
              <button type="submit" className="btn btn--primary btn--small" disabled={busy}>
                Save
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => setEditing(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </form>
          ) : (
            <>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => onAcknowledge?.(alert)}
                disabled={busy || alert.status !== 'OPEN'}
                title={
                  alert.status === 'OPEN'
                    ? 'Record that a manager has seen this alert'
                    : 'Already acknowledged'
                }
              >
                Acknowledge
              </button>
              {onInvestigate && alert.status !== 'INVESTIGATING' && (
                <button
                  type="button"
                  className="btn btn--ghost btn--small"
                  onClick={() => onInvestigate(alert)}
                  disabled={busy}
                  title="Mark this alert as currently undergoing staff investigation"
                >
                  Investigate
                </button>
              )}
              <button
                type="button"
                className="btn btn--small"
                onClick={() => setEditing(true)}
                disabled={busy}
              >
                Resolve…
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => onResolve?.(alert, { dismiss: true })}
                disabled={busy}
                title="Record this as a false positive rather than a resolved incident"
              >
                Dismiss as false positive
              </button>
            </>
          )}
        </footer>
      )}

      {canTriage && !isOpen && onReopen && (
        <footer className="alert-card__actions">
          <button
            type="button"
            className="btn btn--ghost btn--small"
            onClick={() => onReopen(alert)}
            disabled={busy}
            title="Reopen this resolved or dismissed alert"
          >
            Reopen alert
          </button>
        </footer>
      )}
    </article>
  )
}
