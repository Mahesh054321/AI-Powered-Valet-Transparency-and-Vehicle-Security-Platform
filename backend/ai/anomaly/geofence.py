"""Geofence evaluation: the maths behind ``NORMAL | WARNING | SUSPICIOUS | BREACH``.

This module is deliberately pure. It imports neither Flask nor SQLAlchemy, takes
plain numbers in, and returns a verdict with the reasoning attached, so every
rule can be unit-tested without a database and re-derived by hand from the
inputs stored on the GPS row.

**This is a rule engine, not a trained model.** Nothing here has learned
anything about a specific yard. Each verdict is the highest rank among a small
set of stated rules, and the module returns *which* rules fired so the UI can
show them. That is the honest version of "explainable AI" for a problem that
does not need learning: the rules are knowable, so guessing at them would only
make them harder to audit.

Distance maths uses a spherical earth and, for point-to-edge work, a local
equirectangular projection. At the scale of a car park (tens to hundreds of
metres) both are accurate to well under a metre, and both are far simpler to
verify than a projected coordinate system. No PostGIS is required, which keeps
the project installable on stock PostgreSQL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

#: Mean earth radius in metres (IUGG). Used for every great-circle distance.
EARTH_RADIUS_METRES = 6_371_008.8

# -- verdict vocabulary -----------------------------------------------------
# Defined as plain strings here because ``ai.*`` must not import from
# ``models``. ``models.enums.MovementStatus`` mirrors these values, and
# ``tests/test_geofence_engine.py`` asserts the two vocabularies still match --
# the drift guard that keeps the mirror honest.
NORMAL = "NORMAL"
WARNING = "WARNING"
SUSPICIOUS = "SUSPICIOUS"
BREACH = "BREACH"
MOVEMENT_STATUSES = (NORMAL, WARNING, SUSPICIOUS, BREACH)
_STATUS_RANK = {status: index for index, status in enumerate(MOVEMENT_STATUSES)}

# -- alert vocabulary -------------------------------------------------------
# Mirrors ``models.enums.AlertType`` / ``AlertSeverity``. Same drift guard.
ALERT_GEOFENCE_BREACH = "GEOFENCE_BREACH"
ALERT_UNUSUAL_SPEED = "UNUSUAL_SPEED"
ALERT_ROUTE_DEVIATION = "ROUTE_DEVIATION"
ALERT_ABNORMAL_MOVEMENT = "ABNORMAL_MOVEMENT"
ALERT_TELEMETRY_LOSS = "TELEMETRY_LOSS"
ALERT_CONFIRMED_EXIT = "CONFIRMED_EXIT"
ALERT_RETURN_TO_ZONE = "RETURN_TO_ZONE"
ALERT_UNEXPECTED_PARKED_MOVEMENT = "UNEXPECTED_PARKED_MOVEMENT"
ALERT_FREQUENT_CROSSINGS = "FREQUENT_CROSSINGS"
ALERT_IMPLAUSIBLE_MOVEMENT = "IMPLAUSIBLE_MOVEMENT"
ALERT_LOCATION_UNCERTAIN = "LOCATION_UNCERTAIN"
ALERT_DEVICE_TAMPER_ALARM = "DEVICE_TAMPER_ALARM"
ALERT_IGNITION_ALERT = "IGNITION_ALERT"
ALERT_DOOR_OPEN_ALERT = "DOOR_OPEN_ALERT"
ALERT_VIBRATION_SHOCK_ALERT = "VIBRATION_SHOCK_ALERT"
ALERT_POWER_DISCONNECT = "POWER_DISCONNECT"
ALERT_DEVICE_REMOVED = "DEVICE_REMOVED"

# -- boundary state vocabulary ----------------------------------------------
BOUNDARY_INSIDE = "INSIDE"
BOUNDARY_NEAR_BOUNDARY = "NEAR_BOUNDARY"
BOUNDARY_OUTSIDE = "OUTSIDE"
BOUNDARY_UNCERTAIN = "UNCERTAIN"
BOUNDARY_STATES = (BOUNDARY_INSIDE, BOUNDARY_NEAR_BOUNDARY, BOUNDARY_OUTSIDE, BOUNDARY_UNCERTAIN)

# -- telemetry freshness vocabulary -----------------------------------------
FRESHNESS_LIVE = "LIVE"
FRESHNESS_STALE = "STALE"
FRESHNESS_OFFLINE = "OFFLINE"
FRESHNESS_UNCERTAIN = "LOCATION_UNCERTAIN"
FRESHNESS_STATES = (FRESHNESS_LIVE, FRESHNESS_STALE, FRESHNESS_OFFLINE, FRESHNESS_UNCERTAIN)

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

#: How many vertices a generated circular geofence has. 64 is visually
#: indistinguishable from a circle at map scale while staying cheap to store.
CIRCLE_RING_POINTS = 64


def _rank(status: str) -> int:
    return _STATUS_RANK[status]


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def haversine_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Great-circle distance between two WGS-84 points, in metres."""
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    chord = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_METRES * math.asin(min(1.0, math.sqrt(chord)))


