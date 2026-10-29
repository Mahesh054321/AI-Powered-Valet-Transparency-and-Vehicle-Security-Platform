import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost } from './apiClient'
import {
  custodyLabel,
  emptyReason,
  fetchDashboard,
  fetchRetrievalQueue,
  hasData,
  relativeTime,
  requestVehicle,
  securityLabel,
  securityTone,
} from './dashboardService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

describe('dashboardService endpoints', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
  })

  it('reads the composed dashboard', async () => {
    vi.mocked(apiGet).mockResolvedValue({ has_active_custody: true })

    await fetchDashboard()

    expect(apiGet).toHaveBeenCalledWith('/v1/customers/me/dashboard')
  })

  it('posts the pickup request with the custody session id', async () => {
    vi.mocked(apiPost).mockResolvedValue({ custody_status: 'RETRIEVAL_REQUESTED' })

    await requestVehicle('custody-1')

    expect(apiPost).toHaveBeenCalledWith('/v1/retrieval/request', {
      custody_session_id: 'custody-1',
    })
  })

  it('reads the staff retrieval queue', async () => {
    vi.mocked(apiGet).mockResolvedValue({ items: [] })

    await fetchRetrievalQueue()

    expect(apiGet).toHaveBeenCalledWith('/v1/retrieval/queue')
  })
})

describe('section state helpers', () => {
  it('treats only "ok" as having data', () => {
    expect(hasData({ state: 'ok' })).toBe(true)
    expect(hasData({ state: 'empty' })).toBe(false)
    expect(hasData({ state: 'unavailable' })).toBe(false)
    expect(hasData(null)).toBe(false)
    expect(hasData(undefined)).toBe(false)
  })

  it('prefers the backend reason over a generic line', () => {
    // The backend always supplies one; the fallback exists only so a missing
    // reason never renders as a blank card.
    expect(emptyReason({ state: 'empty', reason: 'No GPS fix yet.' })).toBe(
      'No GPS fix yet.'
    )
    expect(emptyReason({ state: 'unavailable' })).toBe(
      'This could not be read right now.'
    )
    expect(emptyReason({ state: 'empty' })).toBe('Nothing to show yet.')
    expect(emptyReason(null, 'Custom.')).toBe('Custom.')
  })
})

describe('label mapping', () => {
  it('renders custody statuses in words', () => {
    expect(custodyLabel('PARKED')).toBe('Parked')
    expect(custodyLabel('RETRIEVAL_REQUESTED')).toBe('Retrieval requested')
    expect(custodyLabel('READY_FOR_PICKUP')).toBe('Ready for pickup')
  })

  it('does not invent a label for an unknown status', () => {
    expect(custodyLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW')
    expect(custodyLabel(null)).toBe('Unknown')
  })

  it('maps security posture to a label and a tone', () => {
    expect(securityLabel('SECURE')).toBe('Secure')
    expect(securityLabel('BREACH')).toBe('Breach')
    expect(securityTone('SECURE')).toBe('badge--success')
    expect(securityTone('BREACH')).toBe('badge--danger')
    expect(securityTone('NOPE')).toBe('badge--muted')
  })
})

describe('relativeTime', () => {
  const now = new Date('2026-09-09T12:00:00Z').getTime()

  it('describes recent times in minutes', () => {
    expect(relativeTime('2026-09-09T11:48:00Z', now)).toBe('12 minutes ago')
    // Only sub-half-minute deltas collapse to "just now"; JS Math.round(0.5)
    // is 1, so 30 seconds reads as one minute rather than as immediate.
    expect(relativeTime('2026-09-09T11:59:45Z', now)).toBe('just now')
  })

  it('does not pluralise a single unit', () => {
    expect(relativeTime('2026-09-09T11:59:00Z', now)).toBe('1 minute ago')
    expect(relativeTime('2026-09-09T11:00:00Z', now)).toBe('1 hour ago')
  })

  it('rolls up to hours and days', () => {
    expect(relativeTime('2026-09-09T09:00:00Z', now)).toBe('3 hours ago')
    expect(relativeTime('2026-09-07T12:00:00Z', now)).toBe('2 days ago')
  })

  it('returns null rather than a nonsense string for bad input', () => {
    expect(relativeTime(null, now)).toBeNull()
    expect(relativeTime('not-a-date', now)).toBeNull()
  })

  it('never reports a negative duration for a future timestamp', () => {
    expect(relativeTime('2026-09-09T12:30:00Z', now)).toBe('just now')
  })
})
