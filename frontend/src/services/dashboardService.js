/**
 * Customer dashboard and pickup-request API.
 *
 * The dashboard arrives as one composed snapshot rather than ten parallel calls.
 * That is not an optimisation: parallel requests are read at different moments,
 * so a dashboard could show a custody status from before a valet moved the car
 * beside a queue position from after. One snapshot cannot disagree with itself.
 *
 * **Every section carries a `state`** of `ok`, `empty` or `unavailable`, plus a
 * `reason` for the latter two. This matters more than it looks: a card that
 * renders nothing when data is missing is indistinguishable from a card saying
 * "nothing is wrong". A customer with no car in custody is a normal condition
 * and the UI must say so in words.
 *
 * **Nothing here is static.** An `empty` state is a real answer the backend read
 * from the database, not a placeholder the frontend invented.
 */

import { apiGet, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** The signed-in customer's whole dashboard, in one snapshot. */
export function fetchDashboard() {
  return apiGet(ENDPOINTS.customerDashboard)
}

/**
 * Ask for the vehicle: status change, audit entry, estimate and valet queue in
 * one call.
 *
 * Doing this as a single request rather than four is deliberate. A browser that
 * changed the status and then failed to generate an estimate would leave the
 * customer waiting on a car nobody had been told to fetch.
 */
export function requestVehicle(custodySessionId) {
  return apiPost(ENDPOINTS.retrievalRequest, { custody_session_id: custodySessionId })
}

/**
 * The retrieval queue staff act on. Staff-only; a customer gets 403.
 *
 * Polled, not pushed — there is no websocket channel in this project, and the
 * response says so via `delivery` and `delivery_note`. Callers should surface
 * that rather than describing the view as live.
 */
export function fetchRetrievalQueue() {
  return apiGet(ENDPOINTS.retrievalQueue)
}

/** True when a section has real data to render. */
export function hasData(section) {
  return section?.state === 'ok'
}

/**
 * The words to show when a section has nothing.
 *
 * Falls back to a generic line only if the backend somehow omitted the reason —
 * which should not happen, since every section is built with one.
 */
export function emptyReason(section, fallback = 'Nothing to show yet.') {
  if (section?.reason) return section.reason
  if (section?.state === 'unavailable') return 'This could not be read right now.'
  return fallback
}

const SECURITY_LABELS = {
  SECURE: 'Secure',
  ATTENTION: 'Needs attention',
  BREACH: 'Breach',
  UNKNOWN: 'Unknown',
}

const SECURITY_TONES = {
  SECURE: 'badge--success',
  ATTENTION: 'badge--warning',
  BREACH: 'badge--danger',
  UNKNOWN: 'badge--muted',
}

export function securityLabel(status) {
  return SECURITY_LABELS[status] ?? 'Unknown'
}

export function securityTone(status) {
  return SECURITY_TONES[status] ?? 'badge--muted'
}

const CUSTODY_LABELS = {
  CHECKED_IN: 'Checked in',
  PARKED: 'Parked',
  IN_TRANSIT: 'In transit',
  RETRIEVAL_REQUESTED: 'Retrieval requested',
  RETRIEVAL_IN_PROGRESS: 'Being retrieved',
  READY_FOR_PICKUP: 'Ready for pickup',
  CHECKED_OUT: 'Checked out',
  DISPUTED: 'Disputed',
}

export function custodyLabel(status) {
  return CUSTODY_LABELS[status] ?? String(status ?? 'Unknown')
}

/**
 * How long ago, in words, for a "checked in 12 minutes ago" line.
 *
 * Deliberately coarse. A dashboard that says "12 minutes ago" is honest about
 * the resolution it has; one that prints a full timestamp alongside a live
 * countdown invites the reader to trust precision that is not there.
 */
export function relativeTime(isoString, now = Date.now()) {
  if (!isoString) return null
  const then = new Date(isoString).getTime()
  if (Number.isNaN(then)) return null
  const minutes = Math.round((now - then) / 60000)
  if (minutes < 0) return 'just now'
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}
