/**
 * Interactive Safe Zone Builder.
 *
 * Allows customers and managers to define, preview, and save custom safe boundaries:
 *  - Circles (center + radius)
 *  - Rectangles (center + length + width + orientation angle)
 *  - Polygons (vertex ring)
 *
 * Real-time metric previews (effective radius, estimated area in m², tolerance buffer).
 */

import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useToast } from '../../context/ToastContext'
import { ErrorNotice, Panel, Spinner } from '../ui'
import { normaliseError } from '../../services/apiClient'
import { createGeofence, previewGeofence } from '../../services/geofenceService'

export function SafeZoneBuilder({
  initialCenter,
  vehicleId,
  custodyId,
  onCreated,
  onCancel,
}) {
  const queryClient = useQueryClient()
  const toast = useToast()

  const [zoneType, setZoneType] = useState('CIRCLE') // 'CIRCLE' | 'RECTANGLE' | 'POLYGON'
  const [name, setName] = useState('')
  const [code, setCode] = useState(() => `SZ-${Date.now().toString(36).slice(-4).toUpperCase()}`)
  const [latitude, setLatitude] = useState(initialCenter?.latitude ? String(initialCenter.latitude) : '28.4595')
  const [longitude, setLongitude] = useState(initialCenter?.longitude ? String(initialCenter.longitude) : '77.0266')
  const [radiusMetres, setRadiusMetres] = useState('100')
  const [lengthMetres, setLengthMetres] = useState('120')
  const [widthMetres, setWidthMetres] = useState('60')
  const [orientationDegrees, setOrientationDegrees] = useState('0')
  const [bufferMetres, setBufferMetres] = useState('20')
  const [polygonCoords, setPolygonCoords] = useState('')

  const [previewMetrics, setPreviewMetrics] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(null)

  // Auto-calculate preview on parameter changes
  useEffect(() => {
    let active = true
    const lat = parseFloat(latitude)
    const lon = parseFloat(longitude)
    if (isNaN(lat) || isNaN(lon)) return

    const payload = {
      latitude: lat,
      longitude: lon,
      zone_type: zoneType,
      buffer_metres: parseFloat(bufferMetres) || 20,
    }

    if (zoneType === 'CIRCLE') {
      payload.radius = parseFloat(radiusMetres) || 100
    } else if (zoneType === 'RECTANGLE') {
      payload.length_metres = parseFloat(lengthMetres) || 120
      payload.width_metres = parseFloat(widthMetres) || 60
      payload.orientation_degrees = parseFloat(orientationDegrees) || 0
    } else if (zoneType === 'POLYGON' && polygonCoords.trim()) {
      try {
        payload.geofence_polygon = JSON.parse(polygonCoords)
      } catch {
        return
      }
    }

    setPreviewLoading(true)
    previewGeofence(payload)
      .then((res) => {
        if (active) {
          setPreviewMetrics(res)
          setPreviewError(null)
          setPreviewLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setPreviewError(normaliseError(err).message)
          setPreviewLoading(false)
        }
      })

    return () => {
      active = false
    }
  }, [zoneType, latitude, longitude, radiusMetres, lengthMetres, widthMetres, orientationDegrees, bufferMetres, polygonCoords])

  const saveMutation = useMutation({
    mutationFn: (body) => createGeofence(body),
    onSuccess: (data) => {
      toast.success(
        'Safe zone created',
        `Boundary '${data.name || data.code}' configured with ${Math.round(data.effective_radius_metres || 0)} m effective radius.`,
      )
      queryClient.invalidateQueries({ queryKey: ['geofences'] })
      queryClient.invalidateQueries({ queryKey: ['gps', 'tracking'] })
      onCreated?.(data)
    },
    onError: (err) => {
      toast.error('Could not save safe zone', normaliseError(err).message)
    },
  })

  const handleSubmit = (e) => {
    e.preventDefault()
    const lat = parseFloat(latitude)
    const lon = parseFloat(longitude)
    if (isNaN(lat) || isNaN(lon)) {
      toast.error('Invalid coordinates', 'Latitude and longitude must be valid numbers.')
      return
    }

    const payload = {
      code: code.trim().toUpperCase() || `SZ-${Date.now().toString(36).toUpperCase()}`,
      name: name.trim() || `Safe Zone ${code}`,
      latitude: lat,
      longitude: lon,
      zone_type: zoneType,
      buffer_metres: parseFloat(bufferMetres) || 20,
      vehicle_id: vehicleId || null,
      custody_session_id: custodyId || null,
    }

    if (zoneType === 'CIRCLE') {
      payload.radius = parseFloat(radiusMetres) || 100
    } else if (zoneType === 'RECTANGLE') {
      payload.length_metres = parseFloat(lengthMetres) || 120
      payload.width_metres = parseFloat(widthMetres) || 60
      payload.orientation_degrees = parseFloat(orientationDegrees) || 0
    } else if (zoneType === 'POLYGON' && polygonCoords.trim()) {
      try {
        payload.geofence_polygon = JSON.parse(polygonCoords)
      } catch {
        toast.error('Invalid polygon JSON', 'Coordinates must be valid GeoJSON [[lon, lat], ...].')
        return
      }
    }

    saveMutation.mutate(payload)
  }

  return (
    <Panel
      title="Safe Zone Builder"
      subtitle="Define a secure boundary with meter-based geospatial tolerances."
      actions={
        onCancel && (
          <button type="button" className="btn btn--ghost btn--small" onClick={onCancel}>
            Cancel
          </button>
        )
      }
    >
      <form onSubmit={handleSubmit} className="safe-zone-builder" data-testid="safe-zone-form" noValidate>
        <div className="form-row">
          <label className="field">
            <span className="field__label">Boundary Shape</span>
            <select value={zoneType} onChange={(e) => setZoneType(e.target.value)}>
              <option value="CIRCLE">Circular Geofence (Centre + Radius)</option>
              <option value="RECTANGLE">Rectangular Yard (Length × Width + Angle)</option>
              <option value="POLYGON">Custom Polygon Coordinates</option>
            </select>
          </label>

          <label className="field">
            <span className="field__label">Zone Code</span>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              maxLength={32}
            />
          </label>

          <label className="field">
            <span className="field__label">Zone Label / Name</span>
            <input
              type="text"
              value={name}
              placeholder="e.g. VIP Covered Bay, North Lot"
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
            />
          </label>
        </div>

        <div className="form-row">
          <label className="field">
            <span className="field__label">Centre Latitude</span>
            <input
              type="number"
              step="any"
              value={latitude}
              onChange={(e) => setLatitude(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span className="field__label">Centre Longitude</span>
            <input
              type="number"
              step="any"
              value={longitude}
              onChange={(e) => setLongitude(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span className="field__label">GPS Buffer / Tolerance (metres)</span>
            <input
              type="number"
              min="0"
              max="100"
              value={bufferMetres}
              onChange={(e) => setBufferMetres(e.target.value)}
            />
            <span className="field__hint">Tolerance before confirmed exit alerts trip.</span>
          </label>
        </div>

        {zoneType === 'CIRCLE' && (
          <div className="form-row">
            <label className="field">
              <span className="field__label">Radius (metres): {radiusMetres} m</span>
              <input
                type="range"
                min="20"
                max="1000"
                step="10"
                value={radiusMetres}
                onChange={(e) => setRadiusMetres(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field__label">Direct Radius Input (m)</span>
              <input
                type="number"
                min="10"
                value={radiusMetres}
                onChange={(e) => setRadiusMetres(e.target.value)}
              />
            </label>
          </div>
        )}

        {zoneType === 'RECTANGLE' && (
          <div className="form-row">
            <label className="field">
              <span className="field__label">Length (metres)</span>
              <input
                type="number"
                min="10"
                value={lengthMetres}
                onChange={(e) => setLengthMetres(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span className="field__label">Width (metres)</span>
              <input
                type="number"
                min="10"
                value={widthMetres}
                onChange={(e) => setWidthMetres(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span className="field__label">Orientation Angle (°): {orientationDegrees}°</span>
              <input
                type="range"
                min="0"
                max="360"
                step="5"
                value={orientationDegrees}
                onChange={(e) => setOrientationDegrees(e.target.value)}
              />
            </label>
          </div>
        )}

        {zoneType === 'POLYGON' && (
          <div className="form-row">
            <label className="field field--full">
              <span className="field__label">GeoJSON Polygon Coordinates (JSON array)</span>
              <textarea
                rows={3}
                placeholder="[[lon1, lat1], [lon2, lat2], [lon3, lat3], [lon1, lat1]]"
                value={polygonCoords}
                onChange={(e) => setPolygonCoords(e.target.value)}
              />
            </label>
          </div>
        )}

        {/* Live Calculation Preview Card */}
        <div className="notice notice--info safe-zone-preview-card">
          <strong>Boundary Metrics Preview</strong>
          {previewLoading ? (
            <Spinner label="Calculating geometry…" />
          ) : previewError ? (
            <p className="notice--error">{previewError}</p>
          ) : previewMetrics ? (
            <div className="metrics-summary">
              <span>Effective Radius: <strong>{previewMetrics.effective_radius_metres} m</strong></span> ·{' '}
              <span>Estimated Area: <strong>{previewMetrics.estimated_area_sq_metres?.toLocaleString()} m²</strong></span> ·{' '}
              <span>Perimeter Ring Points: <strong>{previewMetrics.geofence_polygon?.length || 0}</strong></span> ·{' '}
              <span>Tolerance Buffer: <strong>{previewMetrics.buffer_metres} m</strong></span>
            </div>
          ) : null}
        </div>

        <ErrorNotice error={saveMutation.error ? normaliseError(saveMutation.error) : null} />

        <div className="form-actions">
          <button
            type="submit"
            className="btn btn--primary"
            disabled={saveMutation.isPending || previewLoading}
          >
            {saveMutation.isPending ? 'Saving Safe Zone…' : 'Save & Activate Boundary'}
          </button>
          {onCancel && (
            <button type="button" className="btn btn--secondary" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </Panel>
  )
}
