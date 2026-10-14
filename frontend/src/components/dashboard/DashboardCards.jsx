/**
 * The customer dashboard's cards.
 *
 * Every card goes through `DashCard`, which is what keeps the empty and error
 * states consistent. The rule it enforces: **a card never renders as blank.**
 * A card with no data shows the backend's own reason in words, because blank is
 * indistinguishable from "everything is fine" — and on a security card that is
 * the dangerous direction to be ambiguous in.
 *
 * The five cards the brief names are here: Vehicle Status, Security Status,
 * Current Location, Estimated Retrieval and Condition Status.
 */

import { Badge } from '../ui'
import { formatTime } from '../../utils/format'
import { RETRIEVAL_PROTOTYPE_NOTICE } from '../../utils/constants'
import {
  custodyLabel,
  emptyReason,
  hasData,
  relativeTime,
  securityLabel,
  securityTone,
} from '../../services/dashboardService'

/** One dashboard card, with its empty and unavailable states handled once. */
function DashCard({ title, section, badge, children, footer }) {
  const state = section?.state

  return (
    <article className={`dash-card dash-card--${state ?? 'empty'}`}>
      <header className="dash-card__head">
        <h3>{title}</h3>
        {badge}
      </header>

      {state === 'ok' ? (
        children
      ) : (
        <p className="dash-card__empty">{emptyReason(section)}</p>
      )}

      {footer}
    </article>
  )
}

function Row({ label, value, mono = false }) {
  return (
    <div className="dash-card__row">
      <span className="dash-card__label">{label}</span>
      <span className={`dash-card__value${mono ? ' dash-card__value--mono' : ''}`}>
        {value ?? '—'}
      </span>
    </div>
  )
}

/** Date portion only, for a card row where a full timestamp is too wide. */
function formatDateOnly(value) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function VehicleStatusCard({ vehicle, custody }) {
  return (
    <DashCard
      title="Vehicle Status"
      section={vehicle}
      badge={
        hasData(custody) ? (
          <Badge tone="badge--info">{custodyLabel(custody.status)}</Badge>
        ) : null
      }
    >
      <p className="dash-card__headline">
        {vehicle.plate_number}
        {vehicle.make ? ` · ${vehicle.make}` : ''}
        {vehicle.model ? ` ${vehicle.model}` : ''}
      </p>
      <Row label="Custody status" value={custodyLabel(custody?.status)} />
      <Row label="Checked in" value={relativeTime(custody?.checked_in_at)} />
      <Row label="Ticket" value={custody?.ticket_code} mono />
      <Row label="Vehicle record" value={vehicle.status} />
    </DashCard>
  )
}

export function SecurityStatusCard({ security }) {
  return (
    <DashCard
      title="Security Status"
      section={security}
      badge={
        hasData(security) ? (
          <Badge tone={securityTone(security.status)}>
            {securityLabel(security.status)}
          </Badge>
        ) : null
      }
    >
      <p className="dash-card__summary">{security.summary}</p>
      <Row label="Movement verdict" value={security.movement_status ?? 'No telemetry'} />
      <Row
        label="Open alerts"
        value={security.open_alert_count ?? 0}
      />
      {security.highest_severity && (
        <Row label="Highest severity" value={security.highest_severity} />
      )}
      {security.alerts?.length > 0 && (
        <ul className="dash-card__list">
          {security.alerts.slice(0, 3).map((alert) => (
            <li key={alert.id}>
              <Badge tone={securityTone(alert.severity === 'CRITICAL' ? 'BREACH' : 'ATTENTION')}>
                {alert.severity}
              </Badge>
              <span>{alert.alert_type}</span>
              <span className="dash-card__muted">{alert.status}</span>
            </li>
          ))}
        </ul>
      )}
    </DashCard>
  )
}

