/**
 * One damage finding, with the evidence and the controls to verify it.
 *
 * The wording is not decorative. A finding nobody has reviewed is labelled
 * "Potential Damage" and says so loudly, because these come from classical
 * image differencing rather than a trained classifier. Only a manager or
 * administrator can move it to Confirmed or Rejected, and the backend enforces
 * that independently of whether these buttons render.
 */

import { useState } from 'react'
import { Badge } from '../ui'
import {
  DAMAGE_SEVERITY_LABELS,
  DAMAGE_SEVERITY_TONES,
  DAMAGE_TYPE_LABELS,
  VERIFICATION_STATUS,
  VERIFICATION_STATUS_LABELS,
  VERIFICATION_STATUS_TONES,
  angleLabel,
} from '../../utils/constants'

/** Confidence as a percentage plus a tone. Never rendered as a probability. */
function ConfidenceMeter({ value }) {
  const percent = Math.round((value ?? 0) * 100)
  const tone = percent >= 70 ? 'meter--high' : percent >= 40 ? 'meter--mid' : 'meter--low'

  return (
    <div className="meter" title={`Ranking signal: ${percent}%`}>
      <div className="meter__track">
        <div className={`meter__fill ${tone}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="meter__value">{percent}%</span>
    </div>
  )
}

export function DamageCard({ detection, canVerify, onVerify, busy }) {
  const [notes, setNotes] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState(null)

  const label = detection.presentation_label ?? 'Potential Damage'
  const status = detection.verification_status ?? VERIFICATION_STATUS.UNVERIFIED
  const isPotential = status === VERIFICATION_STATUS.UNVERIFIED

  async function submit(nextStatus) {
    setError(null)
    try {
      await onVerify(detection.id, { status: nextStatus, notes: notes.trim() || undefined })
      setNotes('')
    } catch (cause) {
      setError(cause?.message ?? 'Verification failed.')
    }
  }

  return (
    <article className={`finding ${isPotential ? 'finding--potential' : ''}`}>
      <header className="finding__header">
        <div>
          <h4 className="finding__title">
            <span className={`finding__flag ${isPotential ? 'finding__flag--warn' : ''}`}>
              {label}
            </span>
            {DAMAGE_TYPE_LABELS[detection.damage_type] ?? detection.damage_type}
          </h4>
          <p className="finding__where muted">
            {detection.region?.replace(/_/g, ' ') ?? 'Unknown region'}
            {detection.angle ? ` · ${angleLabel(detection.angle)} view` : ''}
          </p>
        </div>
        <div className="finding__badges">
          <Badge tone={DAMAGE_SEVERITY_TONES[detection.severity] ?? 'badge--muted'}>
            {DAMAGE_SEVERITY_LABELS[detection.severity] ?? detection.severity}
          </Badge>
          <Badge tone={VERIFICATION_STATUS_TONES[status] ?? 'badge--muted'}>
            {VERIFICATION_STATUS_LABELS[status] ?? status}
          </Badge>
        </div>
      </header>

      <div className="finding__metrics">
        <div>
          <span className="finding__metric-label">Region confidence</span>
          <ConfidenceMeter value={detection.confidence} />
        </div>
        <div>
          <span className="finding__metric-label">Label confidence</span>
          <ConfidenceMeter value={detection.type_confidence} />
          <small className="muted">
            Naming the defect is a weaker claim than detecting the change.
          </small>
        </div>
      </div>

      {isPotential && (
        <p className="finding__caveat">
          Flagged by classical OpenCV image differencing, not a trained damage
          classifier. This is a candidate for review, not a confirmed defect.
        </p>
      )}

      {(detection.evidence_url || detection.comparison_url) && (
        <div className="finding__images">
          {detection.evidence_url && (
            <figure>
              <img src={detection.evidence_url} alt="Detected region, drop-off beside pickup" />
              <figcaption>Detected region</figcaption>
            </figure>
          )}
          {detection.comparison_url && (
            <figure>
              <img src={detection.comparison_url} alt="Full before, after and difference comparison" />
              <figcaption>Comparison</figcaption>
            </figure>
          )}
        </div>
      )}

      <details className="finding__reasoning" open={expanded} onToggle={(e) => setExpanded(e.target.open)}>
        <summary>Why this was flagged</summary>
        <p>{detection.detection_reasoning}</p>
        {detection.algorithm_params?.region_statistics && (
          <dl className="finding__stats">
            {Object.entries(detection.algorithm_params.region_statistics).map(([key, value]) => (
              <div key={key}>
                <dt>{key.replace(/_/g, ' ')}</dt>
                <dd>{typeof value === 'number' ? value.toFixed(3) : String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </details>

      {detection.verification_notes && (
        <p className="finding__notes">
          <strong>Reviewer note:</strong> {detection.verification_notes}
        </p>
      )}

      {canVerify && (
        <div className="finding__verify">
          <input
            type="text"
            value={notes}
            maxLength={500}
            placeholder="Reviewer note (optional, 500 chars)"
            onChange={(event) => setNotes(event.target.value)}
            aria-label="Verification note"
          />
          <div className="finding__verify-actions">
            <button
              type="button"
              className="btn btn--danger"
              disabled={busy}
              onClick={() => submit(VERIFICATION_STATUS.CONFIRMED)}
            >
              Confirm damage
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => submit(VERIFICATION_STATUS.REJECTED)}
            >
              Reject as false positive
            </button>
            {!isPotential && (
              <button
                type="button"
                className="btn btn--ghost"
                disabled={busy}
                onClick={() => submit(VERIFICATION_STATUS.UNVERIFIED)}
              >
                Reopen
              </button>
            )}
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}
    </article>
  )
}

export default DamageCard
