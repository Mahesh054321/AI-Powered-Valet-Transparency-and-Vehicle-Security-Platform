import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useLiveGpsStream } from './useLiveGpsStream'
import * as tokenStorage from '../utils/tokenStorage'

describe('useLiveGpsStream', () => {
  let listeners = {}
  let mockEventSourceInstance

  beforeEach(() => {
    listeners = {}
    vi.spyOn(tokenStorage, 'readStoredToken').mockReturnValue('mock-jwt-token')

    mockEventSourceInstance = {
      addEventListener: vi.fn((event, cb) => {
        listeners[event] = cb
      }),
      close: vi.fn(),
      onopen: null,
      onerror: null,
    }

    window.EventSource = vi.fn(function (url) {
      this.url = url
      mockEventSourceInstance.url = url
      return mockEventSourceInstance
    })
  })

  it('connects to SSE endpoint with bearer token and receives snapshot', async () => {
    const { result } = renderHook(() => useLiveGpsStream('custody-100'))

    await waitFor(() => expect(window.EventSource).toHaveBeenCalled())
    expect(window.EventSource).toHaveBeenCalled()
    const calledUrl = window.EventSource.mock.calls[0][0]
    expect(calledUrl).toContain('/api/v1/gps/stream/custody-100')
    expect(calledUrl).toContain('token=mock-jwt-token')

    // Simulate open
    act(() => {
      mockEventSourceInstance.onopen?.()
    })
    expect(result.current.streamStatus).toBe('connected')
    expect(result.current.isStreaming).toBe(true)

    // Simulate snapshot
    act(() => {
      listeners['snapshot']?.({
        data: JSON.stringify({
          custody_session_id: 'custody-100',
          movement_status: 'NORMAL',
          latest_fix: { latitude: 28.45, longitude: 77.02 },
        }),
      })
    })

    expect(result.current.liveView).toEqual(
      expect.objectContaining({
        custody_session_id: 'custody-100',
        movement_status: 'NORMAL',
      }),
    )
    expect(result.current.latestFix).toEqual({ latitude: 28.45, longitude: 77.02 })
  })

  it('receives live location event and updates latestFix and movement_status', async () => {
    const onFix = vi.fn()
    const { result } = renderHook(() => useLiveGpsStream('custody-100', { onFix }))
    await waitFor(() => expect(window.EventSource).toHaveBeenCalled())

    act(() => {
      mockEventSourceInstance.onopen?.()
      listeners['snapshot']?.({
        data: JSON.stringify({
          custody_session_id: 'custody-100',
          track: [],
        }),
      })
    })

    // Simulate location event
    const liveFix = { id: 'fix-99', latitude: 28.46, longitude: 77.03, movement_status: 'BREACH' }
    act(() => {
      listeners['location']?.({
        data: JSON.stringify({
          movement_status: 'BREACH',
          boundary_state: 'OUTSIDE',
          freshness: 'LIVE',
          fix: liveFix,
        }),
      })
    })

    expect(result.current.latestFix).toEqual(liveFix)
    expect(result.current.liveView.movement_status).toBe('BREACH')
    expect(result.current.liveView.boundary_state).toBe('OUTSIDE')
    expect(onFix).toHaveBeenCalledWith(liveFix)
  })

  it('closes EventSource on unmount', async () => {
    const { unmount } = renderHook(() => useLiveGpsStream('custody-100'))
    await waitFor(() => expect(window.EventSource).toHaveBeenCalled())
    unmount()
    expect(mockEventSourceInstance.close).toHaveBeenCalled()
  })
})
