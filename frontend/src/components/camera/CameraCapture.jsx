/**
 * Browser-camera capture for drop-off and pickup photographs.
 *
 * The callback receives an ordinary JPEG File, so all the existing upload,
 * inspection-angle, validation and audit paths remain unchanged. Permission is
 * requested only after the operator presses the button. On unsupported/blocked
 * browsers, a second input asks the phone's native camera to take a picture.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const MAX_EDGE = 1920

function cameraError(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') {
    return 'Camera access was denied. Allow camera permission in your browser, or use the phone-camera option below.'
  }
  if (error?.name === 'NotFoundError' || error?.name === 'DevicesNotFoundError') {
    return 'No camera was found. You can use the phone-camera option or upload a photo instead.'
  }
  return 'Camera could not start. Check that it is not being used by another app, or use the phone-camera option.'
}

export function CameraCapture({ onCapture, label = 'Capture with Camera', filenamePrefix = 'vehicle', disabled = false }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [ready, setReady] = useState(false)
  const [facing, setFacing] = useState('environment')
  const [error, setError] = useState('')
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const nativeInputRef = useRef(null)

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  useEffect(() => {
    if (!open) return undefined

    let cancelled = false
    setLoading(true)
    setReady(false)
    setError('')

    if (!navigator.mediaDevices?.getUserMedia) {
      setLoading(false)
      setError('Live camera preview is not supported in this browser. Try the phone-camera option below.')
      return undefined
    }

    navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: facing } } })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          // Browser policy can reject play(); metadata events handle readiness.
          const playback = videoRef.current.play?.()
          playback?.catch?.(() => {})
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(cameraError(caught))
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
      stopStream()
    }
  }, [open, facing, stopStream])

  const close = () => {
    stopStream()
    setOpen(false)
    setReady(false)
    setLoading(false)
    setError('')
  }

  const captureFrame = () => {
    const video = videoRef.current
    if (!video || !video.videoWidth || !video.videoHeight) {
      setError('Camera is not ready yet. Wait for the preview and try again.')
      return
    }

    const scale = Math.min(1, MAX_EDGE / Math.max(video.videoWidth, video.videoHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      setError('Unable to capture the camera frame on this device.')
      return
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    canvas.toBlob((blob) => {
      if (!blob) {
        setError('Could not save the photo. Please try again.')
        return
      }
      const stamp = new Date().toISOString().replace(/[:.]/g, '-')
      onCapture(new File([blob], `${filenamePrefix}-${stamp}.jpg`, { type: 'image/jpeg' }))
      close()
    }, 'image/jpeg', 0.9)
  }

  return (
    <>
      <button type="button" className="btn btn--ghost" disabled={disabled} onClick={() => setOpen(true)}>
        {label}
      </button>

      {open && (
        <div className="camera-capture__overlay">
          <section className="camera-capture__dialog" role="dialog" aria-modal="true" aria-label="Take a vehicle photo" onKeyDown={(event) => {
            if (event.key === 'Escape') close()
          }}>
            <div className="camera-capture__header">
              <h3>Take a vehicle photo</h3>
              <button type="button" className="btn btn--ghost btn--sm" onClick={close}>Close camera</button>
            </div>
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="camera-capture__preview"
              onLoadedMetadata={() => { setReady(true); setLoading(false) }}
              aria-label="Live camera preview"
            />
            {loading && <p className="muted" role="status">Starting camera… please allow camera access.</p>}
            {error && <p className="camera-capture__error" role="alert">{error}</p>}
            <div className="camera-capture__actions">
              <button type="button" className="btn btn--primary" disabled={!ready || loading} onClick={captureFrame}>
                Take Photo
              </button>
              <button type="button" className="btn btn--ghost" disabled={loading} onClick={() => {
                setFacing((current) => current === 'environment' ? 'user' : 'environment')
              }}>
                Switch Camera
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => nativeInputRef.current?.click()}>
                Use Phone Camera
              </button>
            </div>
            <input
              ref={nativeInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              hidden
              aria-label="Take photo with phone camera"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) {
                  onCapture(file)
                  close()
                }
                event.target.value = ''
              }}
            />
            <p className="muted camera-capture__hint">Camera photos follow the same upload and image validation rules as gallery photos.</p>
          </section>
        </div>
      )}
    </>
  )
}
