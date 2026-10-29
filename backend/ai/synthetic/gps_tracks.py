"""Synthetic GPS tracks for development and demonstration.

Real telemetry requires a GPS receiver mounted in a vehicle. Most people
running this project locally do not have one, so this module generates the
*movement* that a receiver would have reported -- and every generated fix then
passes through the same geofence engine as a hardware fix. Nothing here
produces verdicts: the classification is genuinely computed downstream.

**Every fix this module emits is marked ``SIMULATED``** and the API records that
provenance on the row. The tracking dashboard renders a persistent banner
whenever any fix on screen is simulated, and the per-fix provenance is shown
next to the reading. Simulated data must never be mistakable for hardware data,
so the label travels with the data rather than living in a deployment setting.

Tracks are deterministic given a ``seed``. That is deliberate: a demo that
reproduces and a test that asserts on it are both more useful than one that
surprises you.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from ai.anomaly.geofence import bearing_degrees, destination_point, haversine_m

#: Seconds between consecutive fixes. Real yard trackers report every 15-60 s;
#: 30 s keeps a demo track short enough to read on one screen.
DEFAULT_FIX_INTERVAL_SECONDS = 30.0

#: Realistic consumer-GPS error, in metres, outdoors with a clear sky.
DEFAULT_ACCURACY_METRES = 6.0


@dataclass(frozen=True)
class SimulatedFix:
    """One generated position report, in the shape the ingest API expects."""

    latitude: float
    longitude: float
    speed_kmph: float
    heading_degrees: float
    recorded_at: datetime
    accuracy_metres: float = DEFAULT_ACCURACY_METRES
    #: What the generator intended at this point in the track. Exposed in the
    #: API response so a demo can show the planted behaviour next to whatever
    #: the engine actually concluded.
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "speed_kmph": round(self.speed_kmph, 2),
            "heading_degrees": round(self.heading_degrees, 2),
            "recorded_at": self.recorded_at.isoformat(),
            "accuracy_metres": round(self.accuracy_metres, 2),
            "note": self.note,
        }


@dataclass(frozen=True)
class SimulatedTrack:
    """A whole generated journey."""

    scenario: str
    description: str
    movement_expected: bool
    fixes: tuple[SimulatedFix, ...]

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "description": self.description,
            "movement_expected": self.movement_expected,
            "fix_count": len(self.fixes),
            "source": "SIMULATED",
            "starts_at": self.fixes[0].recorded_at.isoformat() if self.fixes else None,
            "ends_at": self.fixes[-1].recorded_at.isoformat() if self.fixes else None,
            "notes": sorted({fix.note for fix in self.fixes if fix.note}),
        }


def stable_seed(value: int | str) -> int:
    """Derive a reproducible 32-bit seed from an int or a string.

    ``hash()`` is deliberately salted per process, so using it here would make
    every "deterministic" track differ between runs and silently break both the
    demo and the tests. A digest is stable across processes and platforms.
    """
    if isinstance(value, int):
        return abs(value) % (2**32)
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _jittered(
    latitude: float, longitude: float, rng: np.random.Generator, metres: float
) -> tuple[float, float]:
    """Nudge a position by a random offset of up to ``metres``."""
    if metres <= 0:
        return latitude, longitude
    bearing = rng.uniform(0.0, 360.0)
    distance = rng.uniform(0.0, metres)
    return destination_point(latitude, longitude, bearing, distance)


def _stationary(
    latitude: float,
    longitude: float,
    *,
    count: int,
    start: datetime,
    interval: float,
    rng: np.random.Generator,
    jitter_metres: float,
    note: str,
    accuracy_metres: float = DEFAULT_ACCURACY_METRES,
) -> list[SimulatedFix]:
    """A parked vehicle: near-zero speed and small position wander."""
    fixes: list[SimulatedFix] = []
    for index in range(count):
        lat, lon = _jittered(latitude, longitude, rng, jitter_metres)
        fixes.append(
            SimulatedFix(
                latitude=round(lat, 6),
                longitude=round(lon, 6),
                speed_kmph=round(rng.uniform(0.0, 2.5), 2),
                heading_degrees=round(rng.uniform(0.0, 360.0), 2),
                recorded_at=start + timedelta(seconds=interval * index),
                accuracy_metres=accuracy_metres,
                note=note,
            )
        )
    return fixes


def _driving(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    *,
    speed_kmph: float,
    start: datetime,
    interval: float,
    rng: np.random.Generator,
    jitter_metres: float,
    note: str,
    accuracy_metres: float = DEFAULT_ACCURACY_METRES,
) -> list[SimulatedFix]:
    """Drive from one point to another at roughly ``speed_kmph``.

    Positions are interpolated along the straight line between the endpoints --
    accurate enough over the few hundred metres a yard spans -- with GPS noise
    added afterwards so the track does not look artificially perfect.
    """
    metres_per_step = max(1.0, speed_kmph * (interval / 3600.0) * 1000.0)
    heading = bearing_degrees(start_latitude, start_longitude, end_latitude, end_longitude)
    # Walk outward in whole steps until the endpoint is passed, so the last fix
    # lands past the target rather than exactly on it.
    distance = 0.0
    fixes: list[SimulatedFix] = []
    index = 0
    total = _straight_line_distance_m(
        start_latitude, start_longitude, end_latitude, end_longitude
    )
    while distance <= total + metres_per_step:
        fraction = min(1.0, distance / total) if total > 0 else 1.0
        latitude = start_latitude + (end_latitude - start_latitude) * fraction
        longitude = start_longitude + (end_longitude - start_longitude) * fraction
        lat, lon = _jittered(latitude, longitude, rng, jitter_metres)
        fixes.append(
            SimulatedFix(
                latitude=round(lat, 6),
                longitude=round(lon, 6),
                speed_kmph=round(max(0.0, speed_kmph + rng.uniform(-3.0, 3.0)), 2),
                heading_degrees=round((heading + rng.uniform(-8.0, 8.0)) % 360.0, 2),
                recorded_at=start + timedelta(seconds=interval * index),
                accuracy_metres=accuracy_metres,
                note=note,
            )
        )
        distance += metres_per_step
        index += 1
    return fixes


def _straight_line_distance_m(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Distance in metres between two points, used only to size a track."""
    return haversine_m(latitude_a, longitude_a, latitude_b, longitude_b)


