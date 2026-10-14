/**
 * The geofence map.
 *
 * Draws the assigned zone's boundary, the GPS tolerance band around it, the
 * vehicle's recorded track and its current position, coloured by the geofence
 * verdict so a breach is visible without reading a number.
 *
 * Two implementation notes that matter:
 *
 *  - **No default Leaflet marker icons.** The stock icons are PNGs resolved
 *    relative to the stylesheet, which bundlers routinely break. Every marker
 *    here is a `divIcon` built from CSS, so nothing depends on an asset path
 *    and the colour can follow the verdict.
 *
 *  - **The polygon is drawn from the stored ring, not from `radius`.** The
 *    boundary the engine evaluated against is the ring; drawing a circle from
 *    the radius instead would let the map disagree with the verdict, which is
 *    the one thing a security view must never do. The radius-derived circle is
 *    drawn only as the tolerance band, and only when the zone really is a
 *    circle.
 */

import { useEffect, useMemo, useState } from 'react'
import {
  Circle,
  MapContainer,
  Marker,
  Polygon,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

import { MOVEMENT_STATUS, movementStatusColor, movementStatusLabel } from '../../utils/constants'
import {
  collectPoints,
  fixesToPath,
  pickCentre,
  pointToLatLng,
  ringToLatLngs,
} from '../../utils/mapGeometry'

/** WGS-84 metres per degree of latitude, for drawing the tolerance band. */
const METRES_PER_DEGREE = 111_320

/**
 * Move the view when the subject changes.
 *
 * A separate component because `MapContainer` only honours `center` on first
 * mount; without this the map would stay wherever it started even after the
 * user switches vehicle.
 */
function Recenter({ position, points, zoom }) {
  const map = useMap()

  // The map can be mounted while its container is still being laid out (or be
  // resized by the side panel / browser zoom); Leaflet must be told to re-measure.
  useEffect(() => {
    const el = map.getContainer()
    map.invalidateSize()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(el)
    return () => observer.disconnect()
  }, [map])

  useEffect(() => {
    if (points.length > 1) {
      map.fitBounds(points, { padding: [24, 24], maxZoom: zoom, animate: false })
    } else if (position) {
      map.setView(position, zoom, { animate: false })
    }
  }, [map, position, points, zoom])
  return null
}

function statusIcon(status, { size = 16, pulse = false } = {}) {
  const color = movementStatusColor(status)
  return L.divIcon({
    className: 'tracking-marker',
    html: `<span class="tracking-marker__dot${pulse ? ' tracking-marker__dot--pulse' : ''}"
                 style="--marker-color:${color};width:${size}px;height:${size}px"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

/**
 * @param {object} props
 * @param {object|null} props.zone          Zone with `geofence_polygon`, `centre`, `radius_metres`, `buffer_metres`.
 * @param {object|null} props.latestFix     The most recent GPS fix.
 * @param {Array}       props.track         All fixes, oldest first.
 * @param {string}      [props.status]      Latest movement status; drives the boundary colour.
 * @param {number}      [props.zoom]
 */
export function TrackingMap({ zone, latestFix, track = [], status, zoom = 17 }) {
  // Leaflet wants [lat, lng]; the API stores GeoJSON [lon, lat]. The swap and
  // all validation live in utils/mapGeometry.js.
  const boundary = useMemo(() => ringToLatLngs(zone?.geofence_polygon), [zone])
  const latest = useMemo(() => pointToLatLng(latestFix), [latestFix])
  const zoneCentre = useMemo(() => pointToLatLng(zone?.centre), [zone])
  const path = useMemo(() => fixesToPath(track), [track])
  const centre = useMemo(
    () => pickCentre({ latest, zoneCentre, ring: boundary }),
    [latest, zoneCentre, boundary],
  )
  const points = useMemo(
    () => collectPoints({ ring: boundary, path, latest }),
    [boundary, path, latest],
  )
  const [tilesFailed, setTilesFailed] = useState(false)

  const flagged = useMemo(
    () => track.filter((fix) => pointToLatLng(fix) && fix.movement_status && fix.movement_status !== MOVEMENT_STATUS.NORMAL),
    [track],
  )

  const color = movementStatusColor(status)
  const bufferMetres = Number(zone?.buffer_metres ?? 0)
  const radiusMetres = Number(zone?.radius_metres ?? 0)
  // Only a generated circle has a meaningful radius to offset; for an irregular
  // polygon the ring already is the boundary.
  const showToleranceBand = zone?.radius_metres && bufferMetres > 0

  if (!centre) {
    return (
      <div className="tracking-map tracking-map--empty">
        <p className="muted">
          No position has been recorded for this vehicle yet, and no zone with a
          usable boundary is assigned, so there is nothing to draw.
        </p>
      </div>
    )
  }

  return (
    <>
    <div className="tracking-map">
      {tilesFailed && (
        <div className="tracking-map__notice" role="alert">
          Map tiles could not be loaded (check your internet connection or
          whether OpenStreetMap is blocked). The zone boundary and positions
          are still drawn, just without the street background.
        </div>
      )}
      <MapContainer center={centre} zoom={zoom} scrollWheelZoom>
        <TileLayer
          eventHandlers={{ tileerror: () => setTilesFailed(true), tileload: () => setTilesFailed(false) }}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Recenter position={centre} points={points} zoom={zoom} />

        {boundary.length >= 3 && (
          <Polygon
            positions={boundary}
            pathOptions={{ color, weight: 2.5, fillColor: color, fillOpacity: 0.1 }}
          >
            <Tooltip sticky>
              {zone?.name ? `Permitted zone: ${zone.name}` : 'Permitted zone'}
            </Tooltip>
          </Polygon>
        )}

        {showToleranceBand && zoneCentre && (
          <Circle
            center={zoneCentre}
            radius={radiusMetres + bufferMetres}
            pathOptions={{ color, weight: 1, dashArray: '4 6', fill: false }}
          >
            <Tooltip sticky>
              GPS tolerance band: a fix within {bufferMetres} m of the boundary is
              not treated as a breach.
            </Tooltip>
          </Circle>
        )}

        {path.length > 1 && (
          <Polyline positions={path} pathOptions={{ color: '#5b6b80', weight: 2, opacity: 0.75 }} />
        )}

        {flagged.map((fix) => (
          <Marker
            key={fix.id}
            position={pointToLatLng(fix)}
            icon={statusIcon(fix.movement_status, { size: 10 })}
          >
            <Tooltip>
              {movementStatusLabel(fix.movement_status)}
              {fix.distance_from_zone_metres != null && (
                <> · {Math.round(fix.distance_from_zone_metres)} m from the boundary</>
              )}
            </Tooltip>
          </Marker>
        ))}

        {latest && (
          <Marker position={latest} icon={statusIcon(status, { size: 18, pulse: true })}>
            <Tooltip>
              Current position · {movementStatusLabel(status)}
              {latestFix.speed_kmph != null && <> · {Number(latestFix.speed_kmph).toFixed(0)} km/h</>}
            </Tooltip>
          </Marker>
        )}
      </MapContainer>
    </div>
    <ul className="tracking-legend" aria-label="Map legend">
      <li className="tracking-legend__item"><span className="tracking-legend__swatch tracking-legend__swatch--zone" />Permitted zone boundary</li>
      <li className="tracking-legend__item"><span className="tracking-legend__swatch tracking-legend__swatch--dot" />Latest recorded position</li>
      <li className="tracking-legend__item"><span className="tracking-legend__swatch" />Recorded route</li>
    </ul>
    </>
  )
}

/** Degrees of latitude for a distance in metres, used by callers that need a radius. */
export function metresToLatitudeDegrees(metres) {
  return metres / METRES_PER_DEGREE
}