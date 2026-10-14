import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPatch, apiPost, apiUpload } from './apiClient'
import {
  captureDropOff,
  capturePickup,
  fetchConditionReport,
  fetchDemoScenarios,
  runDemoComparison,
  verifyDetection,
} from './inspectionService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiUpload: vi.fn(),
}))

/**
 * A real `File`. jsdom's FormData enforces the spec and rejects anything that
 * is not a Blob, so a plain object stand-in cannot be appended.
 */
function fakeFile(name = 'photo.png') {
  return new File(['fake-png-bytes'], name, { type: 'image/png' })
}

describe('inspectionService', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    vi.mocked(apiPatch).mockReset()
    vi.mocked(apiUpload).mockReset()
  })

  it('posts drop-off photos to the drop-off endpoint', async () => {
    vi.mocked(apiUpload).mockResolvedValue({ stage: 'DROP_OFF' })

    await captureDropOff({
      custodyId: 'custody-1',
      photos: [{ file: fakeFile(), angle: 'FRONT' }],
    })

    const [path, form] = vi.mocked(apiUpload).mock.calls[0]
    expect(path).toBe('/v1/inspections/dropoff')
    expect(form.get('custody_id')).toBe('custody-1')
    expect(form.getAll('images')).toHaveLength(1)
    expect(form.getAll('angles')).toEqual(['FRONT'])
  })

  it('posts pickup photos to the pickup endpoint', async () => {
    vi.mocked(apiUpload).mockResolvedValue({ stage: 'PICKUP' })

    await capturePickup({
      custodyId: 'custody-1',
      photos: [{ file: fakeFile(), angle: 'LEFT_SIDE' }],
    })

    expect(vi.mocked(apiUpload).mock.calls[0][0]).toBe('/v1/inspections/pickup')
  })

  it('sends one angle per photo, in parallel', async () => {
    vi.mocked(apiUpload).mockResolvedValue({})

    await captureDropOff({
      custodyId: 'custody-1',
      photos: [
        { file: fakeFile('a.png'), angle: 'FRONT' },
        { file: fakeFile('b.png'), angle: 'REAR' },
        { file: fakeFile('c.png'), angle: 'LEFT_SIDE' },
      ],
    })

    const form = vi.mocked(apiUpload).mock.calls[0][1]
    // The backend rejects a count mismatch, so the two lists must stay aligned.
    expect(form.getAll('images')).toHaveLength(3)
    expect(form.getAll('angles')).toEqual(['FRONT', 'REAR', 'LEFT_SIDE'])
  })

  it('omits optional fields rather than sending empty strings', async () => {
    vi.mocked(apiUpload).mockResolvedValue({})

    await captureDropOff({
      custodyId: 'custody-1',
      photos: [{ file: fakeFile(), angle: 'FRONT' }],
      notes: '',
      odometer: '',
      location: '',
    })

    const form = vi.mocked(apiUpload).mock.calls[0][1]
    expect(form.has('notes')).toBe(false)
    expect(form.has('odometer')).toBe(false)
    expect(form.has('location')).toBe(false)
  })

  it('includes optional fields when they are provided', async () => {
    vi.mocked(apiUpload).mockResolvedValue({})

    await capturePickup({
      custodyId: 'custody-1',
      photos: [{ file: fakeFile(), angle: 'FRONT' }],
      notes: 'Customer present',
      odometer: 42100,
      location: 'Bay 3',
    })

    const form = vi.mocked(apiUpload).mock.calls[0][1]
    expect(form.get('notes')).toBe('Customer present')
    expect(form.get('odometer')).toBe('42100')
    expect(form.get('location')).toBe('Bay 3')
  })

  it('fetches the report by custody session id', async () => {
    vi.mocked(apiGet).mockResolvedValue({ custody_session_id: 'custody-1' })

    await fetchConditionReport('custody-1')

    expect(apiGet).toHaveBeenCalledWith('/v1/inspections/custody-1')
  })

  it('sends the verification decision as a PATCH', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ verification_status: 'CONFIRMED' })

    await verifyDetection('det-1', { status: 'CONFIRMED', notes: 'Visible scuff' })

    expect(apiPatch).toHaveBeenCalledWith('/v1/inspections/detections/det-1/verification', {
      status: 'CONFIRMED',
      notes: 'Visible scuff',
    })
  })

  it('omits the note when none is given', async () => {
    vi.mocked(apiPatch).mockResolvedValue({})

    await verifyDetection('det-1', { status: 'REJECTED' })

    expect(apiPatch).toHaveBeenCalledWith('/v1/inspections/detections/det-1/verification', {
      status: 'REJECTED',
    })
  })

  it('lists the demo scenarios', async () => {
    vi.mocked(apiGet).mockResolvedValue({ scenarios: [] })

    await fetchDemoScenarios()

    expect(apiGet).toHaveBeenCalledWith('/v1/inspections/demo/scenarios')
  })

  it('runs the demo with the chosen scenario', async () => {
    vi.mocked(apiPost).mockResolvedValue({ synthetic: true })

    await runDemoComparison('rust')

    expect(apiPost).toHaveBeenCalledWith('/v1/inspections/demo', { scenario: 'rust' })
  })
})
