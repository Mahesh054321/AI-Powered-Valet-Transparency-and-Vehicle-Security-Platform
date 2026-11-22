/**
 * Server-Sent Events (SSE) hook for real-time vehicle GPS telemetry.
 *
 * Connects to `/api/v1/gps/stream/:custodyId` with authentication token.
 * Receives initial full view snapshot, followed by streaming live position
 * updates, boundary status transitions, and real-time security alerts.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { APP_CONFIG } from '../config/appConfig'
import { ENDPOINTS } from '../services/endpoints'
import { readStoredToken } from '../utils/tokenStorage'

export function useLiveGpsStream(custodyId, { onAlert, onFix, enabled = true } = {}) {
  const [streamStatus, setStreamStatus] = useState('disconnected') // 'connecting' | 'connected' | 'reconnecting' | 'disconnected'
  const [liveView, setLiveView] = useState(null)
  const [latestFix, setLatestFix] = useState(null)
  const [streamError, setStreamError] = useState(null)
  const eventSourceRef = useRef(null)
  const callbacksRef = useRef({ onAlert, onFix })

  useEffect(() => {
    callbacksRef.current = { onAlert, onFix }
  }, [onAlert, onFix])

  const connect = useCallback(() => {
    if (!custodyId || !enabled) return
    if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
      setStreamStatus('unsupported')
      return
    }

    const token = readStoredToken()
    if (!token) {
      setStreamStatus('unauthenticated')
      return
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }

    setStreamStatus('connecting')
    setStreamError(null)

    const base = APP_CONFIG.apiBaseUrl.replace(/\/+$/, '')
    const streamPath = ENDPOINTS.gpsStream(custodyId)
    const url = `${base}${streamPath}?token=${encodeURIComponent(token)}`

    try {
      const es = new window.EventSource(url)
      eventSourceRef.current = es

      es.onopen = () => {
        setStreamStatus('connected')
        setStreamError(null)
      }

      es.addEventListener('snapshot', (e) => {
        try {
          const payload = JSON.parse(e.data)
          setLiveView(payload)
          if (payload.latest_fix) {
            setLatestFix(payload.latest_fix)
          }
        } catch (err) {
          console.warn('Failed to parse SSE snapshot:', err)
        }
      })

      es.addEventListener('location', (e) => {
        try {
          const payload = JSON.parse(e.data)
          const newFix = payload.fix || payload.latest_fix
          if (newFix) {
            setLatestFix(newFix)
            callbacksRef.current.onFix?.(newFix)
          }
          setLiveView((prev) => {
            if (!prev) return null
            const updatedTrack = newFix ? [...(prev.track || []), newFix] : prev.track
            return {
              ...prev,
              latest_fix: newFix || prev.latest_fix,
              movement_status: payload.movement_status || prev.movement_status,
              boundary_state: payload.boundary_state || prev.boundary_state,
              freshness: payload.freshness || prev.freshness || 'OFFLINE',
              last_updated_at:
                payload.last_updated_at || newFix?.recorded_at || prev.last_updated_at || null,
              track: updatedTrack,
              fix_count: (prev.fix_count || 0) + (newFix ? 1 : 0),
            }
          })
        } catch (err) {
          console.warn('Failed to parse SSE location update:', err)
        }
      })

      es.addEventListener('alert', (e) => {
        try {
          const alert = JSON.parse(e.data)
          callbacksRef.current.onAlert?.(alert)
          setLiveView((prev) => {
            if (!prev) return null
            const existing = prev.alerts || []
            if (existing.some((a) => a.id === alert.id)) return prev
            return {
              ...prev,
              alerts: [alert, ...existing],
              open_alert_count: (prev.open_alert_count || 0) + 1,
            }
          })
        } catch (err) {
          console.warn('Failed to parse SSE alert update:', err)
        }
      })

      es.onerror = (err) => {
        // SSE connection dropped; EventSource automatically attempts to reconnect.
        setStreamStatus('reconnecting')
        setStreamError(err)
      }
    } catch (err) {
      setStreamStatus('disconnected')
      setStreamError(err)
    }
  }, [custodyId, enabled])

  useEffect(() => {
    const initialConnectTimeout = window.setTimeout(connect, 0)

    return () => {
      window.clearTimeout(initialConnectTimeout)
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
    }
  }, [connect])

  return {
    isStreaming: streamStatus === 'connected',
    streamStatus,
    liveView,
    latestFix,
    streamError,
    reconnect: connect,
  }
}
