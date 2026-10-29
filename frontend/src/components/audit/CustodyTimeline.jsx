/**
 * Chronological custody timeline.
 *
 * The view the whole project exists to answer: what happened to this vehicle,
 * in what order, and who did it.
 *
 * **Oldest first, and that is deliberate.** The log viewer on the same screen
 * runs newest-first because it is a worklist. A timeline is a narrative —
 * checked in, parked, requested, handed back — and reversing it would make the
 * reader reconstruct the order mentally on every visit.
 *
 * **Gaps are shown, not hidden.** `lifecycle_progress` reports all fourteen
 * lifecycle events with a boolean each, so a custody with no pickup photographs
 * says so explicitly. A timeline that only rendered what happened would let a
 * missing step pass as a step that never needed to happen.
 *
 * **Scope is labelled.** Some entries are not stamped with this custody —
 * ticket and vehicle registration predate the session, and a login belongs to a
 * person rather than a car. The backend labels each entry, and the gutter shows
 * it, so nothing looks like part of the custody that is not.
 */

import { Badge, Panel, Spinner, ErrorNotice } from '../ui'
import {
  actorLabel,
  formatEntryTime,
  groupEventsByScope,
  summariseEntry,
} from '../../services/auditService'

/** Tone per outcome, so a denied action is visible without reading the text. */
const OUTCOME_TONE = {
  SUCCESS: 'badge--success',
  FAILURE: 'badge--danger',
  DENIED: 'badge--warning',
}

const SCOPE_LABEL = {
  custody: 'custody',
  provenance: 'before custody',
  login: 'login',
}

/**
 * The fourteen-stage progress strip.
 *
 * Rendered as a checklist rather than a percentage: "8 of 14 complete" invites
 * the reader to treat the missing six as optional, when several of them —
 * pickup images, damage check — are exactly the ones worth asking about.
 */
export function LifecycleProgress({ progress, missing = [] }) {
  if (!progress) return null
  const entries = Object.entries(progress)
  return (
    <div className="timeline-progress">
      <p className="timeline-progress__heading">Lifecycle coverage</p>
      <ul className="timeline-progress__list">
        {entries.map(([action, happened]) => (
          <li
            key={action}
            className={
              happened
                ? 'timeline-progress__item timeline-progress__item--done'
                : 'timeline-progress__item timeline-progress__item--missing'
            }
          >
            <span aria-hidden="true">{happened ? '✓' : '○'}</span>
            <span className="timeline-progress__action">{action.replaceAll('_', ' ').toLowerCase()}</span>
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <p className="timeline-progress__note">
          {missing.length} lifecycle {missing.length === 1 ? 'step' : 'steps'} not
          recorded for this custody.
        </p>
      )}
    </div>
  )
}

/** One entry on the timeline. */
export function TimelineEvent({ event }) {
  if (!event) return null
  return (
    <li className={`timeline-event timeline-event--${event.scope || 'custody'}`}>
      <div className="timeline-event__gutter">
        <span className="timeline-event__dot" aria-hidden="true" />
        <time className="timeline-event__time" dateTime={event.occurred_at || undefined}>
          {formatEntryTime(event.occurred_at)}
        </time>
      </div>
      <div className="timeline-event__body">
        <div className="timeline-event__head">
          <span className="timeline-event__action">{event.label || event.action}</span>
          <Badge tone={OUTCOME_TONE[event.outcome] || 'badge--neutral'}>{event.outcome}</Badge>
          {event.scope && event.scope !== 'custody' && (
            <span className="timeline-event__scope">{SCOPE_LABEL[event.scope] || event.scope}</span>
          )}
        </div>
        <p className="timeline-event__summary">{summariseEntry(event)}</p>
        <dl className="timeline-event__meta">
          <div>
            <dt>actor</dt>
            <dd>{actorLabel(event)}</dd>
          </div>
          <div>
            <dt>entity</dt>
            <dd className="mono">{event.entity_type}</dd>
          </div>
          {event.ip_address && (
            <div>
              <dt>ip</dt>
              <dd className="mono">{event.ip_address}</dd>
            </div>
          )}
          {event.request_id && (
            <div>
              <dt>request</dt>
              <dd className="mono">{event.request_id.slice(0, 12)}</dd>
            </div>
          )}
        </dl>
        {event.metadata && Object.keys(event.metadata).length > 0 && (
          <details className="timeline-event__metadata">
            <summary>metadata</summary>
            <pre>{JSON.stringify(event.metadata, null, 2)}</pre>
          </details>
        )}
        {event.failure_reason && (
          <p className="timeline-event__failure">Reason: {event.failure_reason}</p>
        )}
      </div>
    </li>
  )
}

/**
 * The full timeline for one custody.
 *
 * @param {object} props
 * @param {object} [props.timeline] the timeline payload from the API
 * @param {boolean} [props.isLoading]
 * @param {*} [props.error]
 * @param {Function} [props.onRetry]
 */
export function CustodyTimeline({ timeline, isLoading, error, onRetry }) {
  if (isLoading) return <Spinner label="Loading timeline…" />
  if (error) return <ErrorNotice error={error} onRetry={onRetry} />
  if (!timeline) return null

  const groups = groupEventsByScope(timeline.events)
  const contextCount = groups.provenance.length + groups.login.length

  return (
    <Panel
      title="Custody timeline"
      subtitle={
        timeline.vehicle
          ? `${timeline.vehicle.plate_number} · ${timeline.vehicle.make || ''} ${
              timeline.vehicle.model || ''
            }`.trim()
          : 'Custody session'
      }
    >
      <div className="timeline">
        <dl className="timeline__facts">
          <div>
            <dt>status</dt>
            <dd>{timeline.status}</dd>
          </div>
          <div>
            <dt>zone</dt>
            <dd>{timeline.zone?.code ?? '—'}</dd>
          </div>
          <div>
            <dt>dropped off</dt>
            <dd>{formatEntryTime(timeline.dropped_off_at) || '—'}</dd>
          </div>
          <div>
            <dt>picked up</dt>
            <dd>{formatEntryTime(timeline.picked_up_at) || 'not yet'}</dd>
          </div>
        </dl>

        {timeline.had_security_incident && (
          <p className="timeline__incident">
            A geofence breach was recorded during this custody.
          </p>
        )}

        <LifecycleProgress
          progress={timeline.lifecycle_progress}
          missing={timeline.lifecycle_missing}
        />

        {contextCount > 0 && timeline.scope_note && (
          <p className="timeline__scope-note">{timeline.scope_note}</p>
        )}

        <ol className="timeline__events">
          {(timeline.events || []).map((event) => (
            <TimelineEvent key={event.id} event={event} />
          ))}
        </ol>

        {(timeline.events || []).length === 0 && (
          <p className="timeline__empty">No audit entries are recorded for this custody.</p>
        )}
      </div>
    </Panel>
  )
}

export default CustodyTimeline
