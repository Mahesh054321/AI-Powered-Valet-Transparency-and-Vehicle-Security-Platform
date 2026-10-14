/**
 * The retrieval-time estimate card.
 *
 * Shows the three things a person waiting for a car actually needs:
 *
 *     Estimated Retrieval : 8 minutes
 *     Queue Position      : 2
 *     Recommended Dispatch: Now
 *
 * Two honesty requirements shape the markup, and both are deliberate rather than
 * decorative:
 *
 * - **The synthetic-data notice is always rendered.** The brief requires it, and
 *   more practically: this card is the single most screenshot-able number in the
 *   product. If the caveat can be cropped out, "Estimated Retrieval: 8 minutes"
 *   becomes an accuracy claim about a model that has never seen a real yard.
 * - **The estimate is shown as a range when the backend supplies one.** A single
 *   figure implies precision this model does not have. The point estimate is
 *   still the headline, because that is what a customer plans around, but the
 *   bounds sit next to it.
 *
 * The per-feature contributions are listed underneath so the number can be
 * argued with: "4th in the queue adds 3.1 minutes" is checkable against the yard
 * in a way that a bare "13 minutes" is not.
 */

import { Badge } from '../ui'
import { RETRIEVAL_PROTOTYPE_NOTICE } from '../../utils/constants'
import {
  formatWait,
  retrievalProvenance,
  topContributions,
} from '../../services/retrievalService'

/**
 * @param {object} props
 * @param {object|null} props.estimate  A prediction payload from the API.
 * @param {boolean} [props.loading]     Render a placeholder while fetching.
 * @param {Function} [props.onRequest]  Ask for an estimate when none exists.
 * @param {boolean} [props.busy]        A request is in flight.
 */
export function RetrievalEstimateCard({ estimate = null, loading = false, onRequest, busy = false }) {
  if (loading) {
    return (
      <section className="retrieval-card retrieval-card--loading">
        <h3>Retrieval estimate</h3>
        <p className="muted">Working out how long the vehicle will take…</p>
      </section>
    )
  }

  if (!estimate) {
    // Absent means "nobody has asked for an estimate", not "the wait is zero".
    // An empty card would read as an immediate pickup.
    return (
      <section className="retrieval-card retrieval-card--absent">
        <h3>Retrieval estimate</h3>
        <p className="muted">
          No estimate yet. One is produced when a retrieval is requested, from
          the live queue length, staffing and zone occupancy.
        </p>
        {onRequest && (
          <button type="button" className="btn btn--secondary" onClick={onRequest} disabled={busy}>
            {busy ? 'Estimating…' : 'Estimate retrieval time'}
          </button>
        )}
        <p className="retrieval-card__notice">{RETRIEVAL_PROTOTYPE_NOTICE}</p>
      </section>
    )
  }

  const wait = formatWait(estimate)
  const position = estimate.queue_position
  const dispatch = estimate.recommended_dispatch ?? 'Now'
  const provenance = retrievalProvenance(estimate, RETRIEVAL_PROTOTYPE_NOTICE)
  const factors = topContributions(estimate)
  const interval =
    estimate.min_wait_minutes !== undefined && estimate.max_wait_minutes !== undefined
      ? `${estimate.min_wait_minutes}–${estimate.max_wait_minutes} min`
      : null

  return (
    <section className="retrieval-card">
      <header className="retrieval-card__head">
        <h3>Retrieval estimate</h3>
        <Badge tone="badge--info">Synthetic-data model</Badge>
      </header>

      <dl className="retrieval-card__figures">
        <div className="retrieval-card__figure retrieval-card__figure--primary">
          <dt>Estimated Retrieval</dt>
          <dd>
            {wait ?? '—'}
            {interval && <span className="retrieval-card__interval">({interval})</span>}
          </dd>
        </div>
        <div className="retrieval-card__figure">
          <dt>Queue Position</dt>
          <dd>{position === undefined || position === null ? '—' : position}</dd>
        </div>
        <div className="retrieval-card__figure">
          <dt>Recommended Dispatch</dt>
          <dd>{dispatch}</dd>
        </div>
      </dl>

      {estimate.reason && <p className="retrieval-card__reason">{estimate.reason}</p>}

      {factors.length > 0 && (
        <div className="retrieval-card__factors">
          <h4>What moved the estimate</h4>
          <ul>
            {factors.map(({ feature, minutes }) => (
              <li key={feature}>
                <span className="retrieval-card__factor-name">{labelFor(feature)}</span>
                <span className="retrieval-card__factor-value">
                  {minutes > 0 ? '+' : ''}
                  {minutes.toFixed(1)} min
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="retrieval-card__meta">
        {provenance.modelVersion && <span>model {provenance.modelVersion}</span>}
        {estimate.confidence !== undefined && estimate.confidence !== null && (
          <span>confidence {Number(estimate.confidence).toFixed(2)}</span>
        )}
        {estimate.history_note && <span>{estimate.history_note}</span>}
      </div>

      <p className="retrieval-card__notice">{provenance.disclaimer}</p>
      {provenance.dataBasis && (
        <p className="retrieval-card__basis">Data basis: {provenance.dataBasis}</p>
      )}

      {onRequest && (
        <button type="button" className="btn btn--secondary" onClick={onRequest} disabled={busy}>
          {busy ? 'Estimating…' : 'Recalculate estimate'}
        </button>
      )}
    </section>
  )
}

/**
 * A readable name for a feature key.
 *
 * The backend sends `feature_columns` with labels on the model endpoint, but a
 * prediction payload carries only the raw keys, so the card maps the handful it
 * is likely to show rather than depending on a second request.
 */
function labelFor(feature) {
  const labels = {
    queue_size: 'Queue size',
    queue_position: 'Queue position',
    active_valets: 'Valets on duty',
    zone_capacity: 'Zone capacity',
    zone_occupancy_percent: 'Zone occupancy',
    hour_sin: 'Time of day',
    hour_cos: 'Time of day',
    dow_sin: 'Day of week',
    dow_cos: 'Day of week',
    historical_zone_mean_minutes: 'Zone historical average',
    vehicle_flagged: 'Vehicle flagged',
  }
  return labels[feature] ?? feature.replace(/_/g, ' ')
}

export default RetrievalEstimateCard
