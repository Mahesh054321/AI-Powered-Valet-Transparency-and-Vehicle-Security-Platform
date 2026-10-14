/**
 * Pure coordinate helpers for the tracking map.
 *
 * The API stores polygon rings as GeoJSON [longitude, latitude]; Leaflet wants
 * [latitude, longitude]. Values can also arrive as strings or null. All of that
 * is handled here, once, so the map component never draws a bad point.
 */

/** A finite number in a valid lat/lng range, or null. */
export function toCoordinate(value, limit) {
  if (value === null || value === undefined || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n) || Math.abs(n) > limit) return null
  return n
}

/** {latitude, longitude} -> [lat, lng], or null when either part is invalid. */
export function pointToLatLng(point) {
  if (!point) return null
  const lat = toCoordinate(point.latitude, 90)
  const lng = toCoordinate(point.longitude, 180)
  return lat === null || lng === null ? null : [lat, lng]
}

/** GeoJSON ring [[lon, lat], ...] -> Leaflet [[lat, lng], ...]; invalid vertices are dropped. */
export function ringToLatLngs(ring) {
  if (!Array.isArray(ring)) return []
  return ring
    .map((vertex) => {
      if (!Array.isArray(vertex) || vertex.length < 2) return null
      const lng = toCoordinate(vertex[0], 180)
      const lat = toCoordinate(vertex[1], 90)
      return lat === null || lng === null ? null : [lat, lng]
    })
    .filter(Boolean)
}

/** Fixes -> Leaflet path; fixes with invalid coordinates are skipped. */
export function fixesToPath(fixes) {
  if (!Array.isArray(fixes)) return []
  return fixes.map(pointToLatLng).filter(Boolean)
}

/** Every drawable point (zone ring + track + latest fix), for fitting the view. */
export function collectPoints({ ring = [], path = [], latest = null }) {
  return [...ring, ...path, ...(latest ? [latest] : [])]
}

/** Centre to fall back on when there is no latest fix. */
export function pickCentre({ latest, zoneCentre, ring }) {
  if (latest) return latest
  if (zoneCentre) return zoneCentre
  if (ring.length) {
    const lat = ring.reduce((sum, p) => sum + p[0], 0) / ring.length
    const lng = ring.reduce((sum, p) => sum + p[1], 0) / ring.length
    return [lat, lng]
  }
  return null
}