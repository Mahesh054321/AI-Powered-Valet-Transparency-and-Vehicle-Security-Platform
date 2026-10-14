/**
 * Valet ticket and custody API - the check-in workflow.
 *
 * The ticket `status` returned by these calls is the *projected* vocabulary
 * (CREATED / IN_CUSTODY / READY_FOR_PICKUP / COMPLETED / CANCELLED), derived on
 * the backend from the stored ticket status plus the custody session's state.
 * `storedStatus` comes back alongside it so the UI can be honest about which
 * one it is displaying.
 */

import { apiGet, apiPatch, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

export function fetchTickets(filters = {}) {
  return apiGet(ENDPOINTS.tickets, { params: filters })
}

export function fetchTicket(id) {
  return apiGet(ENDPOINTS.ticket(id))
}

/**
 * Issue a ticket for an already-registered vehicle.
 *
 * This single call performs workflow steps 5, 8 and 9: it creates the ticket,
 * allocates the unique ticket number and renders the QR image server-side. The
 * response carries `qr_url`, which the digital ticket page displays.
 */
export function issueTicket({ vehicleId, specialInstructions }) {
  return apiPost(ENDPOINTS.tickets, {
    vehicle_id: vehicleId,
    ...(specialInstructions ? { special_instructions: specialInstructions } : {}),
  })
}

/**
 * Hand the vehicle over: open custody, assign a zone, record its condition.
 *
 * `photoRefs` are the image references returned by `uploadService.uploadImages`
 * - never image bytes, because the schema stores references only.
 */
export function checkInTicket(ticketId, { zoneId, photoRefs, odometer, fuelLevel, damageNotes, instructions, retrievalMinutes }) {
  return apiPost(ENDPOINTS.ticketCheckIn(ticketId), {
    zone_id: zoneId,
    ...(photoRefs?.length ? { photo_refs: photoRefs } : {}),
    ...(odometer !== undefined && odometer !== '' ? { odometer_at_drop_off: Number(odometer) } : {}),
    ...(fuelLevel !== undefined && fuelLevel !== '' ? { fuel_level_percent: Number(fuelLevel) } : {}),
    ...(damageNotes ? { pre_existing_damage_notes: damageNotes } : {}),
    ...(instructions ? { inspector_notes: instructions } : {}),
    ...(retrievalMinutes ? { expected_retrieval_minutes: Number(retrievalMinutes) } : {}),
  })
}

/** Return the vehicle to the customer and close the ticket. */
export function completeTicket(ticketId, { odometer, conditionAccepted, conditionNotes } = {}) {
  return apiPost(ENDPOINTS.ticketComplete(ticketId), {
    ...(odometer !== undefined && odometer !== '' ? { odometer_at_pickup: Number(odometer) } : {}),
    ...(conditionAccepted !== undefined ? { condition_accepted: conditionAccepted } : {}),
    ...(conditionNotes ? { condition_notes: conditionNotes } : {}),
  })
}

export function cancelTicket(ticketId, reason) {
  return apiPost(ENDPOINTS.ticketCancel(ticketId), { ...(reason ? { reason } : {}) })
}

export function fetchCustodySession(id) {
  return apiGet(ENDPOINTS.custodySession(id))
}

/**
 * Move a custody session to a new state.
 *
 * An illegal transition comes back as a 409 whose `details.allowed` lists the
 * states it could have moved to, so the UI can offer those instead of guessing.
 */
export function updateCustodyStatus(id, status) {
  return apiPatch(ENDPOINTS.custodySession(id), { status })
}