export function CurrentLocationCard({ location, zone }) {
  const coordinates =
    hasData(location) && location.latitude != null
      ? `${Number(location.latitude).toFixed(5)}, ${Number(location.longitude).toFixed(5)}`
      : null

  return (
    <DashCard
      title="Current Location"
      section={location}
      badge={
        hasData(zone) ? <Badge tone="badge--muted">{zone.code}</Badge> : null
      }
      footer={
        // Provenance is not optional here: a simulated track must never read as
        // a hardware position.
        location?.telemetry_note ? (
          <p className="dash-card__provenance">{location.telemetry_note}</p>
        ) : null
      }
    >
      <Row label="Parking zone" value={hasData(zone) ? `${zone.code} — ${zone.name}` : null} />
      <Row label="Coordinates" value={coordinates} mono />
      <Row label="Last fix" value={relativeTime(location.recorded_at)} />
      <Row label="Movement" value={location.movement_status} />
      {hasData(zone) && zone.capacity != null && (
        <Row
          label="Zone occupancy"
          value={`${zone.current_occupancy} / ${zone.capacity}`}
        />
      )}
    </DashCard>
  )
}

export function EstimatedRetrievalCard({ retrieval }) {
  const minutes = retrieval?.estimated_minutes
  const interval =
    retrieval?.min_wait_minutes != null && retrieval?.max_wait_minutes != null
      ? `${retrieval.min_wait_minutes}–${retrieval.max_wait_minutes} min`
      : null

  return (
    <DashCard
      title="Estimated Retrieval"
      section={retrieval}
      badge={
        hasData(retrieval) ? (
          <Badge tone="badge--info">Synthetic-data model</Badge>
        ) : null
      }
      footer={
        hasData(retrieval) ? (
          // Always rendered: this is the most screenshot-able number in the
          // product, and a cropped caveat turns it into an accuracy claim.
          <p className="dash-card__notice">
            {retrieval.disclaimer ?? RETRIEVAL_PROTOTYPE_NOTICE}
          </p>
        ) : null
      }
    >
      <p className="dash-card__big">
        {minutes}
        <span className="dash-card__unit">minutes</span>
        {interval && <span className="dash-card__interval">({interval})</span>}
      </p>
      <Row label="Queue position" value={retrieval.queue_position} />
      <Row label="Recommended dispatch" value={retrieval.recommended_dispatch} />
      <Row label="Ready by" value={formatTime(retrieval.predicted_ready_at)} />
      <Row label="Ready date" value={formatDateOnly(retrieval.predicted_ready_at)} />
      <Row
        label="Estimate generated"
        value={relativeTime(retrieval.prediction?.requested_at)}
      />
    </DashCard>
  )
}

export function ConditionStatusCard({ condition }) {
  return (
    <DashCard
      title="Condition Status"
      section={condition}
      badge={
        hasData(condition) ? (
          <Badge
            tone={
              condition.unverified_count > 0
                ? 'badge--warning'
                : condition.confirmed_count > 0
                  ? 'badge--danger'
                  : 'badge--success'
            }
          >
            {condition.status === 'REVIEW'
              ? 'Needs review'
              : condition.status === 'DAMAGE_CONFIRMED'
                ? 'Damage confirmed'
                : 'No damage'}
          </Badge>
        ) : null
      }
      footer={
        hasData(condition) && condition.detector_disclaimer ? (
          // Carried through verbatim from the backend: the detector is classical
          // image differencing, not a trained classifier.
          <p className="dash-card__notice">{condition.detector_disclaimer}</p>
        ) : null
      }
    >
      <p className="dash-card__summary">{condition.summary}</p>
      <Row label="Findings" value={condition.total_detections ?? 0} />
      <Row label="Awaiting confirmation" value={condition.unverified_count ?? 0} />
      <Row label="Confirmed" value={condition.confirmed_count ?? 0} />
      <Row
        label="Pickup inspection"
        value={condition.pickup_completed ? 'Captured' : 'Not yet'}
      />
    </DashCard>
  )
}
