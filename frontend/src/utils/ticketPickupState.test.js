import { describe, expect, it } from 'vitest'
import { getTicketPickupState } from './ticketPickupState'

const ticket = (status, custodyStatus, extras = {}) => ({
  status,
  custody_session: custodyStatus ? { id: 'custody-1', status: custodyStatus } : null,
  ...extras,
})

describe('pickup action after QR scan', () => {
  it.each(['PARKED', 'IN_TRANSIT'])('allows the customer request from active %s custody', (status) => {
    expect(getTicketPickupState(ticket('IN_CUSTODY', status)).canRequest).toBe(true)
  })
  it.each(['CHECKED_IN', 'RETRIEVAL_REQUESTED', 'RETRIEVAL_IN_PROGRESS', 'READY_FOR_PICKUP', 'DISPUTED', 'CHECKED_OUT'])('does not allow pickup from %s', (status) => {
    expect(getTicketPickupState(ticket('IN_CUSTODY', status)).canRequest).toBe(false)
  })
  it('hides pickup when no custody session exists', () => {
    expect(getTicketPickupState(ticket('CREATED', null)).canRequest).toBe(false)
  })
  it('hides pickup for a completed ticket even with a stale parked session', () => {
    const state = getTicketPickupState(ticket('COMPLETED', 'PARKED'))
    expect(state.canRequest).toBe(false)
    expect(state.code).toBe('completed')
  })
  it('hides pickup for cancelled tickets', () => {
    expect(getTicketPickupState(ticket('CANCELLED', 'PARKED')).canRequest).toBe(false)
  })
  it('treats a pickup timestamp as closed even if the status is stale', () => {
    expect(getTicketPickupState(ticket('IN_CUSTODY', 'PARKED', { custody_session: {
      id: 'custody-1', status: 'PARKED', picked_up_at: '2026-11-26T08:00:00Z',
    } })).code).toBe('completed')
  })
  it('does not allow a second request if the timestamp is present', () => {
    expect(getTicketPickupState(ticket('IN_CUSTODY', 'PARKED', { custody_session: {
      id: 'custody-1', status: 'PARKED', retrieval_requested_at: '2026-11-26T07:45:00Z',
    } })).code).toBe('in-progress')
  })
})
