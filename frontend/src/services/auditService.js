/**
 * Audit trail service.
 *
 * Every function here is a **read**. That is not an omission: the backend
 * registers no write route for the audit trail, and the `audit_logs` table
 * rejects UPDATE and DELETE at the database level. A service module that
 * offered `updateAuditEntry` would be offering a call that can only fail, and
 * a UI built on it would imply the log is editable when the whole point is that
 * it is not.
 */

import { apiGet } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * A page of audit entries, newest first.
 *
 * @param {object} [options]
 * @param {number} [options.page]          1-based page number
 * @param {number} [options.pageSize]      rows per page (backend caps at 200)
 * @param {string} [options.action]        e.g. `VEHICLE_CHECKED_IN`
 * @param {string} [options.entityType]    e.g. `custody_sessions`
 * @param {string} [options.entityId]
 * @param {string} [options.actorId]       everything one user did
 * @param {string} [options.custodySessionId]
 * @param {string} [options.outcome]       `SUCCESS` | `FAILURE` | `DENIED`
 * @param {string} [options.dateFrom]      ISO-8601
 * @param {string} [options.dateTo]        ISO-8601
 * @param {boolean} [options.lifecycleOnly] the fourteen lifecycle events only
 */
export function fetchAuditLog(options = {}) {
  const {
    page,
    pageSize,
    action,
    entityType,
    entityId,
    actorId,
    custodySessionId,
    outcome,
    dateFrom,
    dateTo,
    lifecycleOnly,
  } = options

  const params = {}
  if (page) params.page = page
  if (pageSize) params.page_size = pageSize
  if (action) params.action = action
  if (entityType) params.entity_type = entityType
  if (entityId) params.entity_id = entityId
  if (actorId) params.actor_id = actorId
  if (custodySessionId) params.custody_session_id = custodySessionId
  if (outcome) params.outcome = outcome
  if (dateFrom) params.date_from = dateFrom
  if (dateTo) params.date_to = dateTo
  if (lifecycleOnly) params.lifecycle_only = 'true'

  return apiGet(ENDPOINTS.audit, { params })
}

/** Distinct values present in the log, so filter dropdowns never offer a dud. */
export function fetchAuditFilters() {
  return apiGet(ENDPOINTS.auditFilters)
}

/** One custody's events, oldest first, with the lifecycle gaps marked. */
export function fetchCustodyTimeline(custodyId) {
  return apiGet(ENDPOINTS.auditTimeline(custodyId))
}

/** Every custody timeline for one vehicle, most recent visit first. */
export function fetchVehicleAuditTrail(vehicleId) {
  return apiGet(ENDPOINTS.auditVehicle(vehicleId))
}

/**
 * Build a short human-readable summary for one entry.
 *
 * The backend supplies a `description` written at the moment the event
 * happened, which is the better text whenever it is present. This exists for
 * the cases where it is not, so a row never renders as an empty cell.
 */
export function summariseEntry(entry) {
  if (!entry) return ''
  if (entry.description) return entry.description
  const entity = entry.entity_type ? ` on ${entry.entity_type}` : ''
  return `${entry.label || entry.action}${entity}`
}

/**
 * A compact timestamp for the timeline gutter.
 *
 * Returns an empty string rather than `Invalid Date` for a missing or malformed
 * value: a log viewer that prints `Invalid Date` next to a real entry makes the
 * whole table look unreliable.
 */
export function formatEntryTime(isoValue) {
  if (!isoValue) return ''
  const parsed = new Date(isoValue)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/**
 * Which actor label to show.
 *
 * Prefers the snapshotted email, falls back to the role, and says "system" when
 * neither is present -- an unattributed entry is a real thing (a failed login
 * before the account was identified) and should be labelled as such rather
 * than left blank.
 */
export function actorLabel(entry) {
  if (!entry) return ''
  if (entry.actor_email) return entry.actor_email
  if (entry.actor_role) return `${entry.actor_role} (no account)`
  return 'system'
}

/**
 * Group timeline events by the scope the backend labelled them with.
 *
 * Keeps the three groups in the order the timeline returns them, so a caller
 * can render "what happened during the custody" separately from "context about
 * it" without re-sorting.
 */
export function groupEventsByScope(events) {
  const groups = { provenance: [], login: [], custody: [] }
  for (const event of events || []) {
    const scope = groups[event.scope] ? event.scope : 'custody'
    groups[scope].push(event)
  }
  return groups
}
