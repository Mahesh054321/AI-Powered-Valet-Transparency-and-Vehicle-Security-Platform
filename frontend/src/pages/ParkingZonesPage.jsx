/**
 * Administrator-only parking zone configuration.
 * The tracking map is intentionally read-only for every role; the server also
 * enforces ADMIN on all geofence create/update/status endpoints.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleMarker, MapContainer, Polygon, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { ErrorNotice, Panel, Spinner } from '../components/ui'
import { normaliseError } from '../services/apiClient'
import {
  activateGeofence,
  createGeofence,
  deactivateGeofence,
  fetchGeofences,
  previewGeofence,
  updateGeofence,
} from '../services/geofenceService'
import { pointToLatLng, ringToLatLngs } from '../utils/mapGeometry'

const EMPTY = {
  code: '', name: '', description: '', capacity: '20',
  latitude: '', longitude: '', zone_type: 'CIRCLE',
  radius: '100', length_metres: '120', width_metres: '60',
  orientation_degrees: '0', buffer_metres: '25',
}

function formFor(zone) {
  if (!zone) return { ...EMPTY }
  return {
    code: zone.code ?? '', name: zone.name ?? '',
    description: zone.description ?? '', capacity: String(zone.capacity ?? 1),
    latitude: String(zone.centre?.latitude ?? ''),
    longitude: String(zone.centre?.longitude ?? ''),
    zone_type: zone.zone_type ?? 'CIRCLE',
    radius: String(zone.radius_metres ?? 100),
    length_metres: String(zone.length_metres ?? 120),
    width_metres: String(zone.width_metres ?? 60),
    orientation_degrees: String(zone.orientation_degrees ?? 0),
    buffer_metres: String(zone.buffer_metres ?? 25),
  }
}

function polygonVertices(zone) {
  const vertices = (zone?.geofence_polygon ?? []).map((p) => [Number(p[0]), Number(p[1])])
  if (vertices.length > 1 && vertices[0][0] === vertices.at(-1)[0] && vertices[0][1] === vertices.at(-1)[1]) {
    return vertices.slice(0, -1)
  }
  return vertices
}

function polygonRing(points) {
  if (points.length < 3) throw new Error('Draw at least three polygon corners on the map.')
  return [...points, [...points[0]]]
}

function buildPayload(form, points, { forPreview = false } = {}) {
  const latitude = Number(form.latitude)
  const longitude = Number(form.longitude)
  if (!form.latitude.trim() || !form.longitude.trim() || !Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    throw new Error('Choose the centre on the map or enter valid latitude and longitude.')
  }
  const buffer = Number(form.buffer_metres)
  if (!Number.isFinite(buffer) || buffer < 0 || buffer > 9999) throw new Error('Enter a valid GPS buffer (0–9999 metres).')
  const body = {
    latitude, longitude, zone_type: form.zone_type,
    buffer_metres: buffer,
  }
  if (form.zone_type === 'CIRCLE') {
    const radius = Number(form.radius)
    if (!Number.isFinite(radius) || radius <= 0 || radius > 5000) throw new Error('Radius must be between 1 and 5000 metres.')
    body.radius = radius
  } else if (form.zone_type === 'RECTANGLE') {
    const length = Number(form.length_metres)
    const width = Number(form.width_metres)
    const orientation = Number(form.orientation_degrees)
    if (!(length > 0 && width > 0 && orientation >= 0 && orientation <= 360)) throw new Error('Enter valid rectangle dimensions and orientation (0–360°).')
    body.length_metres = length
    body.width_metres = width
    body.orientation_degrees = orientation
  } else {
    body.geofence_polygon = polygonRing(points)
  }
  if (!forPreview) {
    const capacity = Number(form.capacity)
    if (!Number.isSafeInteger(capacity) || capacity < 1) throw new Error('Capacity must be a positive whole number.')
    if (!form.code.trim() || !form.name.trim()) throw new Error('Zone code and name are required.')
    body.code = form.code.trim().toUpperCase()
    body.name = form.name.trim()
    body.description = form.description.trim()
    body.capacity = capacity
  }
  return body
}

function MapActions({ editing, zoneType, onPoint }) {
  useMapEvents({
    click(event) {
      if (editing) onPoint(event.latlng.lat, event.latlng.lng, zoneType)
    },
  })
  return null
}

function ZoneMap({ zones, selected, preview, editing, zoneType, onPoint, onSelect, points }) {
  const first = selected || zones[0]
  const defaultCenter = pointToLatLng(first?.centre) ?? [20.5937, 78.9629]
  const draftPolygon = zoneType === 'POLYGON' && editing ? points.map(([lng, lat]) => [lat, lng]) : []
  const draftCenter = preview ? pointToLatLng(preview.centre) : null
  return (
    <div className="zone-map" aria-label="Parking zone map">
      <MapContainer center={defaultCenter} zoom={first ? 16 : 5} scrollWheelZoom className="zone-map__leaflet">
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <MapActions editing={editing} zoneType={zoneType} onPoint={onPoint} />
        <ZoneMapFocus zone={selected} preview={preview} />
        {zones.map((zone) => {
          const ring = ringToLatLngs(zone.geofence_polygon)
          if (ring.length < 3) return null
          const chosen = selected?.id === zone.id
          return (
            <Polygon
              key={zone.id}
              positions={ring}
              pathOptions={{ color: chosen ? '#2563eb' : '#64748b', weight: chosen ? 4 : 2, fillOpacity: chosen ? 0.22 : 0.10, dashArray: zone.status === 'ACTIVE' ? undefined : '7 5' }}
              eventHandlers={{ click: () => { if (!editing) onSelect(zone.id) } }}
            >
              <Tooltip>{zone.code} — {zone.name} ({zone.status})</Tooltip>
            </Polygon>
          )
        })}
        {preview && ringToLatLngs(preview.geofence_polygon).length >= 3 && (
          <Polygon
            positions={ringToLatLngs(preview.geofence_polygon)}
            pathOptions={{ color: '#16a34a', weight: 4, fillOpacity: 0.2, dashArray: '8 6' }}
          >
            <Tooltip>Unsaved boundary preview</Tooltip>
          </Polygon>
        )}
        {draftPolygon.length > 0 && !preview && draftPolygon.map((p, i) => (
          <CircleMarker key={i} center={p} radius={5} pathOptions={{ color: '#16a34a' }} />
        ))}
        {draftPolygon.length >= 3 && !preview && (
          <Polygon positions={draftPolygon} pathOptions={{ color: '#16a34a', fillOpacity: 0.12, dashArray: '8 6' }} />
        )}
        {draftCenter && <CircleMarker center={draftCenter} radius={6} pathOptions={{ color: '#16a34a' }} />}
      </MapContainer>
    </div>
  )
}

// Focus selected zone or a freshly generated preview; selecting a different
// zone updates the viewport while leaving ordinary manual map panning alone.
function ZoneMapFocus({ zone, preview }) {
  const map = useMap()
  useEffect(() => {
    const ring = ringToLatLngs(preview?.geofence_polygon ?? zone?.geofence_polygon)
    const center = pointToLatLng(preview?.centre ?? zone?.centre)
    if (ring.length >= 3) map.fitBounds(ring, { padding: [30, 30], maxZoom: 17 })
    else if (center) map.setView(center, 16)
  }, [map, zone, preview]) // Avoid recentering while the admin clicks to draw a polygon.
  return null
}

export function ParkingZonesPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState(null)
  const [editor, setEditor] = useState(null) // null | { id?: string }
  const [form, setForm] = useState(formFor(null))
  const [points, setPoints] = useState([])
  const [preview, setPreview] = useState(null)
  const [feedback, setFeedback] = useState('')
  const [validation, setValidation] = useState('')
  const [search, setSearch] = useState('')

  const directory = useQuery({ queryKey: ['geofences'], queryFn: () => fetchGeofences() })
  const zones = directory.data?.zones ?? []
  const selected = zones.find((zone) => zone.id === selectedId) ?? zones[0] ?? null
  const shown = useMemo(() => zones.filter((zone) =>
    `${zone.code} ${zone.name} ${zone.status}`.toLowerCase().includes(search.toLowerCase()),
  ), [zones, search])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['geofences'] })
  const action = useMutation({
    mutationFn: async ({ type, id, payload }) => {
      if (type === 'create') return createGeofence(payload)
      if (type === 'update') return updateGeofence(id, payload)
      if (type === 'activate') return activateGeofence(id)
      return deactivateGeofence(id)
    },
    onSuccess(data, variables) {
      refresh()
      setPreview(null)
      setEditor(null)
      setValidation('')
      setFeedback(variables.type === 'create' ? 'Parking zone created.' : variables.type === 'update' ? 'Parking zone updated.' : 'Zone status changed.')
      if (data?.id) setSelectedId(data.id)
    },
  })
  const previewAction = useMutation({
    mutationFn: previewGeofence,
    onSuccess: (data) => { setPreview(data); setValidation('') },
  })

  function change(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setPreview(null)
    setValidation('')
  }
  function editZone(zone) {
    action.reset()
    previewAction.reset()
    setForm(formFor(zone))
    setPoints(zone.zone_type === 'POLYGON' ? polygonVertices(zone) : [])
    setPreview(null)
    setFeedback('')
    setValidation('')
    setSelectedId(zone.id)
    setEditor({ id: zone.id })
  }
  function createZone() {
    action.reset()
    previewAction.reset()
    const center = selected?.centre
    setForm({ ...EMPTY, latitude: center ? String(center.latitude) : '', longitude: center ? String(center.longitude) : '' })
    setPoints([])
    setPreview(null)
    setFeedback('')
    setValidation('')
    setEditor({})
  }
  function onMapPoint(lat, lng, type) {
    if (type === 'POLYGON') {
      setPoints((old) => [...old, [Number(lng.toFixed(6)), Number(lat.toFixed(6))]])
      if (points.length === 0) setForm((old) => ({ ...old, latitude: lat.toFixed(6), longitude: lng.toFixed(6) }))
    } else {
      setForm((old) => ({ ...old, latitude: lat.toFixed(6), longitude: lng.toFixed(6) }))
    }
    setPreview(null)
    setValidation('')
  }
  async function onPreview() {
    try {
      setValidation('')
      await previewAction.mutateAsync(buildPayload(form, points, { forPreview: true }))
    } catch (error) {
      setValidation(normaliseError(error).message)
    }
  }
  async function onSubmit(event) {
    event.preventDefault()
    try {
      setValidation('')
      await action.mutateAsync({ type: editor?.id ? 'update' : 'create', id: editor?.id, payload: buildPayload(form, points) })
    } catch (error) {
      setValidation(normaliseError(error).message)
    }
  }
  function onStatus(zone) {
    const actionLabel = zone.status === 'ACTIVE' ? 'deactivate' : 'activate'
    if (actionLabel === 'deactivate' && !window.confirm(`Deactivate ${zone.code}? Vehicles cannot be assigned to an inactive zone.`)) return
    setValidation('')
    action.mutate({ type: actionLabel, id: zone.id })
  }

  return (
    <div className="page parking-zones-page">
      <header className="page__header">
        <div>
          <h1>Manage Parking Zones</h1>
          <p className="muted">Administrator only · Create, view and edit parking boundaries. Other roles can view assigned zones on their tracking map.</p>
        </div>
        <button type="button" className="btn btn--primary" onClick={createZone}>+ Create Zone</button>
      </header>
      {feedback && <div className="notice notice--success" role="status">{feedback}</div>}
      <ErrorNotice error={directory.error ? normaliseError(directory.error) : null} onRetry={directory.refetch} />
      <ErrorNotice error={action.error ? normaliseError(action.error) : null} />
      {directory.isPending && <Spinner label="Loading parking zones…" />}
      <div className="parking-zones-layout">
        <div className="parking-zones-list">
          <Panel title={`Parking Zones (${zones.length})`} subtitle="Select a zone to see its boundary on the map.">
            <label className="field">
              <span className="field__label">Search zones</span>
              <input aria-label="Search zones" value={search} placeholder="Code, name or status" onChange={(e) => setSearch(e.target.value)} />
            </label>
            <div className="parking-zones-items">
              {shown.length === 0 && <p className="muted">No matching zones. Select Create Zone to make one.</p>}
              {shown.map((zone) => (
                <div className={`parking-zone-item ${selected?.id === zone.id ? 'is-selected' : ''}`} key={zone.id}>
                  <button type="button" className="parking-zone-item__select" onClick={() => { setSelectedId(zone.id); setPreview(null); setEditor(null); setValidation('') }}>
                    <strong>{zone.code} — {zone.name}</strong>
                    <span className="muted">{zone.status} · {zone.current_occupancy}/{zone.capacity} occupied · {zone.zone_type}</span>
                  </button>
                  <div className="parking-zone-item__actions">
                    <button type="button" className="btn btn--small btn--secondary" onClick={() => editZone(zone)}>Edit</button>
                    <button type="button" className="btn btn--small btn--ghost" disabled={action.isPending} onClick={() => onStatus(zone)}>
                      {zone.status === 'ACTIVE' ? 'Deactivate' : 'Activate'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
        <div className="parking-zones-main">
          <Panel
            title="Parking Zone Map"
            subtitle={editor ? (form.zone_type === 'POLYGON' ? 'Click the map to add polygon corners. Preview before saving.' : 'Click the map to set the zone centre, then preview.') : 'View saved parking boundaries. Click a zone in the list to select it.'}
          >
            <ZoneMap
              zones={zones} selected={selected} preview={preview} editing={Boolean(editor)}
              zoneType={form.zone_type} points={points} onPoint={onMapPoint} onSelect={setSelectedId}
            />
            {selected && !editor && (
              <p className="muted parking-zones-map-note">{selected.name} · {selected.status} · Capacity {selected.capacity}, occupied {selected.current_occupancy}. Only Admin may edit this boundary.</p>
            )}
            {preview && <p className="muted parking-zones-map-note">Preview: approximately {Math.round(preview.estimated_area_sq_metres ?? 0).toLocaleString()} m² · not saved yet.</p>}
          </Panel>

          {editor && (
            <Panel
              title={editor.id ? 'Edit Parking Zone' : 'Create Parking Zone'}
              subtitle="All changes are audited. Existing GPS verdicts are not rewritten when a zone moves."
              actions={<button type="button" className="btn btn--ghost btn--small" onClick={() => { setEditor(null); setPreview(null); setValidation('') }}>Cancel</button>}
            >
              <form className="parking-zones-form" onSubmit={onSubmit}>
                <div className="form-row">
                  <label className="field"><span className="field__label">Zone code</span><input required maxLength={32} value={form.code} onChange={(e) => change('code', e.target.value)} placeholder="LOT-A" /></label>
                  <label className="field"><span className="field__label">Zone name</span><input required maxLength={80} value={form.name} onChange={(e) => change('name', e.target.value)} placeholder="Main Parking Yard" /></label>
                  <label className="field"><span className="field__label">Capacity (vehicles)</span><input required type="number" min={1} step={1} value={form.capacity} onChange={(e) => change('capacity', e.target.value)} /></label>
                </div>
                <label className="field"><span className="field__label">Description (optional)</span><input maxLength={300} value={form.description} onChange={(e) => change('description', e.target.value)} /></label>
                <div className="form-row">
                  <label className="field"><span className="field__label">Boundary shape</span>
                    <select value={form.zone_type} onChange={(e) => { change('zone_type', e.target.value); if (e.target.value !== 'POLYGON') setPoints([]) }}>
                      <option value="CIRCLE">Circle</option>
                      <option value="RECTANGLE">Rectangle</option>
                      <option value="POLYGON">Draw polygon on map</option>
                    </select>
                  </label>
                  <label className="field"><span className="field__label">GPS buffer (m)</span><input type="number" min={0} max={9999} step="0.1" value={form.buffer_metres} onChange={(e) => change('buffer_metres', e.target.value)} /></label>
                </div>
                <div className="form-row">
                  <label className="field"><span className="field__label">Centre latitude</span><input type="number" step="any" required value={form.latitude} onChange={(e) => change('latitude', e.target.value)} /></label>
                  <label className="field"><span className="field__label">Centre longitude</span><input type="number" step="any" required value={form.longitude} onChange={(e) => change('longitude', e.target.value)} /></label>
                </div>
                {form.zone_type === 'CIRCLE' && (
                  <label className="field"><span className="field__label">Radius (metres)</span><input type="number" step="any" min={1} max={5000} value={form.radius} onChange={(e) => change('radius', e.target.value)} /></label>
                )}
                {form.zone_type === 'RECTANGLE' && (
                  <div className="form-row">
                    <label className="field"><span className="field__label">Length (m)</span><input type="number" min={1} step="any" value={form.length_metres} onChange={(e) => change('length_metres', e.target.value)} /></label>
                    <label className="field"><span className="field__label">Width (m)</span><input type="number" min={1} step="any" value={form.width_metres} onChange={(e) => change('width_metres', e.target.value)} /></label>
                    <label className="field"><span className="field__label">Orientation (°)</span><input type="number" min={0} max={360} step="any" value={form.orientation_degrees} onChange={(e) => change('orientation_degrees', e.target.value)} /></label>
                  </div>
                )}
                {form.zone_type === 'POLYGON' && (
                  <div className="notice notice--info">
                    Click the map to place at least 3 corners ({points.length} selected). Each click adds a corner.
                    <div className="parking-zones-form__points">
                      <button type="button" className="btn btn--small btn--secondary" disabled={points.length === 0} onClick={() => { setPoints((old) => old.slice(0, -1)); setPreview(null) }}>Undo last corner</button>
                      <button type="button" className="btn btn--small btn--ghost" disabled={points.length === 0} onClick={() => { setPoints([]); setPreview(null) }}>Clear corners</button>
                    </div>
                  </div>
                )}
                {validation && <p className="notice notice--warning" role="alert">{validation}</p>}
                {previewAction.error && <ErrorNotice error={normaliseError(previewAction.error)} />}
                <div className="parking-zones-form__actions">
                  <button type="button" className="btn btn--secondary" disabled={previewAction.isPending || action.isPending} onClick={onPreview}>
                    {previewAction.isPending ? 'Generating preview…' : 'Preview on Map'}
                  </button>
                  <button type="submit" className="btn btn--primary" disabled={action.isPending}>
                    {action.isPending ? 'Saving…' : editor.id ? 'Save Changes' : 'Create Parking Zone'}
                  </button>
                </div>
              </form>
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}
