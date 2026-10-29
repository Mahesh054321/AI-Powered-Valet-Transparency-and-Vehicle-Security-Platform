/**
 * The geofence map with real-time SSE streaming, auto-follow, and distinct phone vs vehicle tracking.
 *
 * Draws the assigned zone's boundary, the GPS tolerance band around it, the
 * vehicle's recorded track and its current position, coloured by the geofence
 * verdict so a breach is visible without reading a number.
 *
 * Distinct markers for:
 *  - Vehicle GPS location (colored by movement verdict)
 *  - Mobile browser GPS location (clearly distinguished as phone location)
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

function phoneIcon({ size = 16 } = {}) {
  return L.divIcon({
    className: 'tracking-marker tracking-marker--phone',
    html: `<span class="tracking-marker__phone-dot" style="width:${size}px;height:${size}px;background:#2563eb;border:2px solid #ffffff;border-radius:50%;display:inline-block;box-shadow:0 0 6px rgba(37,99,235,0.6)"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

/**
 * Controller inside MapContainer to provide auto-follow and programmatic centering.
 */
function MapController({ position, points, zoom, autoFollow, targetCenter }) {
  const map = useMap()

  useEffect(() => {
    const el = map.getContainer()
    map.invalidateSize()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(el)
    return () => observer.disconnect()
  }, [map])

  // Center when targetCenter explicitly commanded
  useEffect(() => {
    if (targetCenter) {
      map.setView(targetCenter, zoom, { animate: true })
    }
  }, [map, targetCenter, zoom])

  // Auto-follow or initial fit
  useEffect(() => {
    if (autoFollow && position) {
      map.setView(position, map.getZoom(), { animate: true })
    } else if (points.length > 1) {
      map.fitBounds(points, { padding: [24, 24], maxZoom: zoom, animate: false })
    } else if (position) {
      map.setView(position, zoom, { animate: false })
    }
  }, [map, position, points, zoom, autoFollow])

  return null
}

/**
 * @param {object} props
 * @param {object|null} props.zone          Zone with `geofence_polygon`, `centre`, `radius_metres`, `buffer_metres`.
 * @param {object|null} props.latestFix     The most recent GPS fix.
 * @param {object|null} [props.phoneFix]    Recent mobile browser phone fix.
 * @param {Array}       props.track         All fixes, oldest first.
 * @param {string}      [props.status]      Latest movement status; drives the boundary colour.
 * @param {string}      [props.freshness]   LIVE, STALE, OFFLINE, or LOCATION_UNCERTAIN.
 * @param {string}      [props.boundaryState] INSIDE, NEAR_BOUNDARY, OUTSIDE, UNCERTAIN.
 * @param {string}      [props.streamStatus] SSE stream status (connected, connecting, etc.).
 * @param {number}      [props.zoom]
 */
