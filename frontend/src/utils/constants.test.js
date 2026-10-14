/**
 * Drift guard between the frontend status vocabularies and the backend enums.
 *
 * The constants in `utils/constants.js` drive badge labels. They used to list
 * custody statuses (PENDING / IN_RETRIEVAL / COMPLETED / FLAGGED) that the
 * backend has never emitted, so every badge rendered a bare key instead of a
 * label and nothing failed. This test exists so that cannot happen quietly
 * again.
 *
 * The backend lists below are copied from `backend/models/enums.py`. If you
 * change one, change both - and this test will tell you if you forget.
 */

import { describe, expect, it } from 'vitest'
import {
  CUSTODY_STATUS,
  CUSTODY_STATUS_LABELS,
  TICKET_STATUS,
  TICKET_STATUS_LABELS,
  VEHICLE_STATUS,
  VEHICLE_STATUS_LABELS,
} from './constants'

// Mirrors backend/models/enums.py::CustodyStatus
const BACKEND_CUSTODY_STATUSES = [
  'CHECKED_IN',
  'PARKED',
  'IN_TRANSIT',
  'RETRIEVAL_REQUESTED',
  'RETRIEVAL_IN_PROGRESS',
  'READY_FOR_PICKUP',
  'CHECKED_OUT',
  'DISPUTED',
]

// Mirrors backend/models/enums.py::VehicleStatus
const BACKEND_VEHICLE_STATUSES = ['REGISTERED', 'IN_CUSTODY', 'FLAGGED', 'ARCHIVED']

// The API's projected vocabulary, from backend/services/tickets.py
const BACKEND_TICKET_STATUSES = [
  'CREATED',
  'IN_CUSTODY',
  'READY_FOR_PICKUP',
  'COMPLETED',
  'CANCELLED',
  'EXPIRED',
]

describe('status vocabularies match the backend', () => {
  it('lists exactly the custody statuses the backend can emit', () => {
    expect(Object.values(CUSTODY_STATUS).sort()).toEqual([...BACKEND_CUSTODY_STATUSES].sort())
  })

  it('lists exactly the vehicle statuses the backend can emit', () => {
    expect(Object.values(VEHICLE_STATUS).sort()).toEqual([...BACKEND_VEHICLE_STATUSES].sort())
  })

  it('lists exactly the ticket statuses the API projects', () => {
    expect(Object.values(TICKET_STATUS).sort()).toEqual([...BACKEND_TICKET_STATUSES].sort())
  })

  it('has a human label for every custody status', () => {
    for (const status of BACKEND_CUSTODY_STATUSES) {
      expect(CUSTODY_STATUS_LABELS[status], status).toBeTruthy()
      expect(CUSTODY_STATUS_LABELS[status]).not.toBe(status)
    }
  })

  it('has a human label for every vehicle status', () => {
    for (const status of BACKEND_VEHICLE_STATUSES) {
      expect(VEHICLE_STATUS_LABELS[status], status).toBeTruthy()
      expect(VEHICLE_STATUS_LABELS[status]).not.toBe(status)
    }
  })

  it('has a human label for every ticket status', () => {
    for (const status of BACKEND_TICKET_STATUSES) {
      expect(TICKET_STATUS_LABELS[status], status).toBeTruthy()
      expect(TICKET_STATUS_LABELS[status]).not.toBe(status)
    }
  })

  it('never resurrects the statuses that were removed', () => {
    // These were in this file before and matched nothing the backend emits.
    for (const stale of ['PENDING', 'IN_RETRIEVAL', 'FLAGGED']) {
      expect(Object.values(CUSTODY_STATUS)).not.toContain(stale)
    }
  })
})
