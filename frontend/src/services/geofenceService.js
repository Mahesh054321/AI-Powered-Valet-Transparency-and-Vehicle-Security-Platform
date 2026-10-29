/**
 * Vehicle security and geofencing API.
 *
 * Wraps GPS telemetry ingest, the live tracking view, geofence management, the
 * development simulator, and security alert triage. Every call goes through
 * `apiClient`, so responses are unwrapped and errors arrive in the shared
 * `{ status, code, message, details }` shape.
 *
 * **Provenance is a first-class value here.** The backend stamps every fix with
 * `SIMULATED`, `MOBILE_GPS`, `REAL_GPS` or `MANUAL` and this module never
 * overrides it.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * The live tracking view for one custody session.
 */
export function fetchTracking(custodyId) {
  return apiGet(ENDPOINTS.gpsTracking(custodyId))
}

/**
 * Raw telemetry for a session, newest first.
 */
export function fetchFixes(custodyId, { page = 1, pageSize = 100, status } = {}) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) params.set('status', status)
  return apiGet(`${ENDPOINTS.gpsFixes(custodyId)}?${params.toString()}`)
}

/**
 * Record position fixes.
 */
export function postGpsEvents(payload) {
  return apiPost(ENDPOINTS.gpsEvents, payload)
}

/**
 * Ingest GPS telemetry from an authorized mobile browser (MOBILE_GPS).
 */
export function postMobileGps(payload) {
  return apiPost(ENDPOINTS.gpsMobile, payload)
}

/**
 * Record staff-entered vehicle telemetry and sensor observations (MANUAL).
 */
export function postVehicleGps(payload) {
  return apiPost(ENDPOINTS.gpsVehicle, payload)
}

/** The zones and their geofence geometry, for the map and the zone picker. */
export function fetchGeofences(status) {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
  return apiGet(`${ENDPOINTS.geofences}${suffix}`)
}

/** Fetch details for one safe zone. */
export function fetchGeofence(zoneId) {
  return apiGet(ENDPOINTS.geofence(zoneId))
}

/** Calculate boundary coordinates, effective radius and area without persisting. */
export function previewGeofence(payload) {
  return apiPost(ENDPOINTS.geofencePreview, payload)
}

/**
 * Create a parking zone or customer safe zone.
 */
export function createGeofence(payload) {
  return apiPost(ENDPOINTS.geofences, payload)
}

/** Reconfigure a geofence. */
export function updateGeofence(zoneId, payload) {
  return apiPatch(ENDPOINTS.geofence(zoneId), payload)
}

/** Replace a geofence definition. */
export function replaceGeofence(zoneId, payload) {
  return apiPut(ENDPOINTS.geofence(zoneId), payload)
}

/** Deactivate / delete a safe zone. */
export function deleteGeofence(zoneId) {
  return apiDelete(ENDPOINTS.geofence(zoneId))
}

/** Activate an inactive safe zone. */
export function activateGeofence(zoneId) {
  return apiPost(ENDPOINTS.geofenceActivate(zoneId), {})
}

/** Deactivate an active safe zone without deleting history. */
export function deactivateGeofence(zoneId) {
  return apiPost(ENDPOINTS.geofenceDeactivate(zoneId), {})
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
 */
export function acknowledgeAlert(alertId) {
  return apiPost(ENDPOINTS.alertAcknowledge(alertId), {})
}

/**
 * Mark an alert as being investigated by staff.
 */
export function investigateAlert(alertId, { notes } = {}) {
  return apiPost(ENDPOINTS.alertInvestigate(alertId), { notes: notes ?? null })
}

/**
 * Close an alert.
 */
export function resolveAlert(alertId, { notes, dismiss = false } = {}) {
  return apiPost(ENDPOINTS.alertResolve(alertId), { notes: notes ?? null, dismiss })
}

/**
 * Reopen a resolved or dismissed alert.
 */
export function reopenAlert(alertId, { reason } = {}) {
  return apiPost(ENDPOINTS.alertReopen(alertId), { reason: reason ?? null })
}

/** The tracks the development simulator can generate. */
export function fetchSimulatorScenarios() {
  return apiGet(ENDPOINTS.gpsSimulatorScenarios)
}

/**
 * Generate a simulated track and ingest it as `SIMULATED` telemetry.
 */
export function simulateTrack(custodyId, { scenario, seed, zoneId } = {}) {
  return apiPost(ENDPOINTS.gpsSimulate, {
    custody_id: custodyId,
    scenario,
    seed: seed ?? null,
    zone_id: zoneId ?? null,
  })
}