def _parking_spot(
    centre_latitude: float, centre_longitude: float, radius_metres: float, rng: np.random.Generator
) -> tuple[float, float]:
    """A believable bay: somewhere inside the fence, not dead centre."""
    bearing = rng.uniform(0.0, 360.0)
    offset = rng.uniform(0.2, 0.7) * radius_metres
    return destination_point(centre_latitude, centre_longitude, bearing, offset)


def generate_track(
    scenario: str,
    *,
    centre_latitude: float,
    centre_longitude: float,
    radius_metres: float,
    start_time: datetime,
    seed: int | str | None = None,
    interval_seconds: float = DEFAULT_FIX_INTERVAL_SECONDS,
) -> SimulatedTrack:
    """Build a deterministic track for ``scenario`` around a zone centre.

    ``seed`` may be any hashable; the same seed always produces the same track,
    which is what makes both the demo reproducible and the tests meaningful.
    """
    if scenario not in TRACK_SCENARIOS:
        raise ValueError(
            f"scenario must be one of {', '.join(sorted(TRACK_SCENARIOS))}; got {scenario!r}"
        )
    rng = np.random.default_rng(stable_seed(seed if seed is not None else scenario))
    builder = TRACK_SCENARIOS[scenario]["builder"]
    return builder(
        centre_latitude=centre_latitude,
        centre_longitude=centre_longitude,
        radius_metres=radius_metres,
        start_time=start_time,
        rng=rng,
        interval=interval_seconds,
    )