export function TrackingMap({
  zone,
  latestFix,
  phoneFix,
  track = [],
  status,
  freshness = 'LIVE',
  boundaryState,
  streamStatus,
  zoom = 17,
}) {
  const boundary = useMemo(() => ringToLatLngs(zone?.geofence_polygon), [zone])
  const latest = useMemo(() => pointToLatLng(latestFix), [latestFix])
  const phoneLatLng = useMemo(() => (phoneFix ? pointToLatLng(phoneFix) : null), [phoneFix])
  const zoneCentre = useMemo(() => pointToLatLng(zone?.centre), [zone])
  const path = useMemo(() => fixesToPath(track), [track])
  const centre = useMemo(
    () => pickCentre({ latest: phoneLatLng ?? latest, zoneCentre, ring: boundary }),
    [phoneLatLng, latest, zoneCentre, boundary],
  )
  const points = useMemo(
    () => [
      ...collectPoints({ ring: boundary, path, latest }),
      ...(phoneLatLng ? [phoneLatLng] : []),
    ],
    [boundary, path, latest, phoneLatLng],
  )

  const [tilesFailed, setTilesFailed] = useState(false)
  const [autoFollow, setAutoFollow] = useState(false)
  const [targetCenter, setTargetCenter] = useState(null)

  const flagged = useMemo(
    () =>
      track.filter(
        (fix) =>
          pointToLatLng(fix) &&
          fix.movement_status &&
          fix.movement_status !== MOVEMENT_STATUS.NORMAL,
      ),
    [track],
  )

  const color = movementStatusColor(status)
  const bufferMetres = Number(zone?.buffer_metres ?? 0)
  const radiusMetres = Number(zone?.radius_metres ?? 0)
  const showToleranceBand = zone?.radius_metres && bufferMetres > 0

  // Show distinct phone marker only when distinct from latestFix
  const showDistinctPhone =
    phoneLatLng &&
    (!latest || Math.abs(phoneLatLng[0] - latest[0]) > 0.00005 || Math.abs(phoneLatLng[1] - latest[1]) > 0.00005)

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
      <div className="tracking-map-wrapper">
        {/* Real-time map control toolbar */}
        <div className="tracking-map__toolbar">
          <div className="tracking-map__indicators">
            {streamStatus === 'connected' ? (
              <span className="stream-badge stream-badge--live">
                <span className="stream-badge__pulse" /> Real-time SSE Live
              </span>
            ) : streamStatus === 'reconnecting' ? (
              <span className="stream-badge stream-badge--reconnecting">
                Reconnecting Stream…
              </span>
            ) : (
              <span className="stream-badge stream-badge--polling">Polled Telemetry</span>
            )}

            {freshness && (
              <span className={`freshness-badge freshness-badge--${freshness.toLowerCase().replace(/\s+/g, '-')}`}>
                {freshness}
              </span>
            )}

            {boundaryState && (
              <span className={`boundary-badge boundary-badge--${boundaryState.toLowerCase().replace(/\s+/g, '-')}`}>
                {boundaryState}
              </span>
            )}
          </div>

          <div className="tracking-map__actions">
            {latest && (
              <button
                type="button"
                className="btn btn--tiny btn--secondary"
                onClick={() => setTargetCenter(latest)}
                title="Center view on latest vehicle location"
              >
                Center Vehicle
              </button>
            )}
            {zoneCentre && (
              <button
                type="button"
                className="btn btn--tiny btn--secondary"
                onClick={() => setTargetCenter(zoneCentre)}
                title="Center view on parking yard"
              >
                Center Safe Zone
              </button>
            )}
            <button
              type="button"
              className={`btn btn--tiny ${autoFollow ? 'btn--primary' : 'btn--secondary'}`}
              onClick={() => setAutoFollow((prev) => !prev)}
              title="Automatically keep map centered on latest fix"
            >
              Auto-Follow: {autoFollow ? 'ON' : 'OFF'}
            </button>
          </div>
        </div>

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
              eventHandlers={{
                tileerror: () => setTilesFailed(true),
                tileload: () => setTilesFailed(false),
              }}
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            <MapController
              position={centre}
              points={points}
              zoom={zoom}
              autoFollow={autoFollow}
              targetCenter={targetCenter}
            />

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

            {showDistinctPhone && (
              <Marker position={phoneLatLng} icon={phoneIcon({ size: 14 })}>
                <Tooltip>
                  Authorized Mobile Phone Location
                  {phoneFix.accuracy_metres != null && ` (±${Math.round(phoneFix.accuracy_metres)} m)`}
                </Tooltip>
              </Marker>
            )}

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
      </div>

      <ul className="tracking-legend" aria-label="Map legend">
        <li className="tracking-legend__item">
          <span className="tracking-legend__swatch tracking-legend__swatch--zone" />
          Permitted zone boundary
        </li>
        <li className="tracking-legend__item">
          <span className="tracking-legend__swatch tracking-legend__swatch--dot" />
          Latest recorded position
        </li>
        <li className="tracking-legend__item">
          <span className="tracking-legend__swatch" />
          Recorded route
        </li>
        {showDistinctPhone && (
          <li className="tracking-legend__item">
            <span className="tracking-legend__swatch tracking-legend__swatch--phone" style={{ background: '#2563eb' }} />
            Mobile phone location
          </li>
        )}
      </ul>
    </>
  )
}

/** Degrees of latitude for a distance in metres, used by callers that need a radius. */
export function metresToLatitudeDegrees(metres) {
  return metres / METRES_PER_DEGREE
}
