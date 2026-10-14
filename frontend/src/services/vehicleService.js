/**
 * Vehicle API.
 *
 * Every call goes through `apiClient`, so the response envelope is unwrapped
 * and errors arrive as `{ status, code, message, details, requestId }`. No
 * component parses an error shape itself.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * List vehicles.
 *
 * The backend scopes the result to the caller, so a customer gets their own
 * vehicles and staff get everyone's. `filters` accepts `{ status, search,
 * owner_id, include_archived, page, page_size }`.
 */
export function fetchVehicles(filters = {}) {
  return apiGet(ENDPOINTS.vehicles, { params: filters })
}

export function fetchVehicle(id) {
  return apiGet(ENDPOINTS.vehicle(id))
}

/** Previous valet sessions for a vehicle, newest first. */
export function fetchVehicleSessions(id) {
  return apiGet(ENDPOINTS.vehicleSessions(id))
}

/**
 * Register a vehicle.
 *
 * A customer registers to themselves; valet staff pass `ownerId` to register on
 * a customer's behalf during check-in. The backend rejects a customer naming
 * anybody else as the owner.
 */
export function createVehicle({ plateNumber, make, model, year, color, notes, ownerId }) {
  return apiPost(ENDPOINTS.vehicles, {
    plate_number: plateNumber,
    make,
    ...(model ? { model } : {}),
    ...(year ? { year: Number(year) } : {}),
    ...(color ? { color } : {}),
    ...(notes ? { notes } : {}),
    ...(ownerId ? { owner_id: ownerId } : {}),
  })
}

/** Edit a vehicle. Only the fields present are changed. */
export function updateVehicle(id, changes) {
  const payload = {}
  if (changes.plateNumber !== undefined) payload.plate_number = changes.plateNumber
  if (changes.make !== undefined) payload.make = changes.make
  if (changes.model !== undefined) payload.model = changes.model
  if (changes.year !== undefined) payload.year = changes.year ? Number(changes.year) : null
  if (changes.color !== undefined) payload.color = changes.color
  if (changes.notes !== undefined) payload.notes = changes.notes

  return apiPatch(ENDPOINTS.vehicle(id), payload)
}

/**
 * Archive a vehicle.
 *
 * Returns null on success - the endpoint answers 204. The row is not deleted:
 * a vehicle with custody history stays in the audit trail, and the call is
 * refused while the vehicle is in custody.
 */
export function archiveVehicle(id) {
  return apiDelete(ENDPOINTS.vehicle(id))
}
