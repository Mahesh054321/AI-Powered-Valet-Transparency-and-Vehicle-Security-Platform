/**
 * Centralized alert feed service.
 *
 * One feed for every role. The backend decides what an actor may see, so this
 * module never filters by role client-side — doing so would only hide rows that
 * were already sent, and a client-side filter is one refactor away from
 * becoming the only filter.
 *
 * Two questions are kept apart throughout, because they have different answers
 * per person:
 *
 *   - `status`  — has the alert been dealt with (OPEN / RESOLVED / DISMISSED)
 *   - `is_read` — has *this* user seen it
 *
 * Marking something read never changes the alert, so one person opening the app
 * cannot clear it from someone else's queue.
 */

import { apiGet, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** Build the query string from the current filter state. */
export function buildAlertParams(filters = {}) {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.type) params.set('type', filters.type)
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.vehicleId) params.set('vehicle_id', filters.vehicleId)
  if (filters.unreadOnly) params.set('unread_only', 'true')
  if (filters.page) params.set('page', String(filters.page))
  if (filters.pageSize) params.set('page_size', String(filters.pageSize))
  return params.toString()
}

/** The alerts this actor may see. */
export function fetchAlerts(filters = {}) {
  const qs = buildAlertParams(filters)
  return apiGet(qs ? `${ENDPOINTS.alertFeed}?${qs}` : ENDPOINTS.alertFeed)
}

/**
 * Unread counts for the notification badge.
 *
 * A separate endpoint on purpose: polling the whole feed every few seconds just
 * to render a number would be wasteful, and this costs one indexed count.
 */
export function fetchAlertSummary() {
  return apiGet(ENDPOINTS.alertSummary)
}

/** Mark one alert read, for the calling user only. */
export function markAlertRead(alertId) {
  return apiPost(`${ENDPOINTS.alertFeed}/${alertId}/read`, {})
}

/** Mark everything currently visible to this actor as read. */
export function markAllAlertsRead() {
  return apiPost(ENDPOINTS.alertReadAll, {})
}

/** Close an alert. The backend refuses anyone below manager. */
export function resolveAlert(alertId, { notes = '', dismiss = false } = {}) {
  const body = { dismiss }
  // Blank notes are omitted rather than sent as an empty string, so the audit
  // row does not record a note that was never written.
  if (notes && notes.trim()) body.notes = notes.trim()
  return apiPost(`${ENDPOINTS.alertFeed}/${alertId}/resolve`, body)
}

/** The five categories, for filter controls. */
export const ALERT_TYPES = [
  { value: 'GEOFENCE_BREACH', label: 'Geofence breach' },
  { value: 'SUSPICIOUS_MOVEMENT', label: 'Suspicious movement' },
  { value: 'OVERSPEED', label: 'Overspeed' },
  { value: 'POTENTIAL_DAMAGE', label: 'Potential damage' },
  { value: 'RETRIEVAL_READY', label: 'Retrieval ready' },
]

export const ALERT_SEVERITIES = [
  { value: 'CRITICAL', label: 'Critical' },
  { value: 'HIGH', label: 'High' },
  { value: 'MEDIUM', label: 'Medium' },
  { value: 'LOW', label: 'Low' },
]

export const ALERT_STATUSES = [
  { value: 'OPEN', label: 'Open' },
  { value: 'RESOLVED', label: 'Resolved' },
  { value: 'DISMISSED', label: 'Dismissed' },
]

/**
 * Whether a category is something a customer should be shown prominently.
 *
 * The backend already scopes what a customer receives; this only decides
 * presentation. `POTENTIAL_DAMAGE` is deliberately excluded — an unverified
 * detector finding is a task for a manager, not an alarm for a customer.
 */
export function isCustomerFacing(type) {
  return type !== 'POTENTIAL_DAMAGE'
}

/** A short human label for an alert category. */
export function alertTypeLabel(type) {
  return ALERT_TYPES.find((entry) => entry.value === type)?.label ?? type
}

/** Relative time, clamped so a future timestamp never reads as negative. */
export function relativeTime(iso) {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null

  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

/** Tone class for a severity badge. */
export function severityTone(severity) {
  switch (severity) {
    case 'CRITICAL':
      return 'badge--danger'
    case 'HIGH':
      return 'badge--warning'
    case 'MEDIUM':
      return 'badge--info'
    default:
      return 'badge--muted'
  }
}
