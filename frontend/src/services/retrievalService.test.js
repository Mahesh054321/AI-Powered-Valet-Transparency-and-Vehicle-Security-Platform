import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost } from './apiClient'
import {
  fetchRetrievalModelInfo,
  fetchRetrievalPrediction,
  formatWait,
  requestRetrievalPrediction,
  retrievalProvenance,
  scoreRetrievalWhatIf,
  topContributions,
} from './retrievalService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

describe('retrievalService endpoints', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
  })

  it('posts the custody session id to the predict endpoint', async () => {
    vi.mocked(apiPost).mockResolvedValue({ estimated_minutes: 8 })

    await requestRetrievalPrediction('custody-1')

    expect(apiPost).toHaveBeenCalledWith('/v1/retrieval/predict', {
      custody_session_id: 'custody-1',
      persist: true,
    })
  })

  it('can ask for an estimate without storing it', async () => {
    vi.mocked(apiPost).mockResolvedValue({})

    await requestRetrievalPrediction('custody-1', { persist: false })

    expect(apiPost).toHaveBeenCalledWith('/v1/retrieval/predict', {
      custody_session_id: 'custody-1',
      persist: false,
    })
  })

  it('reads a stored estimate by custody session id', async () => {
    vi.mocked(apiGet).mockResolvedValue({ predicted_wait_minutes: 8 })

    await fetchRetrievalPrediction('custody-9')

    expect(apiGet).toHaveBeenCalledWith('/v1/retrieval/custody-9')
  })

  it('reads the model metadata endpoint', async () => {
    vi.mocked(apiGet).mockResolvedValue({ available: true })

    await fetchRetrievalModelInfo()

    expect(apiGet).toHaveBeenCalledWith('/v1/retrieval/model')
  })

  it('posts a what-if feature set unmodified', async () => {
    vi.mocked(apiPost).mockResolvedValue({ estimated_minutes: 13 })
    const features = { queue_size: 5, queue_position: 4 }

    await scoreRetrievalWhatIf(features)

    expect(apiPost).toHaveBeenCalledWith('/v1/retrieval/predict', { features })
  })
})

describe('topContributions', () => {
  const estimate = {
    contributions: {
      queue_position: 3.1,
      zone_capacity: -1.2,
      hour_sin: 0.4,
      vehicle_flagged: 0.02,
      active_valets: -0.01,
    },
  }

  it('returns the largest effects first, signed', () => {
    const result = topContributions(estimate)

    expect(result.map((entry) => entry.feature)).toEqual([
      'queue_position',
      'zone_capacity',
      'hour_sin',
    ])
    expect(result[0].minutes).toBe(3.1)
    expect(result[1].minutes).toBe(-1.2)
  })

  it('drops effects too small to have moved the answer', () => {
    // Listing six factors that between them change the estimate by a few
    // seconds would bury the two that matter.
    const features = topContributions(estimate).map((entry) => entry.feature)

    expect(features).not.toContain('vehicle_flagged')
    expect(features).not.toContain('active_valets')
  })

  it('honours the requested limit', () => {
    expect(topContributions(estimate, 1)).toHaveLength(1)
  })

  it('returns an empty list rather than throwing on a missing block', () => {
    expect(topContributions(null)).toEqual([])
    expect(topContributions({})).toEqual([])
    expect(topContributions({ contributions: 'nope' })).toEqual([])
  })
})

describe('formatWait', () => {
  it('shows a range when the backend supplies bounds', () => {
    // A single number implies a precision this model does not have.
    expect(
      formatWait({ estimated_minutes: 8, min_wait_minutes: 6, max_wait_minutes: 10 })
    ).toBe('6–10 minutes')
  })

  it('falls back to the point estimate when bounds are absent', () => {
    expect(formatWait({ estimated_minutes: 8 })).toBe('8 minutes')
  })

  it('does not pluralise a single minute', () => {
    expect(formatWait({ estimated_minutes: 1 })).toBe('1 minute')
  })

  it('reads the stored field name as well as the live one', () => {
    expect(formatWait({ predicted_wait_minutes: 14 })).toBe('14 minutes')
  })

  it('returns null when there is nothing to show', () => {
    expect(formatWait(null)).toBeNull()
    expect(formatWait({})).toBeNull()
  })
})

describe('retrievalProvenance', () => {
  it('prefers the backend wording over the local fallback', () => {
    // What the UI says must be what the service that produced the number said.
    const result = retrievalProvenance(
      {
        data_basis: 'synthetic historical retrieval records',
        disclaimer: 'Backend disclaimer.',
        trained_on_synthetic_data: true,
        model_version: 'retrieval-baseline/1.0.0',
        method: 'linear-ridge-v1',
      },
      'Local fallback.'
    )

    expect(result.dataBasis).toBe('synthetic historical retrieval records')
    expect(result.disclaimer).toBe('Backend disclaimer.')
    expect(result.modelVersion).toBe('retrieval-baseline/1.0.0')
  })

  it('falls back to the local notice when the response omits one', () => {
    const result = retrievalProvenance({}, 'Local fallback.')

    expect(result.disclaimer).toBe('Local fallback.')
    // Defaults to "synthetic" rather than to "unknown": the safe direction when
    // a field is missing is to keep the caveat, not to drop it.
    expect(result.trainedOnSyntheticData).toBe(true)
  })
})
