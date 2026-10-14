import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPatch, apiPost } from './apiClient'
import {
  cancelTicket,
  checkInTicket,
  completeTicket,
  issueTicket,
  updateCustodyStatus,
} from './ticketService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
}))

describe('ticketService', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    vi.mocked(apiPatch).mockReset()
  })

  it('issues a ticket with the vehicle id only', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await issueTicket({ vehicleId: 'v-1' })

    expect(apiPost).toHaveBeenCalledWith('/v1/tickets', { vehicle_id: 'v-1' })
  })

  it('includes special instructions when given', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await issueTicket({ vehicleId: 'v-1', specialInstructions: 'Leave in shade' })

    expect(apiPost.mock.calls[0][1].special_instructions).toBe('Leave in shade')
  })

  it('sends photo references, never image bytes', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })
    const ref = { path: 'inspections/2026/09/07/abc.png', sha256: 'a'.repeat(64) }

    await checkInTicket('t-1', { zoneId: 'z-1', photoRefs: [ref] })

    const [path, payload] = apiPost.mock.calls[0]
    expect(path).toBe('/v1/tickets/t-1/check-in')
    expect(payload.zone_id).toBe('z-1')
    expect(payload.photo_refs).toEqual([ref])
    // The schema stores references only; a File or blob here would be a bug.
    expect(JSON.stringify(payload)).not.toContain('File')
  })

  it('omits an empty photo list rather than sending an empty array', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await checkInTicket('t-1', { zoneId: 'z-1', photoRefs: [] })

    expect(apiPost.mock.calls[0][1]).toEqual({ zone_id: 'z-1' })
  })

  it('converts the drop-off readings to numbers', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await checkInTicket('t-1', { zoneId: 'z-1', odometer: '45210', fuelLevel: '60' })

    const payload = apiPost.mock.calls[0][1]
    expect(payload.odometer_at_drop_off).toBe(45210)
    expect(payload.fuel_level_percent).toBe(60)
  })

  it('sends a zero odometer rather than dropping it', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await completeTicket('t-1', { odometer: '0' })

    // A falsy check would silently drop a genuine zero reading.
    expect(apiPost.mock.calls[0][1].odometer_at_pickup).toBe(0)
  })

  it('sends condition_accepted: false rather than omitting it', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await completeTicket('t-1', { conditionAccepted: false })

    expect(apiPost.mock.calls[0][1].condition_accepted).toBe(false)
  })

  it('cancels with an optional reason', async () => {
    vi.mocked(apiPost).mockResolvedValue({ ticket: {} })

    await cancelTicket('t-1')
    expect(apiPost).toHaveBeenCalledWith('/v1/tickets/t-1/cancel', {})

    await cancelTicket('t-1', 'Wrong vehicle')
    expect(apiPost.mock.calls[1][1]).toEqual({ reason: 'Wrong vehicle' })
  })

  it('patches the custody session status', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ custody_session: {} })

    await updateCustodyStatus('s-1', 'READY_FOR_PICKUP')

    expect(apiPatch).toHaveBeenCalledWith('/v1/custody-sessions/s-1', {
      status: 'READY_FOR_PICKUP',
    })
  })
})
