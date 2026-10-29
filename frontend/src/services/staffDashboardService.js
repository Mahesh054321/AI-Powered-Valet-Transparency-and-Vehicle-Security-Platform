/**
 * Valet operator dashboard service.
 *
 * Reads come from one composed endpoint. Writes deliberately do **not** live
 * here: every action an operator takes calls the endpoint that already exists
 * for it, so there is one state machine and one audit path. A dashboard service
 * that performed its own writes would be a second, less-tested copy of those
 * rules, and the audit trail would depend on which of the two wrote the row.
 */

import { apiGet, apiPatch, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** The whole operator screen, in one consistent snapshot. */
export function fetchStaffDashboard() {
  return apiGet(ENDPOINTS.staffDashboard)
}

/** The full before/after condition comparison for one vehicle. */
export function fetchStaffCondition(custodyId) {
  return apiGet(ENDPOINTS.staffCondition(custodyId))
}

/**
 * Move a custody session to a new state.
 *
 * The backend refuses illegal transitions with the list of permitted ones, so a
 * caller can recover instead of guessing. `CHECKED_OUT` is not reachable here
 * by design — completing a pickup also settles the ticket, frees the zone and
 * updates the vehicle, none of which a bare status write does.
 */
export function moveVehicle(custodyId, status) {
  return apiPatch(ENDPOINTS.custodySession(custodyId), { status })
}

/**
 * Close the custody window and hand the car back.
 *
 * This is the "complete pickup" action. It is a ticket operation rather than a
 * status change because of everything it settles alongside the status.
 */
export function completePickup(ticketId, { odometer, conditionAccepted, conditionNotes } = {}) {
  const body = {}
  if (odometer != null && odometer !== '') body.odometer_at_pickup = Number(odometer)
  if (conditionAccepted != null) body.condition_accepted = Boolean(conditionAccepted)
  if (conditionNotes) body.condition_notes = conditionNotes
  return apiPost(ENDPOINTS.ticketComplete(ticketId), body)
}

/** Record that a manager has seen an alert. Does not close it. */
export function acknowledgeAlert(alertId) {
  return apiPost(ENDPOINTS.alertAcknowledge(alertId), {})
}

/**
 * Close an alert.
 *
 * Kept separate from acknowledgement because "someone is aware" and "this is
 * dealt with" are different claims, and conflating them would let an alert be
 * closed by being glanced at.
 */
export function resolveAlert(alertId, { notes, dismiss = false } = {}) {
  const body = {}
  if (notes) body.notes = notes
  if (dismiss) body.dismiss = true
  return apiPost(ENDPOINTS.alertResolve(alertId), body)
}

/**
 * Whether a section actually has rows to show.
 *
 * `empty` is a real answer from the database, not a failure, and treating it as
 * one would make a quiet morning look like an outage.
 */
export function hasRows(section) {
  return section?.state === 'ok' && Array.isArray(section.items) && section.items.length > 0
}

/** The sentence an empty or unavailable section should print. */
export function sectionReason(section, fallback = 'Nothing to show yet.') {
  return section?.reason ?? fallback
}

/**
 * How long ago, in words.
 *
 * Returns null for anything it cannot parse rather than a misleading string:
 * an operator reading "just now" about a fix from yesterday would act on it.
 */
export function relativeTime(iso, now = Date.now()) {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null

  const minutes = Math.round(Math.max(0, now - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`

  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`

  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/** A short plate-and-model line, for table rows. */
export function vehicleLabel(vehicle) {
  if (!vehicle) return '—'
  const model = [vehicle.make, vehicle.model].filter(Boolean).join(' ')
  return model ? `${vehicle.plate_number} · ${model}` : vehicle.plate_number
}
