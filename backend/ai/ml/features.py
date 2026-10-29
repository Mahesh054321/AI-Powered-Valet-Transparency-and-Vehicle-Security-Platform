"""Feature extraction for the security classifier.

Pure functions over plain numbers: no Flask, no SQLAlchemy, and no scikit-learn.
That keeps the feature definitions testable in isolation and, more importantly,
guarantees that the vector built at *training* time and the vector built at
*prediction* time come from the same code. Feature-skew between those two paths
is the classic silent failure of an ML service, and the only reliable defence is
to have exactly one implementation.

**Time of day is encoded as a sine/cosine pair, not as an hour.** A raw hour
makes 23:00 and 01:00 look 22 hours apart when they are 2 hours apart, and makes
00:00 and 23:00 maximally distant when they are adjacent. On a circle the only
faithful encoding is a point on a circle. The brief lists `time_of_day` as one
feature; it is that feature, represented correctly, and the two columns are
named `time_sin`/`time_cos` so nobody mistakes them for an hour.

Missing values are not imputed with a mean or a magic number. When a fix has no
predecessor there is genuinely no evidence of movement, so the movement features
are zero - which is what "no observed movement" means, not a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ai.anomaly.geofence import angle_between_degrees, bearing_degrees, haversine_m

#: The feature vector, in a fixed order. Order matters: it is the contract
#: between the trained artifact and the predictor, and it is stored in the
#: artifact so a mismatch is detected rather than silently misread.
FEATURE_COLUMNS: tuple[str, ...] = (
    "speed_kmph",
    "distance_from_zone_metres",
    "movement_duration_seconds",
    "direction_change_degrees",
    "distance_travelled_metres",
    "zone_breach",
    "time_sin",
    "time_cos",
)

#: Below this speed the vehicle counts as stationary for the purposes of
#: measuring how long it has been moving.
STATIONARY_SPEED_KMPH = 5.0

#: Displacement below this is treated as receiver noise rather than movement.
#: Consumer GPS commonly reports 5-10 m of accuracy, and the simulator's own
#: fixes carry ``accuracy_metres`` around 6. Deriving a bearing from a 1 m shift
#: on such a fix produces a direction that is entirely an artefact of the
#: receiver, so no bearing is inferred beneath this floor.
GPS_NOISE_METRES = 5.0


@dataclass(frozen=True)
class PriorObservation:
    """One earlier fix, used for the movement-derived features."""

    latitude: float
    longitude: float
    recorded_at: datetime | None = None
    speed_kmph: float | None = None
    heading_degrees: float | None = None


@dataclass(frozen=True)
class FeatureVector:
    """The extracted features plus the human-readable values behind them."""

    values: dict[str, float]

    def as_list(self) -> list[float]:
        """The vector in ``FEATURE_COLUMNS`` order."""
        return [self.values[column] for column in FEATURE_COLUMNS]

    def describe(self) -> dict[str, float]:
        return dict(self.values)


def time_of_day_features(recorded_at: datetime | None) -> tuple[float, float]:
    """Encode a timestamp as a point on the 24-hour circle.

    Returns ``(sin, cos)``. Midnight and noon are antipodal; 23:00 and 01:00
    are close, as they should be.
    """
    if recorded_at is None:
        return 0.0, 0.0
    seconds = (
        recorded_at.hour * 3600 + recorded_at.minute * 60 + recorded_at.second
    )
    angle = 2.0 * math.pi * (seconds / 86_400.0)
    return math.sin(angle), math.cos(angle)


def movement_duration_seconds(priors: tuple[PriorObservation, ...]) -> float:
    """How long the vehicle has been moving continuously, oldest to newest.

    Walks backwards from the most recent prior fix for as long as the speed
    stayed above the stationary threshold, summing the gaps. A vehicle that has
    been creeping around the yard for twenty minutes is a different proposition
    from one that just started moving, and this is the feature that says so.
    """
    if len(priors) < 2:
        return 0.0
    total = 0.0
    for index in range(len(priors) - 1, 0, -1):
        current = priors[index]
        previous = priors[index - 1]
        speed = current.speed_kmph
        if speed is None or speed < STATIONARY_SPEED_KMPH:
            break
        if current.recorded_at is None or previous.recorded_at is None:
            break
        gap = (current.recorded_at - previous.recorded_at).total_seconds()
        if gap <= 0:
            break
        total += gap
    return total


def extract_features(
    *,
    latitude: float,
    longitude: float,
    recorded_at: datetime | None = None,
    speed_kmph: float | None = None,
    heading_degrees: float | None = None,
    distance_from_zone_metres: float | None = None,
    is_within_zone: bool = True,
    buffer_metres: float = 0.0,
    priors: tuple[PriorObservation, ...] = (),
) -> FeatureVector:
    """Build the feature vector for one GPS fix.

    ``distance_from_zone_metres`` is signed: negative inside the boundary,
    positive outside. Keeping the sign means the classifier can distinguish
    "deep inside" from "just outside" on a single axis, which a boolean cannot.
    ``zone_breach`` is derived from the same number against the tolerance band,
    and is included because the brief specifies it and because a tree model can
    split on it directly.
    """
    latest = priors[-1] if priors else None

    if latest is not None:
        distance_travelled = haversine_m(
            latest.latitude, latest.longitude, latitude, longitude
        )
    else:
        distance_travelled = 0.0

    # A stationary receiver reports a heading, but it is not the direction the
    # vehicle is travelling - with no motion to average over, the compass drifts
    # and multipath sends it anywhere. A parked car therefore produces a
    # "direction change" of 100-160 degrees between successive fixes purely from
    # noise, and a classifier reading that as a feature sees evasive
    # manoeuvring where a car is simply sitting still. Observed directly: it
    # escalated a stationary vehicle in the `parked` simulator scenario to
    # P(suspicious) = 0.78.
    #
    # So direction is only derived when the vehicle is actually moving. Below the
    # stationary threshold the feature is 0.0 - not "unknown", because 0.0 is the
    # honest statement that no change of direction was observed.
    moving = speed_kmph is not None and speed_kmph >= STATIONARY_SPEED_KMPH
    direction_change = 0.0
    if moving and latest is not None:
        if latest.heading_degrees is not None and heading_degrees is not None:
            direction_change = angle_between_degrees(heading_degrees, latest.heading_degrees)
        elif distance_travelled > GPS_NOISE_METRES:
            # No reported heading: fall back to the direction the position
            # actually moved, which is weaker evidence but better than claiming
            # no change. The floor is above receiver noise - a sub-metre
            # displacement on a fix with 6 m of stated accuracy carries no
            # directional information at all, and deriving a bearing from it
            # would invent one.
            implied = bearing_degrees(latest.latitude, latest.longitude, latitude, longitude)
            if heading_degrees is not None:
                direction_change = angle_between_degrees(heading_degrees, implied)

    distance = distance_from_zone_metres if distance_from_zone_metres is not None else 0.0
    zone_breach = 1.0 if (not is_within_zone and distance > buffer_metres) else 0.0

    time_sin, time_cos = time_of_day_features(recorded_at)

    values = {
        "speed_kmph": float(speed_kmph) if speed_kmph is not None else 0.0,
        "distance_from_zone_metres": float(distance),
        "movement_duration_seconds": movement_duration_seconds(priors),
        "direction_change_degrees": float(direction_change),
        "distance_travelled_metres": float(distance_travelled),
        "zone_breach": zone_breach,
        "time_sin": time_sin,
        "time_cos": time_cos,
    }
    return FeatureVector(values=values)


#: Plain-English names, used to phrase a prediction's reason.
FEATURE_LABELS: dict[str, str] = {
    "speed_kmph": "speed",
    "distance_from_zone_metres": "distance from the zone boundary",
    "movement_duration_seconds": "time spent moving",
    "direction_change_degrees": "change of direction",
    "distance_travelled_metres": "distance travelled since the previous fix",
    "zone_breach": "geofence breach",
    "time_sin": "time of day",
    "time_cos": "time of day",
}


def feature_unit(name: str) -> str:
    if name.endswith("_kmph"):
        return "km/h"
    if name.endswith("_metres"):
        return "m"
    if name.endswith("_seconds"):
        return "s"
    if name.endswith("_degrees"):
        return "°"
    return ""


__all__ = [
    "FEATURE_COLUMNS",
    "FEATURE_LABELS",
    "STATIONARY_SPEED_KMPH",
    "FeatureVector",
    "PriorObservation",
    "extract_features",
    "feature_unit",
    "movement_duration_seconds",
    "time_of_day_features",
]
