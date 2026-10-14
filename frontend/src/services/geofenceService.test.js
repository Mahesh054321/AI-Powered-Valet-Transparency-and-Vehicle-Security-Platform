import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPatch, apiPost } from './apiClient'
import {
  acknowledgeAlert,
  createGeofence,
  fetchAlerts,
  fetchFixes,
  fetchGeofences,
  fetchSimulatorScenarios,
  fetchTracking,
  postGpsEvents,
  resolveAlert,
  simulateTrack,
  updateGeofence,
} from './geofenceService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
}))

describe('geofenceService', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    vi.mocked(apiPatch).mockReset()
  })

  it('reads the tracking view for a custody session', async () => {
    vi.mocked(apiGet).mockResolvedValue({ custody_session_id: 'c-1' })

    await fetchTracking('c-1')

    expect(apiGet).toHaveBeenCalledWith('/v1/gps/c-1')
  })

  it('passes paging and a status filter through to the fixes endpoint', async () => {
    vi.mocked(apiGet).mockResolvedValue({ fixes: [] })

    await fetchFixes('c-1', { page: 2, pageSize: 25, status: 'BREACH' })

    const path = vi.mocked(apiGet).mock.calls[0][0]
    expect(path).toContain('/v1/gps/c-1/fixes?')
    expect(path).toContain('page=2')
    expect(path).toContain('page_size=25')
    expect(path).toContain('status=BREACH')
  })

  it('omits the status parameter when no filter is given', async () => {
    vi.mocked(apiGet).mockResolvedValue({ fixes: [] })

    await fetchFixes('c-1')

    expect(vi.mocked(apiGet).mock.calls[0][0]).not.toContain('status=')
  })

  it('posts a single fix to the events endpoint', async () => {
    vi.mocked(apiPost).mockResolvedValue({ movement_status: 'NORMAL' })
    const fix = { custody_id: 'c-1', latitude: 27.49, longitude: 77.67, speed: 3 }

    await postGpsEvents(fix)

    expect(apiPost).toHaveBeenCalledWith('/v1/gps/events', fix)
  })

  it('posts a batch under the same endpoint, unchanged', async () => {
    vi.mocked(apiPost).mockResolvedValue({ accepted: 2 })
    const batch = { fixes: [{ custody_id: 'c-1', timestamp: 'x' }] }

    await postGpsEvents(batch)

    expect(apiPost).toHaveBeenCalledWith('/v1/gps/events', batch)
  })

  it('creates a geofence from centre and radius', async () => {
    vi.mocked(apiPost).mockResolvedValue({ id: 'z-1' })
    const payload = { code: 'Z1', name: 'Yard', latitude: 27.49, longitude: 77.67, radius: 100 }

    await createGeofence(payload)

    expect(apiPost).toHaveBeenCalledWith('/v1/geofences', payload)
  })

  it('reads geofences, with an optional status filter', async () => {
    vi.mocked(apiGet).mockResolvedValue({ zones: [] })

    await fetchGeofences()
    await fetchGeofences('ACTIVE')

    expect(vi.mocked(apiGet).mock.calls[0][0]).toBe('/v1/geofences')
    expect(vi.mocked(apiGet).mock.calls[1][0]).toBe('/v1/geofences?status=ACTIVE')
  })

  it('patches a geofence by zone id', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ id: 'z-1' })

    await updateGeofence('z-1', { radius: 150 })

    expect(apiPatch).toHaveBeenCalledWith('/v1/geofences/z-1', { radius: 150 })
  })

  it('filters alerts by session, status and severity', async () => {
    vi.mocked(apiGet).mockResolvedValue({ alerts: [] })

    await fetchAlerts({
      custodySessionId: 'c-1',
      status: 'OPEN',
      severity: 'HIGH',
      page: 1,
      pageSize: 20,
    })

    const path = vi.mocked(apiGet).mock.calls[0][0]
    expect(path).toContain('/v1/alerts?')
    expect(path).toContain('custody_session_id=c-1')
    expect(path).toContain('status=OPEN')
    expect(path).toContain('severity=HIGH')
    expect(path).toContain('page_size=20')
  })

  it('acknowledges an alert with an empty body', async () => {
    vi.mocked(apiPost).mockResolvedValue({ status: 'ACKNOWLEDGED' })

    await acknowledgeAlert('a-1')

    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/a-1/acknowledge', {})
  })

  it('resolves an alert, carrying notes', async () => {
    vi.mocked(apiPost).mockResolvedValue({ status: 'RESOLVED' })

    await resolveAlert('a-1', { notes: 'Vehicle relocated.' })

    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/a-1/resolve', {
      notes: 'Vehicle relocated.',
      dismiss: false,
    })
  })

  it('keeps a dismissal distinct from a resolution', async () => {
    vi.mocked(apiPost).mockResolvedValue({ status: 'DISMISSED' })

    await resolveAlert('a-1', { dismiss: true, notes: 'GPS multipath.' })

    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/a-1/resolve', {
      notes: 'GPS multipath.',
      dismiss: true,
    })
  })

  it('sends a null note rather than an undefined one', async () => {
    vi.mocked(apiPost).mockResolvedValue({ status: 'RESOLVED' })

    await resolveAlert('a-1')

    expect(apiPost).toHaveBeenCalledWith('/v1/alerts/a-1/resolve', {
      notes: null,
      dismiss: false,
    })
  })

  it('requests the simulator catalogue', async () => {
    vi.mocked(apiGet).mockResolvedValue({ scenarios: [] })

    await fetchSimulatorScenarios()

    expect(apiGet).toHaveBeenCalledWith('/v1/gps/simulate/scenarios')
  })

  it('asks the simulator for a track, mapping camelCase to the API names', async () => {
    vi.mocked(apiPost).mockResolvedValue({ fix_count: 8 })

    await simulateTrack('c-1', { scenario: 'unauthorised', seed: 42, zoneId: 'z-1' })

    expect(apiPost).toHaveBeenCalledWith('/v1/gps/simulate', {
      custody_id: 'c-1',
      scenario: 'unauthorised',
      seed: 42,
      zone_id: 'z-1',
    })
  })

  it('sends null rather than omitting an absent seed', async () => {
    vi.mocked(apiPost).mockResolvedValue({ fix_count: 8 })

    await simulateTrack('c-1', { scenario: 'parked' })

    expect(apiPost).toHaveBeenCalledWith('/v1/gps/simulate', {
      custody_id: 'c-1',
      scenario: 'parked',
      seed: null,
      zone_id: null,
    })
  })
})
