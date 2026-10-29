/**
 * Prototype security classifier API.
 *
 * Two read-only calls: what model is loaded, and what it makes of a given
 * feature set. Classification of real fixes happens inline during telemetry
 * ingest and arrives on the fix itself as an `ml` block - this module is for
 * inspecting the model, not for feeding it production data.
 *
 * **Everything this module returns is from a prototype.** The model was trained
 * and evaluated on synthetic telemetry whose labels were written by hand, so its
 * accuracy says how well it recovers that hand-drawn distinction and nothing
 * about real vehicles. Both responses carry `trained_on_synthetic_data: true`
 * and a `disclaimer`; `ML_PROTOTYPE_NOTICE` in `utils/constants` mirrors that in
 * the UI so a cropped screenshot still cannot read as a production claim.
 *
 * **The classifier is subordinate to the rule engine.** It may raise a fix's
 * verdict and may never lower it, so a geofence breach always raises its alert
 * whatever this model thinks. A component must not use an `ml` block to talk a
 * reviewer out of a rule-based finding. See `docs/AI-INTEGRITY.md`.
 */

import { apiGet, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * Metadata for the loaded classifier.
 *
 * When no artifact is trained this resolves with `available: false` plus a
 * `reason` naming the script that would produce one. That is a supported
 * deployment state rather than an error, so callers should render the reason
 * instead of showing a failure.
 */
export function fetchModelInfo() {
  return apiGet(ENDPOINTS.mlModel)
}

/**
 * Score one feature set and return the reasoning.
 *
 * Every feature the model was trained on is required - the backend refuses a
 * partial input rather than defaulting the gap to zero, because a confident
 * prediction from partly-guessed input is the failure this endpoint exists to
 * avoid. Pass `null` for a genuinely unknown value and the request will be
 * rejected with the missing field named, which is the honest outcome.
 *
 * @param {Record<string, number>} features - all eight trained features.
 * @returns {Promise<object>} classification, probability, severity, reason,
 *   top factors, model version and the prototype disclaimer.
 */
export function classifyFeatures(features) {
  return apiPost(ENDPOINTS.mlClassify, features)
}

/**
 * The feature vector for a fix, read off the tracking payload.
 *
 * Built from the numbers the rule engine already measured rather than
 * recomputed here, so the classifier and the rules are always judging the same
 * observation. Returns `null` when the fix carries no classifier verdict, which
 * is what an untrained deployment produces.
 */
export function mlBlockFromFix(fix) {
  if (!fix || !fix.ml) return null
  const { classification, probability, model_version: modelVersion } = fix.ml
  return { classification, probability, modelVersion }
}
