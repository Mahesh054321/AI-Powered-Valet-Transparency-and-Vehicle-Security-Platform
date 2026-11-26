/**
 * Only an active, parked or in-transit custody session can accept a *new*
 * customer pickup request. Keep this aligned with the backend's
 * ALLOWED_CUSTODY_TRANSITIONS; the API remains the final authority.
 */
const REQUESTABLE_CUSTODY_STATES = new Set(['PARKED', 'IN_TRANSIT'])
const CLOSED_TICKET_STATES = new Set(['COMPLETED', 'CANCELLED'])

export function getTicketPickupState(ticket) {
  const session = ticket?.custody_session
  if (ticket?.status === 'CANCELLED') {
    return { canRequest: false, code: 'cancelled', message: 'This ticket was cancelled. A vehicle request is not available.' }
  }
  if (ticket?.status === 'COMPLETED' || session?.status === 'CHECKED_OUT' ||
      session?.picked_up_at || ticket?.actual_pickup_at) {
    return { canRequest: false, code: 'completed', message: 'Custody completed — the vehicle has been returned. No further pickup request is available.' }
  }
  if (!session?.id || CLOSED_TICKET_STATES.has(ticket?.stored_status)) {
    return { canRequest: false, code: 'not-started', message: 'A pickup request is available only after an active custody session has started.' }
  }
  if (session.retrieval_requested_at || ['RETRIEVAL_REQUESTED', 'RETRIEVAL_IN_PROGRESS', 'READY_FOR_PICKUP'].includes(session.status)) {
    return { canRequest: false, code: 'in-progress', message: 'Pickup has already been requested. Follow the retrieval status; another request is not needed.' }
  }
  if (session.status === 'DISPUTED') {
    return { canRequest: false, code: 'disputed', message: 'This custody session is under review. Please contact valet management.' }
  }
  if (ticket?.status === 'IN_CUSTODY' && REQUESTABLE_CUSTODY_STATES.has(session.status)) {
    return { canRequest: true, code: 'available', message: 'Your vehicle is in active custody. Request pickup to notify the valet staff.' }
  }
  return { canRequest: false, code: 'not-available', message: 'Vehicle requests become available when the car is parked or in transit within an active custody session.' }
}
