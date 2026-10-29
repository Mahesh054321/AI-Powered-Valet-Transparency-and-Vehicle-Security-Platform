/**
 * Lookup endpoints the check-in workflow needs.
 *
 * `fetchCustomers` is staff-only on the backend - a customer calling it gets
 * 403. It returns only the fields needed to pick a person, never a password
 * hash.
 */

import { apiGet } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** Customers a valet may check in, searchable by name or email. */
export function fetchCustomers(search) {
  return apiGet(ENDPOINTS.customers, { params: search ? { search } : {} })
}

/**
 * Parking zones.
 *
 * `parkableOnly` hides zones that cannot currently accept a vehicle (retired or
 * under maintenance), which is what the assignment step wants. A zone at
 * capacity is still returned, with `available_slots: 0`, so the UI can explain
 * why it is not selectable rather than silently omitting it.
 */
export function fetchParkingZones({ parkableOnly = false } = {}) {
  return apiGet(ENDPOINTS.parkingZones, {
    params: parkableOnly ? { parkable_only: true } : {},
  })
}
