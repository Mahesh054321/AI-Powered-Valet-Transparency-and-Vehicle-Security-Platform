/**
 * Hook for capturing and streaming real-time GPS telemetry from an authorized mobile browser.
 *
 * Requirements:
 *  - Explicit user activation (consent dialog / toggle).
 *  - Prominent disclosure that the mobile phone itself is being tracked and
 *    must stay with the vehicle to represent its true position.
 *  - Accuracy classification: accurate vs uncertain based on radius.
 *  - Submits position fixes to `POST /gps/mobile` stamped with `MOBILE_GPS`.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { postMobileGps } from '../services/geofenceService'
import { normaliseError } from '../services/apiClient'

export const MOBILE_GPS_DISCLOSURE =
  'Your mobile phone is being tracked directly via browser geolocation. Keep this device inside the vehicle so its coordinates accurately reflect the vehicle position.'

export function useMobileGeolocation(
  custodyId,
  { onFixSuccess, onFixError, onPositionChange, autoPost = true } = {},
) {
  const isSupported = typeof window !== 'undefined' && 'geolocation' in navigator
  const [isTracking, setIsTracking] = useState(false)
  const [currentPosition, setCurrentPosition] = useState(null)
  const [accuracyMetres, setAccuracyMetres] = useState(null)
  const [permissionState, setPermissionState] = useState(
    isSupported ? 'prompt' : 'unsupported',
  ) // 'prompt' | 'granted' | 'denied' | 'unsupported'
  const [lastTransmittedAt, setLastTransmittedAt] = useState(null)
  const [lastError, setLastError] = useState(null)
  const watchIdRef = useRef(null)
  const onPositionChangeRef = useRef(onPositionChange)

  useEffect(() => {
    onPositionChangeRef.current = onPositionChange
  }, [onPositionChange])

  // Query permissions if supported
  useEffect(() => {
    if (!isSupported) return
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions
        .query({ name: 'geolocation' })
        .then((result) => {
          setPermissionState(result.state)
          result.onchange = () => setPermissionState(result.state)
        })
        .catch(() => {
          // Fallback to prompt
          setPermissionState('prompt')
        })
    }
  }, [isSupported])

  const transmitFix = useCallback(
    async (coords, timestamp) => {
      if (!custodyId || !autoPost) return
      try {
        const payload = {
          custody_id: custodyId,
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy_metres: coords.accuracy,
          speed: coords.speed != null ? Math.max(0, coords.speed * 3.6) : 0.0, // Convert m/s to km/h
          heading: coords.heading != null ? coords.heading : 0.0,
          timestamp: new Date(timestamp).toISOString(),
          device_id: typeof navigator !== 'undefined' ? `${navigator.userAgent.slice(0, 32)}` : 'mobile-browser',
        }
        const result = await postMobileGps(payload)
        setLastTransmittedAt(new Date())
        setLastError(null)
        onFixSuccess?.(result)
      } catch (err) {
        const norm = normaliseError(err)
        setLastError(norm.message)
        onFixError?.(norm)
      }
    },
    [custodyId, autoPost, onFixSuccess, onFixError],
  )

  const startTracking = useCallback(() => {
    if (!isSupported) {
      setLastError('Geolocation is not supported by your browser.')
      return
    }
    if (watchIdRef.current !== null) return

    setLastError(null)
    setIsTracking(true)

    const options = {
      enableHighAccuracy: true,
      timeout: 10000,
      maximumAge: 2000,
    }

    const successHandler = (pos) => {
      setPermissionState('granted')
      setCurrentPosition({
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
      })
      setAccuracyMetres(pos.coords.accuracy)
      onPositionChangeRef.current?.({
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy_metres: pos.coords.accuracy,
        recorded_at: new Date(pos.timestamp).toISOString(),
      })
      transmitFix(pos.coords, pos.timestamp)
    }

    const errorHandler = (err) => {
      if (err.code === 1) {
        setPermissionState('denied')
        if (watchIdRef.current !== null) {
          navigator.geolocation.clearWatch(watchIdRef.current)
          watchIdRef.current = null
        }
        setIsTracking(false)
        setLastError('Location access was denied. Please allow location access in your browser settings.')
      } else if (err.code === 2) {
        setLastError('Location position unavailable. Waiting for GPS signal.')
      } else if (err.code === 3) {
        setLastError('Location request timed out. Retrying…')
      } else {
        setLastError(err.message || 'Error acquiring location fix.')
      }
    }

    // A watch supplies the initial fix and subsequent updates without posting
    // the same initial reading twice through separate one-shot and watch calls.
    try {
      watchIdRef.current = navigator.geolocation.watchPosition(
        successHandler,
        errorHandler,
        options,
      )
    } catch (err) {
      setIsTracking(false)
      setLastError(err.message || 'Could not start location tracking.')
    }
  }, [isSupported, transmitFix])

  const stopTracking = useCallback(() => {
    if (watchIdRef.current !== null && isSupported) {
      navigator.geolocation.clearWatch(watchIdRef.current)
      watchIdRef.current = null
    }
    setIsTracking(false)
  }, [isSupported])

  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null && typeof navigator !== 'undefined' && navigator.geolocation) {
        navigator.geolocation.clearWatch(watchIdRef.current)
      }
    }
  }, [])

  return {
    isSupported,
    isTracking,
    permissionState,
    currentPosition,
    accuracyMetres,
    lastTransmittedAt,
    lastError,
    startTracking,
    stopTracking,
    disclosure: MOBILE_GPS_DISCLOSURE,
  }
}
