"""Geometry helpers shared by the demo seeders.

Kept out of ``ai/`` on purpose: the ``ai`` package must not know about ORM
models, and this reads attributes off a zone row. It is a pure function with no
database access, so both ``database/seed.py`` and the operational-history
generator can use it without importing each other.
"""

from __future__ import annotations

import math

#: Metres in one degree of latitude. The Earth is not a sphere, but for a
#: car park the error is far below GPS accuracy.
METRES_PER_DEGREE_LATITUDE = 111_320.0

#: Fallback when a zone has neither a radius nor a usable polygon.
DEFAULT_ZONE_RADIUS_METRES = 60.0


def zone_radius_metres(zone) -> float:
    """Approximate radius of a parking zone, in metres.

    The synthetic track generator scales its manoeuvres to a radius, but the
    demo zones are polygon-defined. Deriving the value from the zone's own
    bounding box keeps generated tracks proportional to the geofence they are
    judged against; a hardcoded radius produced tracks that overshot small
    zones and looked implausible on the map.

    The smaller half-extent is used so a generated track stays inside the ring
    rather than clipping a corner. Only the *shape* of a track depends on this
    value - movement verdicts are always computed against the real polygon.
    """
    radius = getattr(zone, "radius_metres", None)
    if radius:
        return float(radius)

    ring = getattr(zone, "geofence_polygon", None) or []
    lons = [float(point[0]) for point in ring]
    lats = [float(point[1]) for point in ring]
    if not lons or not lats:
        return DEFAULT_ZONE_RADIUS_METRES

    centre_lat = float(zone.centre_latitude)
    metres_per_degree_lon = METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(centre_lat))
    half_width = (max(lons) - min(lons)) / 2.0 * metres_per_degree_lon
    half_height = (max(lats) - min(lats)) / 2.0 * METRES_PER_DEGREE_LATITUDE
    return max(20.0, min(half_width, half_height))


__all__ = ["DEFAULT_ZONE_RADIUS_METRES", "METRES_PER_DEGREE_LATITUDE", "zone_radius_metres"]
