/**
 * Vehicle security and geofencing API.
 *
 * Wraps GPS telemetry ingest, the live tracking view, geofence management, the
 * development simulator, and security alert triage. Every call goes through
 * `apiClient`, so responses are unwrapped and errors arrive in the shared
 * `{ status, code, message, details }` shape.
 *
 * **Provenance is a first-class value here.** The backend stamps every fix with
 * `SIMULATED`, `HARDWARE` or `MANUAL` and this module never overrides it. A
 * component that renders telemetry should read `is_simulated` off the fix it is
 * drawing rather than trusting a page-level flag, because a single session can
 * legitimately contain both.
 */

import { apiGet, apiPatch, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * The live tracking view for one custody session.
 *
 * A single call returns everything the dashboard needs - vehicle, zone,
 * geofence polygon, latest fix, movement status, status counts, alerts and the
 * provenance flags - so the map is never briefly drawn from a half-loaded
 * combination of responses.
 */
export function fetchTracking(custodyId) {
  return apiGet(ENDPOINTS.gpsTracking(custodyId))
}

/**
 * Raw telemetry for a session, newest first.
 *
 * `status` filters to one verdict (`NORMAL`/`WARNING`/`SUSPICIOUS`/`BREACH`),
 * which is how the dashboard shows "just the fixes that needed attention"
 * without pulling the whole track.
 */
export function fetchFixes(custodyId, { page = 1, pageSize = 100, status } = {}) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) params.set('status', status)
  return apiGet(`${ENDPOINTS.gpsFixes(custodyId)}?${params.toString()}`)
}

/**
 * Record position fixes.
 *
 * Pass a single fix object, or `{ fixes: [...] }` for a batch. A batch must
 * carry a timestamp on every fix: without one the server would stamp them
 * microseconds apart, and the movement rules divide displacement by that
 * interval, so the whole upload would read as physically impossible speed.
 */
export function postGpsEvents(payload) {
  return apiPost(ENDPOINTS.gpsEvents, payload)
}

/** The zones and their geofence geometry, for the map and the zone picker. */
export function fetchGeofences(status) {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
  return apiGet(`${ENDPOINTS.geofences}${suffix}`)
}

/**
 * Create a parking zone and its geofence.
 *
 * `latitude`/`longitude`/`radius` describe a circle, which the backend renders
 * into a polygon ring so the map has something to draw and one point-in-polygon
 * test serves both shapes. Pass `geofence_polygon` (a list of
 * `[longitude, latitude]` pairs, GeoJSON order) to describe an irregular
 * boundary instead.
 */
export function createGeofence(payload) {
  return apiPost(ENDPOINTS.geofences, payload)
}

/** Reconfigure a geofence. Moving a boundary is audited as a security-relevant act. */
export function updateGeofence(zoneId, payload) {
  return apiPatch(ENDPOINTS.geofence(zoneId), payload)
}

/** Security alerts, newest first, optionally filtered by session/status/severity. */
export function fetchAlerts({ custodySessionId, status, severity, page = 1, pageSize = 50 } = {}) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (custodySessionId) params.set('custody_session_id', custodySessionId)
  if (status) params.set('status', status)
  if (severity) params.set('severity', severity)
  return apiGet(`${ENDPOINTS.alerts}?${params.toString()}`)
}

/**
 * Record that a manager has seen an alert.
 *
 * This does not close it - acknowledgement and resolution are separate acts,
 * because "someone is aware" and "this is dealt with" are different claims.
 */
export function acknowledgeAlert(alertId) {
  return apiPost(ENDPOINTS.alertAcknowledge(alertId), {})
}

/**
 * Close an alert.
 *
 * `dismiss: true` records it as a false positive rather than resolved. The two
 * are kept apart on purpose: a dismissal is feedback about the detector, and a
 * reviewer needs to be able to count those separately.
 */
export function resolveAlert(alertId, { notes, dismiss = false } = {}) {
  return apiPost(ENDPOINTS.alertResolve(alertId), { notes: notes ?? null, dismiss })
}

/** The tracks the development simulator can generate. */
export function fetchSimulatorScenarios() {
  return apiGet(ENDPOINTS.gpsSimulatorScenarios)
}

/**
 * Generate a simulated track and ingest it as `SIMULATED` telemetry.
 *
 * Every generated fix passes through the real classification path, so the
 * verdicts are computed rather than scripted - this is a demonstration of the
 * engine, not a mock of its output. The response carries an explicit warning
 * string that the UI is expected to show.
 */
export function simulateTrack(custodyId, { scenario, seed, zoneId } = {}) {
  return apiPost(ENDPOINTS.gpsSimulate, {
    custody_id: custodyId,
    scenario,
    seed: seed ?? null,
    zone_id: zoneId ?? null,
  })
}
