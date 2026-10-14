"""Features for retrieval-time prediction.

Every feature here is either a real column, a count taken from live rows, or a
function of the request timestamp. Nothing is inferred from a signal the
platform does not actually have -- that mistake was already made once in this
codebase, where a stationary GPS receiver's drifting heading was read as a
"change of direction" and the classifier flagged a parked car as suspicious.
A feature that can report a value nobody observed is worse than no feature,
because the model acts on it confidently.

Two things are deliberately *absent*:

* **No distance-to-the-car feature.** The platform stores a geofence polygon per
  zone but has no map of the yard and no bay positions, so it cannot know how
  far a given vehicle is from the handover point. Zone capacity and occupancy
  stand in for it, and the honest thing is to say so rather than synthesise a
  distance.
* **No per-valet skill or fatigue.** ``active_valets`` is a headcount. Treating
  one valet as interchangeable with another is a simplification, but it is one
  the data actually supports.

``ai/*`` must not import Flask or SQLAlchemy, so this module takes plain numbers
and returns a plain dict. ``services/retrieval.py`` is responsible for reading
the database and calling this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

#: The exact feature order the model is trained on and expects at inference.
#: Order is part of the contract: a scaler fitted on one column order silently
#: produces nonsense if the caller reorders them, so the model validates against
#: this tuple rather than trusting the dict it is handed.
RETRIEVAL_FEATURE_COLUMNS: tuple[str, ...] = (
    "queue_size",
    "queue_position",
    "active_valets",
    "zone_capacity",
    "zone_occupancy_percent",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "historical_zone_mean_minutes",
    "vehicle_flagged",
)

#: Plain-English names, used to phrase an estimate's explanation.
FEATURE_LABELS: dict[str, str] = {
    "queue_size": "retrieval queue length",
    "queue_position": "position in queue",
    "active_valets": "valets on duty",
    "zone_capacity": "zone capacity",
    "zone_occupancy_percent": "zone occupancy",
    "hour_sin": "time of day",
    "hour_cos": "time of day",
    "dow_sin": "day of week",
    "dow_cos": "day of week",
    "historical_zone_mean_minutes": "historical average for this zone",
    "vehicle_flagged": "vehicle flagged for review",
}


def feature_unit(name: str) -> str:
    if name.endswith("_minutes"):
        return "min"
    if name.endswith("_percent"):
        return "%"
    if name in ("queue_size", "queue_position", "active_valets", "zone_capacity"):
        return ""
    if name == "vehicle_flagged":
        return ""
    return ""


@dataclass(frozen=True)
class RetrievalContext:
    """Everything known at the moment a customer asks for their car.

    Fields default to the neutral value so a caller can build a context from a
    partial row, but ``historical_zone_mean_minutes`` deliberately has no
    default: an estimate made without any history for the zone should fail
    loudly rather than silently assume zero.
    """

    queue_size: int
    queue_position: int
    active_valets: int
    zone_capacity: int
    zone_occupancy: int
    requested_at: datetime
    historical_zone_mean_minutes: float
    vehicle_flagged: bool = False
    extra: dict[str, float] = field(default_factory=dict)


def time_of_day_features(moment: datetime) -> tuple[float, float]:
    """Encode the hour as a point on a circle.

    A raw hour number makes 23:00 and 00:00 look maximally distant when they are
    one hour apart, and 12:00 equidistant from both when it is not. The sin/cos
    pair keeps midnight continuous, which matters here because retrieval demand
    is cyclical rather than linear.
    """
    seconds = (
        moment.hour * 3600 + moment.minute * 60 + moment.second + moment.microsecond / 1e6
    )
    angle = 2.0 * math.pi * (seconds / 86_400.0)
    return math.sin(angle), math.cos(angle)


def day_of_week_features(moment: datetime) -> tuple[float, float]:
    """Encode the weekday the same way, so Sunday and Monday are adjacent."""
    angle = 2.0 * math.pi * (moment.weekday() / 7.0)
    return math.sin(angle), math.cos(angle)


def occupancy_percent(occupancy: int, capacity: int) -> float:
    """How full the zone is, as a percentage, floored and capped.

    A zone can transiently report more vehicles than its nominal capacity when
    two are squeezed into one bay, so this is clamped rather than allowed to
    exceed 100 -- an uncapped ratio would let one bad row dominate the feature.
    A zero or negative capacity means the zone has no usable capacity recorded,
    and reporting 0% rather than dividing by zero is the honest fallback.
    """
    if capacity <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * occupancy / capacity))


def extract_features(context: RetrievalContext) -> dict[str, float]:
    """Build the feature vector for one retrieval request."""
    if context.queue_size < 0 or context.queue_position < 0:
        raise ValueError("queue size and position cannot be negative")
    if context.active_valets < 0:
        raise ValueError("active_valets cannot be negative")
    if context.historical_zone_mean_minutes < 0:
        raise ValueError("historical_zone_mean_minutes cannot be negative")

    hour_sin, hour_cos = time_of_day_features(context.requested_at)
    dow_sin, dow_cos = day_of_week_features(context.requested_at)

    values: dict[str, float] = {
        "queue_size": float(context.queue_size),
        "queue_position": float(context.queue_position),
        "active_valets": float(context.active_valets),
        "zone_capacity": float(context.zone_capacity),
        "zone_occupancy_percent": occupancy_percent(
            context.zone_occupancy, context.zone_capacity
        ),
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        "historical_zone_mean_minutes": float(context.historical_zone_mean_minutes),
        "vehicle_flagged": 1.0 if context.vehicle_flagged else 0.0,
    }
    values.update(context.extra)

    missing = [name for name in RETRIEVAL_FEATURE_COLUMNS if name not in values]
    if missing:  # pragma: no cover - guards against feature drift
        raise ValueError(f"feature vector is missing {missing}")
    return {name: float(values[name]) for name in RETRIEVAL_FEATURE_COLUMNS}


__all__ = [
    "FEATURE_LABELS",
    "RETRIEVAL_FEATURE_COLUMNS",
    "RetrievalContext",
    "day_of_week_features",
    "extract_features",
    "feature_unit",
    "occupancy_percent",
    "time_of_day_features",
]
