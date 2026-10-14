/**
 * The prototype classifier's opinion on one GPS fix.
 *
 * Rendered *alongside* the rule verdict, never instead of it. The rule engine's
 * status is a measurement against a known boundary; this is an estimate from a
 * prototype trained on synthetic telemetry. Showing them side by side is the
 * point - a reviewer should be able to see when they disagree and judge for
 * themselves, which is also why the disagreement is stated rather than resolved
 * quietly in code.
 *
 * Two honesty requirements drive the markup:
 *
 * - the probability is always shown next to the label, because "suspicious at
 *   51%" and "suspicious at 99%" are not the same claim and collapsing them
 *   into one badge would hide the difference;
 * - the prototype notice is always rendered, so a cropped screenshot of this
 *   card cannot read as a production model's verdict.
 */

import { Badge } from '../ui'
import {
  ML_CLASSIFICATION,
  ML_PROTOTYPE_NOTICE,
  mlClassificationLabel,
  mlClassificationTone,
  mlProbabilityLabel,
} from '../../utils/constants'

/**
 * @param {object} props
 * @param {object|null} props.ml            The `ml` block from a fix, or null.
 * @param {string} [props.ruleStatus]       The rule engine's verdict, to contrast.
 * @param {boolean} [props.escalated]       True when ML raised the verdict.
 */
export function MlVerdictCard({ ml, ruleStatus = null, escalated = false }) {
  if (!ml) {
    // Absent means "no classifier ran", not "the classifier found nothing".
    // Saying so matters: an empty card would imply a clean bill of health.
    return (
      <div className="ml-verdict ml-verdict--absent">
        <p className="muted">
          No classifier verdict for this fix. A model artifact has not been
          trained, so the rule-based verdict above stands on its own.
        </p>
      </div>
    )
  }

  const suspicious = ml.classification === ML_CLASSIFICATION.SUSPICIOUS
  const factors = Array.isArray(ml.top_factors) ? ml.top_factors : []

  // Whether the classifier's opinion lines up with the verdict that was stored.
  // Both directions matter and are rendered differently:
  //
  // - the model was *more* concerned than the rules but stayed under the
  //   escalation threshold, so it changed nothing;
  // - the model was *less* concerned than the rules - a dissent against a
  //   measurement. This is the case a reviewer most needs to see stated, and it
  //   is also the one a naive implementation renders as silence.
  const RULE_RANK = { NORMAL: 0, WARNING: 1, SUSPICIOUS: 2, BREACH: 3 }
  const ruleRank = ruleStatus ? RULE_RANK[String(ruleStatus).toUpperCase()] : null
  const mlRank = suspicious ? RULE_RANK.SUSPICIOUS : RULE_RANK.NORMAL
  const dissenting = ruleRank != null && mlRank < ruleRank
  const underThreshold = ruleRank != null && mlRank > ruleRank

  return (
    <div className="ml-verdict">
      <header className="ml-verdict__head">
        <Badge tone={mlClassificationTone(ml.classification)}>
          {mlClassificationLabel(ml.classification)}
        </Badge>
        <span className="ml-verdict__probability">
          P(suspicious) <strong>{mlProbabilityLabel(ml.probability)}</strong>
        </span>
        {ml.severity && <Badge tone="badge--muted">{ml.severity}</Badge>}
      </header>

      {escalated && (
        <p className="ml-verdict__escalation">
          This classifier raised the verdict{ruleStatus ? ` from ${ruleStatus}` : ''}. It can never
          lower one: the rule-based measurement stays authoritative.
        </p>
      )}

      {!escalated && dissenting && (
        <p className="ml-verdict__disagreement">
          The rule engine recorded {ruleStatus} and the classifier disagreed, rating this fix{' '}
          {mlProbabilityLabel(ml.probability)} suspicious. The rule verdict is what was stored: a
          prototype&apos;s dissent is not grounds to quiet a measurement, so any alert this raised
          still stands.
        </p>
      )}

      {!escalated && underThreshold && (
        <p className="ml-verdict__disagreement">
          The classifier was more concerned than the rules were, but at{' '}
          {mlProbabilityLabel(ml.probability)} it stayed under the escalation threshold, so the
          rule-based verdict of {ruleStatus} stands unchanged.
        </p>
      )}

      {ml.reason && <p className="ml-verdict__reason">{ml.reason}</p>}

      {factors.length > 0 && (
        <details className="ml-verdict__factors">
          <summary>What pushed the estimate</summary>
          <ul>
            {factors.map((factor) => (
              <li key={factor.feature}>
                <span className="ml-verdict__factor-name">{factor.label ?? factor.feature}</span>
                <span className="mono">
                  {factor.value != null ? Number(factor.value).toFixed(1) : '—'}
                  {factor.unit ? ` ${factor.unit}` : ''}
                </span>
                <span className="muted">
                  {factor.contribution != null
                    ? `${factor.contribution > 0 ? '+' : ''}${Number(factor.contribution).toFixed(2)}`
                    : ''}
                  {factor.direction ? ` · ${factor.direction}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <footer className="ml-verdict__footer">
        {ml.model_version && (
          <span className="mono ml-verdict__version">{ml.model_version}</span>
        )}
        <p className="ml-verdict__notice">{ml.disclaimer ?? ML_PROTOTYPE_NOTICE}</p>
      </footer>
    </div>
  )
}

export default MlVerdictCard
