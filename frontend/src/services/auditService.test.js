/**
 * Tests for the audit trail service.
 *
 * The properties worth pinning down are the ones a reviewer would notice if
 * they broke: that every call is a **read** (the log is append-only, so a
 * service offering a write would be offering a call that can only fail), that
 * filters reach the query string under the names the backend expects, and that
 * the small formatting helpers degrade to something honest rather than
 * `Invalid Date` or an empty cell.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
}))

import { apiGet } from './apiClient'
import {
  actorLabel,
  fetchAuditFilters,
  fetchAuditLog,
  fetchCustodyTimeline,
  fetchVehicleAuditTrail,
  formatEntryTime,
  groupEventsByScope,
  summariseEntry,
} from './auditService'

beforeEach(() => {
  apiGet.mockClear()
})

describe('endpoints', () => {
  it('reads the log at the versioned audit path', () => {
    fetchAuditLog()
    expect(apiGet.mock.calls[0][0]).toBe('/v1/audit')
  })

  it('reads filter options from the filters path', () => {
    fetchAuditFilters()
    expect(apiGet.mock.calls[0][0]).toBe('/v1/audit/filters')
  })

  it('builds the timeline path from the custody id', () => {
    fetchCustodyTimeline('abc-123')
    expect(apiGet.mock.calls[0][0]).toBe('/v1/audit/timeline/abc-123')
  })

  it('builds the vehicle path from the vehicle id', () => {
    fetchVehicleAuditTrail('veh-9')
    expect(apiGet.mock.calls[0][0]).toBe('/v1/audit/vehicles/veh-9')
  })
})

describe('fetchAuditLog query parameters', () => {
  it('sends snake_case names the backend parses', () => {
    fetchAuditLog({
      page: 3,
      pageSize: 25,
      action: 'GPS_BREACH',
      entityType: 'gps_events',
      entityId: 'e1',
      actorId: 'a1',
      custodySessionId: 'c1',
      outcome: 'FAILURE',
      dateFrom: '2026-01-01',
      dateTo: '2026-02-01',
      lifecycleOnly: true,
    })
    expect(apiGet.mock.calls[0][1]).toEqual({
      params: {
        page: 3,
        page_size: 25,
        action: 'GPS_BREACH',
        entity_type: 'gps_events',
        entity_id: 'e1',
        actor_id: 'a1',
        custody_session_id: 'c1',
        outcome: 'FAILURE',
        date_from: '2026-01-01',
        date_to: '2026-02-01',
        lifecycle_only: 'true',
      },
    })
  })

  it('omits unset filters rather than sending empty strings', () => {
    fetchAuditLog({ action: 'USER_LOGIN' })
    expect(apiGet.mock.calls[0][1]).toEqual({ params: { action: 'USER_LOGIN' } })
  })

  it('sends no params at all when nothing is set', () => {
    fetchAuditLog()
    expect(apiGet.mock.calls[0][1]).toEqual({ params: {} })
  })

  it('does not send lifecycle_only when it is false', () => {
    fetchAuditLog({ lifecycleOnly: false })
    expect(apiGet.mock.calls[0][1].params.lifecycle_only).toBeUndefined()
  })
})

describe('summariseEntry', () => {
  it('prefers the description written when the event happened', () => {
    expect(summariseEntry({ description: 'Checked in UP78AB1234', action: 'X' })).toBe(
      'Checked in UP78AB1234',
    )
  })

  it('falls back to label plus entity when there is no description', () => {
    expect(
      summariseEntry({ label: 'Vehicle checked in', action: 'VEHICLE_CHECKED_IN', entity_type: 'custody_sessions' }),
    ).toBe('Vehicle checked in on custody_sessions')
  })

  it('falls back to the raw action when there is no label either', () => {
    expect(summariseEntry({ action: 'EXPORT', entity_type: 'users' })).toBe('EXPORT on users')
  })

  it('returns an empty string for a missing entry rather than throwing', () => {
    expect(summariseEntry(null)).toBe('')
    expect(summariseEntry(undefined)).toBe('')
  })
})

describe('formatEntryTime', () => {
  it('formats a real timestamp', () => {
    const out = formatEntryTime('2026-09-09T17:32:48.292441+00:00')
    expect(out).toBeTruthy()
    expect(out).toContain('2026')
  })

  it('returns an empty string for a missing value', () => {
    expect(formatEntryTime(null)).toBe('')
    expect(formatEntryTime('')).toBe('')
  })

  it('returns an empty string rather than Invalid Date for garbage', () => {
    const out = formatEntryTime('not-a-timestamp')
    expect(out).toBe('')
    expect(out).not.toContain('Invalid')
  })
})

describe('actorLabel', () => {
  it('prefers the snapshotted email', () => {
    expect(actorLabel({ actor_email: 'valet@example.com', actor_role: 'VALET_OPERATOR' })).toBe(
      'valet@example.com',
    )
  })

  it('falls back to the role, marked as having no account', () => {
    expect(actorLabel({ actor_role: 'ADMIN' })).toBe('ADMIN (no account)')
  })

  it('says system when neither is present', () => {
    expect(actorLabel({})).toBe('system')
  })

  it('does not throw on a missing entry', () => {
    expect(actorLabel(null)).toBe('')
  })
})

describe('groupEventsByScope', () => {
  it('buckets events by the scope the backend labelled', () => {
    const groups = groupEventsByScope([
      { id: '1', scope: 'provenance' },
      { id: '2', scope: 'login' },
      { id: '3', scope: 'custody' },
      { id: '4', scope: 'custody' },
    ])
    expect(groups.provenance.map((e) => e.id)).toEqual(['1'])
    expect(groups.login.map((e) => e.id)).toEqual(['2'])
    expect(groups.custody.map((e) => e.id)).toEqual(['3', '4'])
  })

  it('preserves order within a group', () => {
    const groups = groupEventsByScope([
      { id: 'a', scope: 'custody' },
      { id: 'b', scope: 'custody' },
    ])
    expect(groups.custody.map((e) => e.id)).toEqual(['a', 'b'])
  })

  it('treats an unknown scope as custody rather than dropping the event', () => {
    const groups = groupEventsByScope([{ id: 'x', scope: 'something-new' }])
    expect(groups.custody.map((e) => e.id)).toEqual(['x'])
  })

  it('handles a missing list', () => {
    const groups = groupEventsByScope(undefined)
    expect(groups).toEqual({ provenance: [], login: [], custody: [] })
  })
})