def bearing_degrees(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Initial great-circle bearing from A to B, as degrees clockwise of north."""
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lon = math.radians(longitude_b - longitude_a)
    x = math.sin(delta_lon) * math.cos(lat_b)
    y = math.cos(lat_a) * math.sin(lat_b) - math.sin(lat_a) * math.cos(lat_b) * math.cos(
        delta_lon
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_between_degrees(bearing_a: float, bearing_b: float) -> float:
    """Smallest unsigned angle between two bearings, in degrees (0..180)."""
    delta = abs(bearing_a - bearing_b) % 360.0
    return 360.0 - delta if delta > 180.0 else delta


def point_in_polygon(latitude: float, longitude: float, ring: list) -> bool:
    """Ray-casting containment test for a ``[longitude, latitude]`` ring.

    ``ring`` uses GeoJSON ordering because that is what ``L.polygon`` in
    Leaflet expects and what the schema stores. A cast ray points east from the
    test point; an odd number of edge crossings means the point is inside.
    """
    if not ring or len(ring) < 3:
        return False
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        x_i, y_i = float(ring[i][0]), float(ring[i][1])
        x_j, y_j = float(ring[j][0]), float(ring[j][1])
        crosses = (y_i > latitude) != (y_j > latitude)
        if crosses and longitude < (x_j - x_i) * (latitude - y_i) / (y_j - y_i) + x_i:
            inside = not inside
        j = i
    return inside


def destination_point(
    latitude: float, longitude: float, bearing: float, distance_metres: float
) -> tuple[float, float]:
    """The point reached by travelling ``distance_metres`` along ``bearing``."""
    angular = distance_metres / EARTH_RADIUS_METRES
    lat = math.radians(latitude)
    bearing_rad = math.radians(bearing)
    lat_b = math.asin(
        math.sin(lat) * math.cos(angular)
        + math.cos(lat) * math.sin(angular) * math.cos(bearing_rad)
    )
    lon_b = math.radians(longitude) + math.atan2(
        math.sin(bearing_rad) * math.sin(angular) * math.cos(lat),
        math.cos(angular) - math.sin(lat) * math.sin(lat_b),
    )
    return math.degrees(lat_b), (math.degrees(lon_b) + 540.0) % 360.0 - 180.0


def circular_ring(
    latitude: float,
    longitude: float,
    radius_metres: float,
    points: int = CIRCLE_RING_POINTS,
) -> list[list[float]]:
    """Approximate a circular geofence as a ``[longitude, latitude]`` ring.

    Lets the API accept the brief's ``centre + radius`` description while the
    stored shape stays a polygon, so one point-in-polygon test serves both an
    irregular real-world yard and a generated circle.
    """
    if radius_metres <= 0:
        raise ValueError(f"radius_metres must be positive; got {radius_metres}")
    if points < 3:
        raise ValueError(f"points must be at least 3; got {points}")
    step = 360.0 / points
    return [
        [
            round(destination_point(latitude, longitude, step * index, radius_metres)[1], 7),
            round(destination_point(latitude, longitude, step * index, radius_metres)[0], 7),
        ]
        for index in range(points)
    ]


def rectangular_ring(
    latitude: float,
    longitude: float,
    length_metres: float,
    width_metres: float,
    orientation_degrees: float = 0.0,
) -> list[list[float]]:
    """Define a rectangular boundary from centre, length, width, and orientation.

    Uses planar projection in local metres for high precision at yard scale.
    Generates a closed [longitude, latitude] ring (5 points, with the first
    repeated at the end) in GeoJSON order.
    """
    if length_metres <= 0:
        raise ValueError(f"length_metres must be positive; got {length_metres}")
    if width_metres <= 0:
        raise ValueError(f"width_metres must be positive; got {width_metres}")

    theta = math.radians(float(orientation_degrees) % 360.0)
    dx_f, dy_f = math.sin(theta), math.cos(theta)
    dx_r, dy_r = math.cos(theta), -math.sin(theta)
    hl = float(length_metres) / 2.0
    hw = float(width_metres) / 2.0

    corners_m = (
        (+hl * dx_f + hw * dx_r, +hl * dy_f + hw * dy_r),
        (-hl * dx_f + hw * dx_r, -hl * dy_f + hw * dy_r),
        (-hl * dx_f - hw * dx_r, -hl * dy_f - hw * dy_r),
        (+hl * dx_f - hw * dx_r, +hl * dy_f - hw * dy_r),
    )

    lat_scale = 180.0 / (math.pi * EARTH_RADIUS_METRES)
    cos_lat = math.cos(math.radians(latitude))
    lon_scale = 180.0 / (math.pi * EARTH_RADIUS_METRES * (cos_lat if abs(cos_lat) > 1e-6 else 1e-6))

    ring = [
        [
            round(longitude + dx * lon_scale, 7),
            round(latitude + dy * lat_scale, 7),
        ]
        for dx, dy in corners_m
    ]
    ring.append(ring[0])  # Close ring
    return ring


def classify_freshness(
    recorded_at: datetime | None,
    accuracy_metres: float | None = None,
    now: datetime | None = None,
    max_uncertain_accuracy_metres: float = 35.0,
    stale_threshold_seconds: float = 300.0,
    offline_threshold_seconds: float = 900.0,
) -> str:
    """Classify fixes as LIVE (5 min), STALE (5-15 min), OFFLINE, or uncertain."""
    if accuracy_metres is not None and accuracy_metres > max_uncertain_accuracy_metres:
        return FRESHNESS_UNCERTAIN
    if recorded_at is None:
        return FRESHNESS_OFFLINE
    if now is None:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        from datetime import timezone
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    gap = (now - recorded_at).total_seconds()
    if gap <= stale_threshold_seconds:
        return FRESHNESS_LIVE
    if gap <= offline_threshold_seconds:
        return FRESHNESS_STALE
    return FRESHNESS_OFFLINE


def _project(
    latitude: float, longitude: float, origin_latitude: float, origin_longitude: float
) -> tuple[float, float]:
    """Local equirectangular projection to planar metres relative to an origin.

    Valid over the few hundred metres a car park spans; the cosine correction
    keeps east-west distance honest away from the equator.
    """
    scale = math.cos(math.radians(origin_latitude))
    return (
        math.radians(longitude - origin_longitude) * EARTH_RADIUS_METRES * scale,
        math.radians(latitude - origin_latitude) * EARTH_RADIUS_METRES,
    )


def _distance_to_segment_m(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Shortest planar distance from ``point`` to the segment ``start``-``end``."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (start[0] + t * dx), point[1] - (start[1] + t * dy))


def signed_distance_to_boundary_m(latitude: float, longitude: float, ring: list) -> float:
    """Distance to the zone boundary, in metres. Negative means inside.

    The sign is the whole point: a caller can see *how far* outside a fix is,
    which is what lets the buffer absorb GPS jitter instead of pageing a
    manager for a two-metre wobble.
    """
    if not ring or len(ring) < 3:
        raise ValueError("ring must have at least three vertices")
    origin_latitude = latitude
    origin_longitude = longitude
    point = _project(latitude, longitude, origin_latitude, origin_longitude)
    vertices = [
        _project(float(vertex[1]), float(vertex[0]), origin_latitude, origin_longitude)
        for vertex in ring
    ]
    nearest = min(
        _distance_to_segment_m(point, vertices[index], vertices[(index + 1) % len(vertices)])
        for index in range(len(vertices))
    )
    inside = point_in_polygon(latitude, longitude, ring)
    return -nearest if inside else nearest


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GeofenceConfig:
    """Every threshold the rule engine uses, in one auditable place.

    Exposed in the verdict's evidence so a reviewer can see the numbers a
    verdict was reached under rather than having to read the source.
    """

    #: Tolerance beyond the boundary before a fix counts as outside at all.
    buffer_metres: float = 25.0
    #: How far beyond the boundary a fix must be to escalate from SUSPICIOUS to
    #: BREACH. Set above realistic street width so a car parked just outside the
    #: yard is suspicious rather than instantly critical.
    breach_margin_metres: float = 150.0
    #: Fastest a vehicle should move inside a valet yard.
    zone_speed_limit_kmph: float = 20.0
    #: Multiplier at which speed stops being "a bit quick" and becomes a finding.
    speed_warning_multiplier: float = 2.0
    #: Above this implied speed between two fixes the telemetry itself is
    #: suspect - no road vehicle in a yard covers ground this fast.
    plausible_max_speed_kmph: float = 180.0
    #: Consecutive outward-moving fixes before drift is called out.
    drift_fix_count: int = 3
    #: Reported heading may differ from the implied direction of travel by this
    #: much before the fix is called internally inconsistent.
    heading_tolerance_degrees: float = 45.0
    #: Below this a vehicle counts as stationary for pattern purposes.
    stationary_speed_kmph: float = 5.0
    #: Longer than this between fixes and telemetry has been lost.
    telemetry_gap_seconds: float = 300.0
    #: Consecutive readings outside buffer required to confirm exit.
    consecutive_readings_required: int = 2
    #: Threshold above which GPS fix accuracy is considered uncertain.
    max_uncertain_accuracy_metres: float = 35.0
    #: Maximum displacement while parked before alerting.
    parked_movement_distance_metres: float = 15.0
    #: Maximum boundary crossings before frequent crossings alert fires.
    max_crossings_count: int = 3
    #: Window for boundary crossings analysis in minutes.
    crossings_window_minutes: float = 10.0


DEFAULT_CONFIG = GeofenceConfig()


@dataclass(frozen=True)
class PriorFix:
    """A previously recorded fix, used for the movement-pattern rules."""

    latitude: float
    longitude: float
    recorded_at: datetime | None = None
    speed_kmph: float | None = None


@dataclass(frozen=True)
class Finding:
    """One rule that fired, phrased for a human to read."""

    alert_type: str
    severity: str
    title: str
    reason: str
    confidence: float
    status: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1]; got {self.confidence}")


@dataclass(frozen=True)
class MovementVerdict:
    """The verdict for one fix, with everything needed to re-derive it."""

    status: str
    distance_from_boundary_metres: float | None
    is_within_zone: bool
    findings: tuple[Finding, ...] = ()
    evidence: dict = field(default_factory=dict)
    boundary_state: str = BOUNDARY_INSIDE

    @property
    def reasons(self) -> list[str]:
        """Every rule that fired, most severe first."""
        ordered = sorted(self.findings, key=lambda item: _rank(item.status), reverse=True)
        return [item.reason for item in ordered]

    @property
    def summary(self) -> str:
        if not self.findings:
            return "Inside the assigned geofence and moving as expected."
        return " ".join(self.reasons)


def _zone_distance(
    latitude: float,
    longitude: float,
    ring: list | None,
    centre: tuple[float, float] | None,
    radius_metres: float | None,
) -> float | None:
    """Signed distance to the zone boundary, from the polygon or the circle.

    The polygon wins when both are present: it is the shape actually stored and
    drawn, so evaluating against anything else would make the map disagree with
    the verdict.
    """
    if ring and len(ring) >= 3:
        return signed_distance_to_boundary_m(latitude, longitude, ring)
    if centre is not None and radius_metres is not None:
        return haversine_m(latitude, longitude, centre[0], centre[1]) - radius_metres
    return None


def evaluate_fix(
    *,
    latitude: float,
    longitude: float,
    zone_ring: list | None = None,
    zone_centre: tuple[float, float] | None = None,
    zone_radius_metres: float | None = None,
    speed_kmph: float | None = None,
    heading_degrees: float | None = None,
    recorded_at: datetime | None = None,
    accuracy_metres: float | None = None,
    sensor_data: dict | None = None,
    is_parked: bool = False,
    consecutive_outside_count: int = 0,
    prior_was_outside: bool = False,
    prior_fixes: tuple[PriorFix, ...] = (),
    movement_expected: bool = False,
    config: GeofenceConfig = DEFAULT_CONFIG,
) -> MovementVerdict:
    """Classify one GPS fix and explain why.

    ``prior_fixes`` must be ordered oldest first. The verdict is the highest
    rank among the findings, so a fix that is both outside the zone and speeding
    reads as a breach rather than being averaged into something vaguer.
    """
    findings: list[Finding] = []
    distance = _zone_distance(
        latitude, longitude, zone_ring, zone_centre, zone_radius_metres
    )
    has_zone = distance is not None
    inside = bool(has_zone and distance is not None and distance <= 0.0)
    buffer_metres = max(0.0, config.buffer_metres)

    # Determine boundary state
    if not has_zone:
        boundary_state = BOUNDARY_UNCERTAIN
    elif accuracy_metres is not None and accuracy_metres > config.max_uncertain_accuracy_metres:
        boundary_state = BOUNDARY_UNCERTAIN
    elif distance is not None and distance <= 0.0:
        boundary_state = BOUNDARY_INSIDE
    elif distance is not None and distance <= buffer_metres:
        boundary_state = BOUNDARY_NEAR_BOUNDARY
    else:
        boundary_state = BOUNDARY_OUTSIDE

    # -- rule 1: zone membership & boundary containment -------------------
    if not has_zone:
        # No zone to evaluate against is not a benign fix; it means the fix
        # could not be judged. Say so rather than defaulting to NORMAL.
        findings.append(
            Finding(
                alert_type=ALERT_ABNORMAL_MOVEMENT,
                severity=SEVERITY_LOW,
                title="Fix could not be evaluated against a geofence",
                reason="No parking zone geometry was available for this fix.",
                confidence=1.0,
                status=WARNING,
            )
        )
    elif boundary_state == BOUNDARY_UNCERTAIN and distance is not None and distance > 0:
        # A single inaccurate GPS reading must not automatically be treated as theft
        findings.append(
            Finding(
                alert_type=ALERT_LOCATION_UNCERTAIN,
                severity=SEVERITY_LOW,
                title="Location reading uncertain",
                reason=(
                    f"Position is {distance:.0f} m outside the boundary, but accuracy radius "
                    f"(±{accuracy_metres:.0f} m) exceeds tolerance. Location is uncertain."
                ),
                confidence=0.4,
                status=WARNING,
            )
        )
    elif distance is not None and distance > buffer_metres:
        # Outside boundary buffer
        if consecutive_outside_count + 1 >= config.consecutive_readings_required:
            if distance > config.breach_margin_metres:
                status, severity = BREACH, SEVERITY_HIGH
            else:
                status, severity = SUSPICIOUS, SEVERITY_MEDIUM
            findings.append(
                Finding(
                    alert_type=ALERT_GEOFENCE_BREACH,
                    severity=severity,
                    title="Vehicle outside its assigned parking zone",
                    reason=(
                        f"Position is {distance:.0f} m beyond the zone boundary, outside the "
                        f"{buffer_metres:.0f} m GPS tolerance across "
                        f"{consecutive_outside_count + 1} confirmed readings."
                    ),
                    confidence=0.98,
                    status=status,
                )
            )
        else:
            # Single reading outside: potential exit pending confirmation to avoid false alarm
            if distance > config.breach_margin_metres:
                status, severity = BREACH, SEVERITY_HIGH
            else:
                status, severity = SUSPICIOUS, SEVERITY_MEDIUM
            findings.append(
                Finding(
                    alert_type=ALERT_GEOFENCE_BREACH,
                    severity=severity,
                    title="Vehicle outside its assigned parking zone",
                    reason=(
                        f"Position is {distance:.0f} m beyond the zone boundary, outside the "
                        f"{buffer_metres:.0f} m GPS tolerance."
                    ),
                    confidence=0.95,
                    status=status,
                )
            )
    elif distance is not None and distance > 0.0:
        findings.append(
            Finding(
                alert_type=ALERT_GEOFENCE_BREACH,
                severity=SEVERITY_LOW,
                title="Vehicle at the edge of its parking zone",
                reason=(
                    f"Position is {distance:.0f} m outside the boundary but still within the "
                    f"{buffer_metres:.0f} m GPS tolerance."
                ),
                confidence=0.6,
                status=WARNING,
            )
        )
    elif boundary_state == BOUNDARY_INSIDE and prior_was_outside:
        findings.append(
            Finding(
                alert_type=ALERT_RETURN_TO_ZONE,
                severity=SEVERITY_LOW,
                title="Vehicle returned to safe zone",
                reason="Vehicle has returned inside the authorized safe zone boundary.",
                confidence=0.95,
                status=NORMAL,
            )
        )

    # -- rule 2: speed ------------------------------------------------------
    # The zone speed limit governs movement *inside* the yard. It deliberately
    # does not apply once a fix is outside the boundary: a vehicle on a public
    # road is expected to travel at road speed, so raising "unusual speed" for
    # it would be a false positive on every arrival, retrieval and escape
    # alike. Outside, the distance finding is the signal and speed is retained
    # as evidence rather than treated as a finding of its own.
    in_yard = inside or (distance is not None and 0.0 < distance <= buffer_metres)
    if speed_kmph is not None and in_yard:
        limit = config.zone_speed_limit_kmph
        if speed_kmph > limit * config.speed_warning_multiplier:
            findings.append(
                Finding(
                    alert_type=ALERT_UNUSUAL_SPEED,
                    severity=SEVERITY_MEDIUM,
                    title="Speed far above the zone limit",
                    reason=(
                        f"Moving at {speed_kmph:.0f} km/h inside the zone where the "
                        f"limit is {limit:.0f} km/h."
                    ),
                    confidence=0.9,
                    status=SUSPICIOUS,
                )
            )
        elif speed_kmph > limit:
            findings.append(
                Finding(
                    alert_type=ALERT_UNUSUAL_SPEED,
                    severity=SEVERITY_LOW,
                    title="Speed above the zone limit",
                    reason=(
                        f"Moving at {speed_kmph:.0f} km/h inside the zone where the "
                        f"limit is {limit:.0f} km/h."
                    ),
                    confidence=0.75,
                    status=WARNING,
                )
            )

    # -- rule 3: movement pattern ------------------------------------------
    latest = prior_fixes[-1] if prior_fixes else None

    if latest is not None:
        step_m = haversine_m(latest.latitude, latest.longitude, latitude, longitude)
        implied_kmph: float | None = None
        gap_seconds: float | None = None
        if latest.recorded_at and recorded_at:
            gap_seconds = (recorded_at - latest.recorded_at).total_seconds()
            if gap_seconds and gap_seconds > 0:
                implied_kmph = (step_m / gap_seconds) * 3600.0 / 1000.0

        # 3a: telemetry gap.
        if gap_seconds is not None and gap_seconds > config.telemetry_gap_seconds:
            findings.append(
                Finding(
                    alert_type=ALERT_TELEMETRY_LOSS,
                    severity=SEVERITY_MEDIUM,
                    title="Telemetry gap detected",
                    reason=(
                        f"No position reported for {gap_seconds / 60.0:.0f} minutes; the "
                        f"threshold is {config.telemetry_gap_seconds / 60.0:.0f} minutes."
                    ),
                    confidence=1.0,
                    status=WARNING,
                )
            )

        # 3b: an impossible jump means the fix or the clock cannot be trusted.
        if implied_kmph is not None and implied_kmph > config.plausible_max_speed_kmph:
            findings.append(
                Finding(
                    alert_type=ALERT_ABNORMAL_MOVEMENT,
                    severity=SEVERITY_HIGH,
                    title="Implied speed between fixes is physically impossible",
                    reason=(
                        f"The position moved {step_m:.0f} m in {gap_seconds:.0f} s, implying "
                        f"{implied_kmph:.0f} km/h. The telemetry is either faulty or "
                        f"fabricated."
                    ),
                    confidence=0.9,
                    status=SUSPICIOUS,
                )
            )

        # 3c: reported heading contradicts the actual direction of travel.
        if (
            heading_degrees is not None
            and implied_kmph is not None
            and implied_kmph > config.stationary_speed_kmph
            and step_m > 1.0
        ):
            implied_bearing = bearing_degrees(
                latest.latitude, latest.longitude, latitude, longitude
            )
            mismatch = angle_between_degrees(heading_degrees, implied_bearing)
            if mismatch > config.heading_tolerance_degrees:
                findings.append(
                    Finding(
                        alert_type=ALERT_ABNORMAL_MOVEMENT,
                        severity=SEVERITY_MEDIUM,
                        title="Reported heading contradicts direction of travel",
                        reason=(
                            f"Device reports {heading_degrees:.0f} degrees but the position "
                            f"moved on a bearing of {implied_bearing:.0f} degrees, a "
                            f"{mismatch:.0f} degree mismatch."
                        ),
                        confidence=0.7,
                        status=SUSPICIOUS,
                    )
                )

        # 3d: sustained outward drift.
        #
        # Two guards keep this rule from firing on GPS noise, which it
        # otherwise does constantly: a genuinely parked vehicle wanders a few
        # metres and three consecutive outward wobbles happen by chance alone.
        # So drift is only claimed when the vehicle is actually moving, and
        # only when the total outward movement exceeds the tolerance band -
        # noise that averages out over the window cannot clear that bar.
        moving_now = speed_kmph is not None and speed_kmph > config.stationary_speed_kmph
        if has_zone and moving_now and len(prior_fixes) >= config.drift_fix_count:
            window = [*prior_fixes[-(config.drift_fix_count - 1) :], PriorFix(latitude, longitude)]
            distances: list[float] = []
            for fix in window:
                step_distance = _zone_distance(
                    fix.latitude,
                    fix.longitude,
                    zone_ring,
                    zone_centre,
                    zone_radius_metres,
                )
                if step_distance is not None:
                    distances.append(step_distance)
            monotonic = len(distances) == config.drift_fix_count and all(
                distances[index] < distances[index + 1] for index in range(len(distances) - 1)
            )
            material = (
                len(distances) == config.drift_fix_count
                and (distances[-1] - distances[0]) > buffer_metres
            )
            if monotonic and material:
                findings.append(
                    Finding(
                        alert_type=ALERT_ROUTE_DEVIATION,
                        severity=SEVERITY_MEDIUM if inside else SEVERITY_HIGH,
                        title="Vehicle is steadily moving away from its zone",
                        reason=(
                            f"Distance from the zone boundary increased across "
                            f"{config.drift_fix_count} consecutive fixes, from "
                            f"{distances[0]:.0f} m to {distances[-1]:.0f} m."
                        ),
                        confidence=0.8,
                        status=SUSPICIOUS if not inside else WARNING,
                    )
                )

        # 3e: moving while no task authorises the movement.
        if moving_now and not movement_expected and inside:
            findings.append(
                Finding(
                    alert_type=ALERT_ABNORMAL_MOVEMENT,
                    severity=SEVERITY_MEDIUM,
                    title="Vehicle moving with no active task",
                    reason=(
                        f"Moving at {speed_kmph:.0f} km/h inside the zone while the custody "
                        f"session has no task in progress."
                    ),
                    confidence=0.7,
                    status=SUSPICIOUS,
                )
            )

        # 3f: outside and heading further away rather than back.
        if (
            has_zone
            and distance is not None
            and distance > buffer_metres
            and zone_centre is not None
            and step_m > 1.0
        ):
            travel = bearing_degrees(latest.latitude, latest.longitude, latitude, longitude)
            toward_zone = bearing_degrees(latitude, longitude, zone_centre[0], zone_centre[1])
            off_course = angle_between_degrees(travel, toward_zone)
            if off_course > 90.0:
                findings.append(
                    Finding(
                        alert_type=ALERT_ROUTE_DEVIATION,
                        severity=SEVERITY_HIGH,
                        title="Vehicle is moving away from the yard",
                        reason=(
                            f"Outside the geofence and travelling {off_course:.0f} degrees off "
                            f"the bearing back to the zone."
                        ),
                        confidence=0.85,
                        status=BREACH,
                    )
                )

        # 3g: unexpected movement while the vehicle is supposed to be parked
        if is_parked and not movement_expected:
            if moving_now:
                findings.append(
                    Finding(
                        alert_type=ALERT_UNEXPECTED_PARKED_MOVEMENT,
                        severity=SEVERITY_HIGH,
                        title="Unexpected movement while vehicle is parked",
                        reason=(
                            f"Vehicle is moving at {speed_kmph:.0f} km/h while custody status is PARKED."
                        ),
                        confidence=0.95,
                        status=SUSPICIOUS,
                    )
                )
            elif step_m > config.parked_movement_distance_metres:
                findings.append(
                    Finding(
                        alert_type=ALERT_UNEXPECTED_PARKED_MOVEMENT,
                        severity=SEVERITY_HIGH,
                        title="Unexpected parked-vehicle displacement",
                        reason=(
                            f"Vehicle shifted position by {step_m:.0f} m while custody status is PARKED."
                        ),
                        confidence=0.9,
                        status=SUSPICIOUS,
                    )
                )

        # 3h: repeated boundary crossings
        if has_zone and len(prior_fixes) >= 3:
            pts = list(prior_fixes) + [PriorFix(latitude, longitude, recorded_at, speed_kmph)]
            crossings = 0
            prev_outside = None
            for fix_item in pts:
                d = _zone_distance(fix_item.latitude, fix_item.longitude, zone_ring, zone_centre, zone_radius_metres)
                if d is not None:
                    out = d > buffer_metres
                    if prev_outside is not None and out != prev_outside:
                        crossings += 1
                    prev_outside = out
            if crossings >= config.max_crossings_count:
                findings.append(
                    Finding(
                        alert_type=ALERT_FREQUENT_CROSSINGS,
                        severity=SEVERITY_MEDIUM,
                        title="Repeated boundary crossings detected",
                        reason=(
                            f"Vehicle crossed the zone boundary {crossings} times across recent fixes, "
                            f"indicating erratic boundary proximity or hovering."
                        ),
                        confidence=0.85,
                        status=SUSPICIOUS,
                    )
                )

    # -- rule 4: hardware tamper & sensor signals ---------------------------
    if sensor_data:
        if sensor_data.get("tamper_triggered"):
            findings.append(
                Finding(
                    alert_type=ALERT_DEVICE_TAMPER_ALARM,
                    severity=SEVERITY_CRITICAL,
                    title="GPS tracker tamper alarm triggered",
                    reason="Physical casing tamper switch or optical sensor triggered on tracking device.",
                    confidence=1.0,
                    status=BREACH,
                )
            )
        if sensor_data.get("ignition") and is_parked and not movement_expected:
            findings.append(
                Finding(
                    alert_type=ALERT_IGNITION_ALERT,
                    severity=SEVERITY_HIGH,
                    title="Unexpected ignition status change",
                    reason="Engine ignition switched ON while vehicle is designated as PARKED.",
                    confidence=0.95,
                    status=SUSPICIOUS,
                )
            )
        if sensor_data.get("shock_detected"):
            findings.append(
                Finding(
                    alert_type=ALERT_VIBRATION_SHOCK_ALERT,
                    severity=SEVERITY_HIGH,
                    title="Vibration or shock alert",
                    reason="High-g impact or disturbance detected by accelerometer (possible towing, jacking or collision).",
                    confidence=0.9,
                    status=SUSPICIOUS,
                )
            )
        if sensor_data.get("power_connected") is False:
            findings.append(
                Finding(
                    alert_type=ALERT_POWER_DISCONNECT,
                    severity=SEVERITY_HIGH,
                    title="Tracker power-loss event",
                    reason="External power disconnected from vehicle tracking unit; running on backup battery.",
                    confidence=0.95,
                    status=SUSPICIOUS,
                )
            )
        if sensor_data.get("device_attached") is False:
            findings.append(
                Finding(
                    alert_type=ALERT_DEVICE_REMOVED,
                    severity=SEVERITY_CRITICAL,
                    title="Vehicle tracking device removed",
                    reason="Tracker unplugged from diagnostic port or dismounted from vehicle.",
                    confidence=1.0,
                    status=BREACH,
                )
            )
        if sensor_data.get("door_open") and is_parked and not movement_expected:
            findings.append(
                Finding(
                    alert_type=ALERT_DOOR_OPEN_ALERT,
                    severity=SEVERITY_MEDIUM,
                    title="Vehicle door open event",
                    reason="Vehicle door sensor opened unexpectedly while parked in custody.",
                    confidence=0.9,
                    status=WARNING,
                )
            )

    status = (
        NORMAL
        if not findings
        else max((finding.status for finding in findings), key=_rank)
    )

    evidence: dict = {
        "boundary_state": boundary_state,
        "distance_from_boundary_metres": (
            round(distance, 2) if distance is not None else None
        ),
        "buffer_metres": buffer_metres,
        "breach_margin_metres": config.breach_margin_metres,
        "speed_kmph": speed_kmph,
        "accuracy_metres": accuracy_metres,
        "sensor_data": sensor_data,
        "consecutive_outside_count": consecutive_outside_count,
        "zone_speed_limit_kmph": config.zone_speed_limit_kmph,
        "speed_limit_applies": in_yard,
        "movement_expected": movement_expected,
        "prior_fix_count": len(prior_fixes),
        "rules_fired": [
            {
                "alert_type": finding.alert_type,
                "status": finding.status,
                "severity": finding.severity,
                "reason": finding.reason,
            }
            for finding in findings
        ],
    }

    return MovementVerdict(
        status=status,
        distance_from_boundary_metres=(round(distance, 2) if distance is not None else None),
        is_within_zone=inside,
        findings=tuple(findings),
        evidence=evidence,
        boundary_state=boundary_state,
    )


__all__ = [
    "ALERT_ABNORMAL_MOVEMENT",
    "ALERT_CONFIRMED_EXIT",
    "ALERT_DEVICE_REMOVED",
    "ALERT_DEVICE_TAMPER_ALARM",
    "ALERT_DOOR_OPEN_ALERT",
    "ALERT_FREQUENT_CROSSINGS",
    "ALERT_GEOFENCE_BREACH",
    "ALERT_IGNITION_ALERT",
    "ALERT_IMPLAUSIBLE_MOVEMENT",
    "ALERT_LOCATION_UNCERTAIN",
    "ALERT_POWER_DISCONNECT",
    "ALERT_RETURN_TO_ZONE",
    "ALERT_ROUTE_DEVIATION",
    "ALERT_TELEMETRY_LOSS",
    "ALERT_UNEXPECTED_PARKED_MOVEMENT",
    "ALERT_UNUSUAL_SPEED",
    "ALERT_VIBRATION_SHOCK_ALERT",
    "BOUNDARY_INSIDE",
    "BOUNDARY_NEAR_BOUNDARY",
    "BOUNDARY_OUTSIDE",
    "BOUNDARY_STATES",
    "BOUNDARY_UNCERTAIN",
    "BREACH",
    "CIRCLE_RING_POINTS",
    "DEFAULT_CONFIG",
    "EARTH_RADIUS_METRES",
    "FRESHNESS_LIVE",
    "FRESHNESS_OFFLINE",
    "FRESHNESS_STALE",
    "FRESHNESS_STATES",
    "FRESHNESS_UNCERTAIN",
    "Finding",
    "GeofenceConfig",
    "MOVEMENT_STATUSES",
    "MovementVerdict",
    "NORMAL",
    "PriorFix",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SUSPICIOUS",
    "WARNING",
    "angle_between_degrees",
    "bearing_degrees",
    "circular_ring",
    "classify_freshness",
    "destination_point",
    "evaluate_fix",
    "haversine_m",
    "point_in_polygon",
    "rectangular_ring",
    "signed_distance_to_boundary_m",
]
