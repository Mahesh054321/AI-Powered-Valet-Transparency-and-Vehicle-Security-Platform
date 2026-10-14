import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost } from './apiClient'
import { classifyFeatures, fetchModelInfo, mlBlockFromFix } from './securityMlService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

describe('securityMlService', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
  })

  it('reads the model metadata endpoint', async () => {
    vi.mocked(apiGet).mockResolvedValue({ available: true })

    await fetchModelInfo()

    expect(apiGet).toHaveBeenCalledWith('/v1/ml/model')
  })

  it('posts every feature it was given, unmodified', async () => {
    vi.mocked(apiPost).mockResolvedValue({ classification: 'NORMAL' })
    const features = { speed_kmph: 0, zone_breach: 0 }

    await classifyFeatures(features)

    expect(apiPost).toHaveBeenCalledWith('/v1/ml/classify', features)
    // The backend rejects a partial feature set rather than defaulting the gap
    // to zero, so the service must not pad or filter the object it forwards.
    expect(vi.mocked(apiPost).mock.calls[0][1]).toBe(features)
  })

  describe('mlBlockFromFix', () => {
    it('returns null when the fix carries no classifier verdict', () => {
      // Absent means "no classifier ran", which the UI renders differently from
      // a clean result. Collapsing the two would imply a verdict was reached.
      expect(mlBlockFromFix({ movement_status: 'NORMAL' })).toBeNull()
      expect(mlBlockFromFix(null)).toBeNull()
      expect(mlBlockFromFix(undefined)).toBeNull()
    })

    it('renames the model version to the frontend convention', () => {
      const block = mlBlockFromFix({
        ml: {
          classification: 'SUSPICIOUS',
          probability: 0.87,
          model_version: 'security-prototype/1.0.0',
        },
      })

      expect(block).toEqual({
        classification: 'SUSPICIOUS',
        probability: 0.87,
        modelVersion: 'security-prototype/1.0.0',
      })
    })
  })
})
