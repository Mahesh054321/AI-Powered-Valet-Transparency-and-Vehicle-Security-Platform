import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useMobileGeolocation } from './useMobileGeolocation'
import { postMobileGps } from '../services/geofenceService'

vi.mock('../services/geofenceService', () => ({
  postMobileGps: vi.fn(),
}))

describe('useMobileGeolocation', () => {
  beforeEach(() => {
    vi.mocked(postMobileGps).mockReset()
    vi.mocked(postMobileGps).mockResolvedValue({ success: true })

    // Stub navigator.geolocation
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        watchPosition: vi.fn((success) => {
          success({
            coords: {
              latitude: 28.4595,
              longitude: 77.0266,
              accuracy: 15.0,
              speed: 5.0,
              heading: 90.0,
            },
            timestamp: Date.now(),
          })
          return 123
        }),
        clearWatch: vi.fn(),
      },
    })
  })

  it('exposes tracking disclosure explaining phone tracking vs vehicle location', () => {
    const { result } = renderHook(() => useMobileGeolocation('custody-123'))
    expect(result.current.disclosure).toContain('mobile phone is being tracked')
    expect(result.current.disclosure).toContain('Keep this device inside the vehicle')
    expect(result.current.isTracking).toBe(false)
  })

  it('starts a continuous watch, shows the first fix and transmits MOBILE_GPS telemetry', async () => {
    const onFixSuccess = vi.fn()
    const onPositionChange = vi.fn()
    const { result } = renderHook(() =>
      useMobileGeolocation('custody-123', { onFixSuccess, onPositionChange }),
    )

    await act(async () => {
      result.current.startTracking()
    })

    expect(result.current.isTracking).toBe(true)
    expect(result.current.currentPosition).toEqual({
      latitude: 28.4595,
      longitude: 77.0266,
    })
    expect(result.current.accuracyMetres).toBe(15.0)
    expect(navigator.geolocation.watchPosition).toHaveBeenCalledTimes(1)

    expect(postMobileGps).toHaveBeenCalledWith(
      expect.objectContaining({
        custody_id: 'custody-123',
        latitude: 28.4595,
        longitude: 77.0266,
        accuracy_metres: 15.0,
      }),
    )
    expect(onFixSuccess).toHaveBeenCalled()
    expect(onPositionChange).toHaveBeenCalledWith(
      expect.objectContaining({ latitude: 28.4595, longitude: 77.0266 }),
    )
  })

  it('updates and transmits each later GPS fix from the active watch', async () => {
    const onPositionChange = vi.fn()
    const { result } = renderHook(() =>
      useMobileGeolocation('custody-123', { onPositionChange }),
    )

    act(() => result.current.startTracking())
    const onWatchPosition = navigator.geolocation.watchPosition.mock.calls[0][0]

    await act(async () => {
      onWatchPosition({
        coords: {
          latitude: 28.4601,
          longitude: 77.0272,
          accuracy: 8,
          speed: 0,
          heading: null,
        },
        timestamp: Date.now() + 1000,
      })
    })

    await waitFor(() => expect(postMobileGps).toHaveBeenCalledTimes(2))
    expect(result.current.currentPosition).toEqual({
      latitude: 28.4601,
      longitude: 77.0272,
    })
    expect(onPositionChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ latitude: 28.4601, longitude: 77.0272 }),
    )
  })

  it('clears watch when stopped', async () => {
    const { result } = renderHook(() => useMobileGeolocation('custody-123'))

    await act(async () => {
      result.current.startTracking()
    })
    expect(result.current.isTracking).toBe(true)

    act(() => {
      result.current.stopTracking()
    })
    expect(result.current.isTracking).toBe(false)
    expect(navigator.geolocation.clearWatch).toHaveBeenCalledWith(123)
  })
})