def _scenario_parked(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """A vehicle sitting in its bay. Everything should read NORMAL."""
    spot = _parking_spot(centre_latitude, centre_longitude, radius_metres, rng)
    fixes = _stationary(
        spot[0],
        spot[1],
        count=12,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="parked inside the geofence",
    )
    return SimulatedTrack(
        scenario="parked",
        description=(
            "A parked vehicle with ordinary GPS wander. Every fix should classify as "
            "NORMAL; this is the baseline the other scenarios are compared against."
        ),
        movement_expected=False,
        fixes=tuple(fixes),
    )


def _scenario_jitter(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """GPS noise near the boundary, to show the buffer absorbing it."""
    edge = destination_point(centre_latitude, centre_longitude, 0.0, radius_metres * 0.95)
    fixes = _stationary(
        edge[0],
        edge[1],
        count=12,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=18.0,
        note="GPS noise near the boundary",
        accuracy_metres=14.0,
    )
    return SimulatedTrack(
        scenario="jitter",
        description=(
            "A stationary vehicle whose receiver is noisy enough to push some fixes "
            "past the boundary. The buffer should absorb most of it: warnings at worst, "
            "never a breach. This is the false-positive case the tolerance band exists "
            "for."
        ),
        movement_expected=False,
        fixes=tuple(fixes),
    )


def _scenario_arrival(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """Valet drives the car in from the road and parks it."""
    entry = destination_point(centre_latitude, centre_longitude, 135.0, radius_metres + 420.0)
    spot = _parking_spot(centre_latitude, centre_longitude, radius_metres, rng)
    fixes = _driving(
        entry[0],
        entry[1],
        spot[0],
        spot[1],
        speed_kmph=28.0,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=4.0,
        note="driving in from the road",
    )
    fixes += _stationary(
        spot[0],
        spot[1],
        count=5,
        start=fixes[-1].recorded_at + timedelta(seconds=interval),
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="parked inside the geofence",
    )
    return SimulatedTrack(
        scenario="arrival",
        description=(
            "A valet drives the vehicle in from the road and parks it. The fixes "
            "before the vehicle reaches the yard are genuinely outside the geofence "
            "and should be flagged: an arrival and an escape are the same telemetry, "
            "distinguished only by context. Note how the speed findings clear once "
            "the vehicle is inside."
        ),
        movement_expected=True,
        fixes=tuple(fixes),
    )


def _scenario_retrieval(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """A legitimate retrieval: out to the handover point and back."""
    spot = _parking_spot(centre_latitude, centre_longitude, radius_metres, rng)
    handover = destination_point(centre_latitude, centre_longitude, 300.0, radius_metres + 260.0)
    fixes = _stationary(
        spot[0],
        spot[1],
        count=3,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="waiting in the bay",
    )
    cursor = fixes[-1].recorded_at + timedelta(seconds=interval)
    fixes += _driving(
        spot[0],
        spot[1],
        handover[0],
        handover[1],
        speed_kmph=26.0,
        start=cursor,
        interval=interval,
        rng=rng,
        jitter_metres=4.0,
        note="retrieving the vehicle",
    )
    cursor = fixes[-1].recorded_at + timedelta(seconds=interval)
    fixes += _stationary(
        handover[0],
        handover[1],
        count=3,
        start=cursor,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="waiting at the handover point",
    )
    cursor = fixes[-1].recorded_at + timedelta(seconds=interval)
    fixes += _driving(
        handover[0],
        handover[1],
        spot[0],
        spot[1],
        speed_kmph=24.0,
        start=cursor,
        interval=interval,
        rng=rng,
        jitter_metres=4.0,
        note="returning the vehicle to its bay",
    )
    return SimulatedTrack(
        scenario="retrieval",
        description=(
            "A legitimate retrieval while the custody session has one in progress. "
            "The vehicle leaves the geofence, so zone findings still fire - a "
            "geofence engine that knew about tasks would be a different system - "
            "but the 'moving with no active task' rule stays quiet. That difference "
            "is the point of passing task context into the classifier."
        ),
        movement_expected=True,
        fixes=tuple(fixes),
    )


def _scenario_unauthorised(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """The vehicle is taken out fast with no retrieval task. Should reach BREACH."""
    spot = _parking_spot(centre_latitude, centre_longitude, radius_metres, rng)
    fixes = _stationary(
        spot[0],
        spot[1],
        count=4,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="parked inside the geofence",
    )
    target = destination_point(centre_latitude, centre_longitude, 20.0, radius_metres + 900.0)
    fixes += _driving(
        spot[0],
        spot[1],
        target[0],
        target[1],
        speed_kmph=58.0,
        start=fixes[-1].recorded_at + timedelta(seconds=interval),
        interval=interval,
        rng=rng,
        jitter_metres=5.0,
        note="leaving the yard at speed with no task",
    )
    return SimulatedTrack(
        scenario="unauthorised",
        description=(
            "The vehicle is driven out of the yard at road speed while no retrieval "
            "is in progress. This should escalate through SUSPICIOUS to BREACH and "
            "raise geofence, speed and abnormal-movement alerts together."
        ),
        movement_expected=False,
        fixes=tuple(fixes),
    )


def _scenario_telemetry_loss(
    *, centre_latitude, centre_longitude, radius_metres, start_time, rng, interval
) -> SimulatedTrack:
    """The tracker goes quiet for twelve minutes, then resumes."""
    spot = _parking_spot(centre_latitude, centre_longitude, radius_metres, rng)
    fixes = _stationary(
        spot[0],
        spot[1],
        count=4,
        start=start_time,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="parked inside the geofence",
    )
    gap_start = fixes[-1].recorded_at + timedelta(seconds=720.0)
    fixes += _stationary(
        spot[0],
        spot[1],
        count=4,
        start=gap_start,
        interval=interval,
        rng=rng,
        jitter_metres=3.0,
        note="reporting again after the tracker went quiet",
    )
    return SimulatedTrack(
        scenario="telemetry_loss",
        description=(
            "The tracker stops reporting for twelve minutes and then resumes in the "
            "same place. Nothing is wrong with the vehicle, but a silent tracker is "
            "itself a security event: the absence of evidence is not evidence of "
            "safety."
        ),
        movement_expected=False,
        fixes=tuple(fixes),
    )


TRACK_SCENARIOS: dict[str, dict] = {
    "parked": {"builder": _scenario_parked, "label": "Parked in bay"},
    "jitter": {"builder": _scenario_jitter, "label": "GPS noise near the boundary"},
    "arrival": {"builder": _scenario_arrival, "label": "Valet drives the vehicle in"},
    "retrieval": {"builder": _scenario_retrieval, "label": "Legitimate retrieval"},
    "unauthorised": {"builder": _scenario_unauthorised, "label": "Unauthorised movement"},
    "telemetry_loss": {"builder": _scenario_telemetry_loss, "label": "Tracker goes quiet"},
}


def scenario_catalogue() -> list[dict]:
    """Describe the available scenarios, for the demo picker in the UI."""
    return [
        {"id": key, "label": value["label"]} for key, value in TRACK_SCENARIOS.items()
    ]


__all__ = [
    "DEFAULT_ACCURACY_METRES",
    "DEFAULT_FIX_INTERVAL_SECONDS",
    "TRACK_SCENARIOS",
    "SimulatedFix",
    "SimulatedTrack",
    "generate_track",
    "scenario_catalogue",
]
