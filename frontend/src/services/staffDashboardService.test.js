import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPatch, apiPost } from './apiClient'
import {
  acknowledgeAlert,
  completePickup,
  fetchStaffCondition,
  fetchStaffDashboard,
  hasRows,
  moveVehicle,
  relativeTime,
  resolveAlert,
  sectionReason,
  vehicleLabel,
} from './staffDashboardService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
}))

describe('staffDashboardService endpoints', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    vi.mocked(apiPatch).mockReset()
  })

  it('reads the composed operator screen', async () => {
    await fetchStaffDashboard()
    expect(apiGet).toHaveBeenCalledWith('/v1/staff/dashboard')
  })

  it('reads one condition comparison', async () => {
    await fetchStaffCondition('custody-1')
    expect(apiGet).toHaveBeenCalledWith('/v1/staff/custody/custody-1/condition')
  })

  it('moves a vehicle through the existing custody endpoint', async () => {
    // Not a dashboard-specific write: one state machine, one audit path.
    await moveVehicle('custody-1', 'READY_FOR_PICKUP')
    expect(apiPatch).toHaveBeenCalledWith('/v1/custody-sessions/custody-1', {
      status: 'READY_FOR_PICKUP',
    })
  })

  it('completes a pickup through the ticket endpoint', async () => {
    await completePickup('ticket-1', {
      odometer: '12500',
      conditionAccepted: true,
      conditionNotes: 'Clean handover.',
    })

    expect(apiPost).toHaveBeenCalledWith('/v1/tickets/ticket-1/complete', {
      odometer_at_pickup: 12500,
      condition_accepted: true,
      condition_notes: 'Clean handover.',
    })
  })

  it('omits fields that were not supplied rather than sending blanks', async () => {
    await completePickup('ticket-1', { odometer: '', conditionAccepted: null })

    expect(apiPost).toHaveBeenCalledWith('/v1/tickets/ticket-1/complete', {})
  })

  it('acknowledges and resolves through separate calls', async () => {
    // "Someone has seen it" and "this is dealt with" are different claims.
    await acknowledgeAlert('alert-1')
    await resolveAlert('alert-1', { notes: 'GPS drift.' })

    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/alert-1/acknowledge', {})
    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/alert-1/resolve', {
      notes: 'GPS drift.',
    })
  })

  it('sends the dismiss flag only when asked', async () => {
    await resolveAlert('alert-1', { dismiss: true })
    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/alert-1/resolve', { dismiss: true })
  })
})

describe('section state helpers', () => {
  it('treats only an ok section with rows as having data', () => {
    expect(hasRows({ state: 'ok', items: [1] })).toBe(true)
    expect(hasRows({ state: 'ok', items: [] })).toBe(false)
    expect(hasRows({ state: 'empty', items: [] })).toBe(false)
    expect(hasRows({ state: 'unavailable' })).toBe(false)
    expect(hasRows(null)).toBe(false)
  })

  it('prefers the backend reason over a generic line', () => {
    expect(sectionReason({ reason: 'Queue is clear.' })).toBe('Queue is clear.')
    expect(sectionReason({})).toBe('Nothing to show yet.')
    expect(sectionReason(null, 'Custom.')).toBe('Custom.')
  })
})

describe('relativeTime', () => {
  const now = new Date('2026-09-09T12:00:00Z').getTime()

  it('describes elapsed time in the largest useful unit', () => {
    expect(relativeTime('2026-09-09T11:48:00Z', now)).toBe('12 minutes ago')
    expect(relativeTime('2026-09-09T09:00:00Z', now)).toBe('3 hours ago')
    expect(relativeTime('2026-09-07T12:00:00Z', now)).toBe('2 days ago')
  })

  it('does not pluralise a single unit', () => {
    expect(relativeTime('2026-09-09T11:59:00Z', now)).toBe('1 minute ago')
    expect(relativeTime('2026-09-09T11:00:00Z', now)).toBe('1 hour ago')
  })

  it('returns null rather than a misleading string for bad input', () => {
    // An operator reading "just now" about a fix from yesterday would act on it.
    expect(relativeTime(null, now)).toBeNull()
    expect(relativeTime('not-a-date', now)).toBeNull()
  })

  it('never reports a negative duration for a future timestamp', () => {
    expect(relativeTime('2026-09-09T12:30:00Z', now)).toBe('just now')
  })
})

describe('vehicleLabel', () => {
  it('combines plate and model when both exist', () => {
    expect(vehicleLabel({ plate_number: 'UP85CA1234', make: 'Maruti', model: 'Swift' })).toBe(
      'UP85CA1234 · Maruti Swift'
    )
  })

  it('falls back gracefully on partial data', () => {
    expect(vehicleLabel({ plate_number: 'UP85CA1234' })).toBe('UP85CA1234')
    expect(vehicleLabel({ plate_number: 'UP85CA1234', make: 'Maruti' })).toBe(
      'UP85CA1234 · Maruti'
    )
    expect(vehicleLabel(null)).toBe('—')
  })
})
