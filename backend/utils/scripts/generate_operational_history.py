"""Generate a realistic operational history so the manager dashboard has data.

**Why this exists.** The manager dashboard reports trends: vehicles per day,
retrieval times over time, alert rates, damage outcomes. A freshly seeded
database holds two tickets and one alert, and a chart drawn from two points is
not a chart — it is a lie with axes on it. Rather than let the dashboard
silently present noise as trend, this script fills the demo database with a
plausible operating history that the charts can actually describe.

**This is synthetic data and says so.** Every row it writes carries
``metadata_ = {"is_demo": True, "origin": "operational_history"}`` (or a
``[DEMO]`` marker where the schema has no metadata column), and the run is
recorded in ``schema_meta``. The dashboard endpoint reports the provenance so
the UI can label it. Nothing here is presented as a real valet operation, and
the models in this project are still trained on their own synthetic generators.

**Deterministic and idempotent.** IDs come from a fixed UUID namespace keyed on
the day and sequence, so re-running produces the same history rather than
duplicating it. The seed is a CLI flag, so a reviewer can regenerate a
different-but-equally-plausible dataset and see that the dashboard is not
tuned to one particular set of numbers.

Usage::

    python utils/scripts/generate_operational_history.py            # 90 days
    python utils/scripts/generate_operational_history.py --days 30
    python utils/scripts/generate_operational_history.py --seed 7 --reset
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete, func, select, text  # noqa: E402

from ai.anomaly import geofence as geofence_engine  # noqa: E402
from ai.synthetic.gps_tracks import generate_track  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from models.custody_session import CustodySession  # noqa: E402
from models.damage_detection import (  # noqa: E402
    DamageDetection,
    DamageType,
    DetectionMethod,
    VehicleRegion,
)
from models.enums import (  # noqa: E402
    AlertSeverity,
    AlertStatus,
    AlertType,
    CustodyStatus,
    DamageSeverity,
    InspectionOutcome,
    InspectionStage,
    PredictionStatus,
    TelemetrySource,
    TicketStatus,
    UserStatus,
    VehicleStatus,
    VerificationStatus,
    ZoneStatus,
)
from models.gps_event import GpsEvent  # noqa: E402
from models.parking_zone import ParkingZone  # noqa: E402
from models.retrieval_prediction import RetrievalPrediction  # noqa: E402
from models.security_alert import SecurityAlert  # noqa: E402
from models.user import User  # noqa: E402
from models.valet_ticket import ValetTicket  # noqa: E402
from models.vehicle import Vehicle  # noqa: E402
from models.vehicle_inspection import VehicleInspection  # noqa: E402
from utils.security import Role, hash_password  # noqa: E402
from utils.zone_geometry import zone_radius_metres  # noqa: E402

#: Fixed namespace so generated ids are stable across runs.
NAMESPACE = uuid.UUID("9c1f4b7a-2d5e-4a86-9f30-71b6c25e8d41")

ORIGIN = "operational_history"

#: Users and Vehicles have no metadata column, only `notes`, so provenance for
#: those two is carried in the note text. The marker is a constant so `_reset`
#: can find them again; only `ValetTicket` has a real JSONB metadata column.
DEMO_NOTE = "[DEMO] Generated operational history - not a real valet operation."

# ---------------------------------------------------------------------------
# Operating profile
# ---------------------------------------------------------------------------
#: Relative demand by weekday (Mon..Sun). Weekends are roughly half a weekday:
#: a valet operation serving offices and restaurants is not flat across the week,
#: and a flat series would make the "vehicles per day" chart meaningless.
WEEKDAY_DEMAND = (1.00, 1.06, 1.02, 1.09, 1.24, 0.62, 0.48)

#: Mean vehicles on a demand-1.0 day.
BASE_DAILY_VEHICLES = 14

#: Mean custody duration in hours, log-normally distributed. A log-normal rather
#: than a normal because durations cannot go negative and a few people leave a
#: car all day; a symmetric distribution would put mass below zero.
CUSTODY_MEAN_HOURS = 4.6
CUSTODY_SIGMA = 0.55

MAKES = (
    ("Maruti", ("Swift", "Baleno", "Brezza")),
    ("Hyundai", ("i20", "Creta", "Venue")),
    ("Tata", ("Nexon", "Altroz", "Harrier")),
    ("Honda", ("City", "Amaze", "WR-V")),
    ("Toyota", ("Glanza", "Urban Cruiser", "Innova")),
)

FIRST_NAMES = (
    "Aisha", "Rohit", "Meera", "Arjun", "Fatima", "Vikram", "Neha", "Kabir",
    "Sana", "Dev", "Priya", "Imran", "Lakshmi", "Yusuf", "Ananya", "Rahul",
)
LAST_NAMES = (
    "Verma", "Shah", "Iyer", "Khan", "Nair", "Mehta", "Reddy", "Bose",
    "Chopra", "Desai", "Qureshi", "Menon", "Kapoor", "Sethi",
)

DAMAGE_TYPES = (
    (DamageType.SCRATCH, 0.34),
    (DamageType.DENT, 0.22),
    (DamageType.PAINT_CHIP, 0.16),
    (DamageType.DIRT_STAIN, 0.11),
    (DamageType.CRACK, 0.07),
    (DamageType.RUST, 0.05),
    (DamageType.GLASS_DAMAGE, 0.03),
    (DamageType.TYRE_DAMAGE, 0.02),
)

REGIONS = tuple(VehicleRegion)

ALERT_PROFILES = (
    (AlertType.GEOFENCE_BREACH, 0.38, AlertSeverity.HIGH),
    (AlertType.ABNORMAL_MOVEMENT, 0.22, AlertSeverity.MEDIUM),
    (AlertType.UNUSUAL_SPEED, 0.16, AlertSeverity.MEDIUM),
    (AlertType.ROUTE_DEVIATION, 0.10, AlertSeverity.LOW),
    (AlertType.TELEMETRY_LOSS, 0.08, AlertSeverity.LOW),
    (AlertType.UNAUTHORIZED_ACCESS, 0.04, AlertSeverity.CRITICAL),
    (AlertType.TAMPER_DETECTED, 0.02, AlertSeverity.CRITICAL),
)


@dataclass
class Stats:
    days: int = 0
    customers: int = 0
    vehicles: int = 0
    tickets: int = 0
    sessions: int = 0
    inspections: int = 0
    detections: int = 0
    alerts: int = 0
    predictions: int = 0
    gps_events: int = 0
    skipped: int = 0
    by_day: dict = field(default_factory=dict)


def _demo_id(kind: str, key: str) -> uuid.UUID:
    """A stable id for a generated row, so re-running does not duplicate it."""
    return uuid.uuid5(NAMESPACE, f"{ORIGIN}:{kind}:{key}")


def _weighted(choices, rng: random.Random):
    """Pick from ``((value, weight), ...)``.

    Materialised first, because callers pass generator expressions and a
    generator can be iterated only once - summing it would leave nothing for
    the selection loop.
    """
    options = list(choices)
    total = sum(weight for _, weight in options)
    point = rng.random() * total
    for value, weight in options:
        point -= weight
        if point <= 0:
            return value
    return options[-1][0]


def _plate(rng: random.Random) -> str:
    """A plate in the shape the validator accepts: 2 letters, 2 digits, 2
    letters, 4 digits."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return (
        f"{''.join(rng.choice(letters) for _ in range(2))}"
        f"{rng.randint(10, 99)}"
        f"{''.join(rng.choice(letters) for _ in range(2))}"
        f"{rng.randint(1000, 9999)}"
    )


def _lognormal_hours(rng: random.Random) -> float:
    """A custody duration in hours, floored so nobody gets their car back in
    negative time."""
    hours = math.exp(rng.gauss(math.log(CUSTODY_MEAN_HOURS), CUSTODY_SIGMA))
    return max(0.4, min(hours, 22.0))


def _drop_off_time(day: datetime, rng: random.Random) -> datetime:
    """When the car arrived.

    Weighted towards midday with a morning and an evening shoulder, because
    arrivals are not uniform across the day and a uniform assumption would show
    up as a flat line in any hourly view.
    """
    weights = (
        (8, 0.05), (9, 0.08), (10, 0.11), (11, 0.13), (12, 0.14), (13, 0.13),
        (14, 0.10), (15, 0.08), (16, 0.07), (17, 0.05), (18, 0.03), (19, 0.02),
        (20, 0.01),
    )
    hour = _weighted(weights, rng)
    return datetime.combine(day.date(), time(hour, rng.randint(0, 59)), tzinfo=timezone.utc)


def _queue_pressure(hour: int, weekday: int) -> float:
    """How busy the desk is, which drives retrieval time.

    Rush hour and weekends both slow a retrieval down, so the retrieval chart
    has a real signal in it rather than noise around a constant.
    """
    base = 1.0
    if hour in (12, 13, 18, 19):
        base += 0.55
    if weekday >= 5:
        base += 0.25
    return base


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
def _ensure_zones(rng: random.Random) -> list[ParkingZone]:
    """Use the seeded zones if present, else create a small set."""
    zones = list(db.session.execute(select(ParkingZone)).scalars())
    if zones:
        return zones
    specs = (
        ("ZONE-A", "Main Yard", 40, 30.0),
        ("ZONE-B", "Covered Parking", 60, 20.0),
        ("ZONE-C", "Overflow", 25, 55.0),
    )
    for i, (code, name, capacity, radius) in enumerate(specs):
        zone = ParkingZone(
            id=_demo_id("zone", code),
            code=code,
            name=name,
            description=f"[DEMO] Generated by {ORIGIN}.",
            capacity=capacity,
            current_occupancy=0,
            status=str(ZoneStatus.ACTIVE),
            centre_latitude=27.4920 + i * 0.0009,
            centre_longitude=77.6730 + i * 0.0011,
            radius_metres=radius,
        )
        db.session.add(zone)
        zones.append(zone)
    db.session.flush()
    return zones


def _ensure_valets(rng: random.Random) -> list[User]:
    """The staff who appear as the operator on generated sessions."""
    staff = list(
        db.session.execute(
            select(User).where(
                User.role.in_([Role.VALET_OPERATOR.value, Role.VALET_MANAGER.value])
            )
        ).scalars()
    )
    if staff:
        return staff

    password = hash_password("Demo#Passw0rd")
    for i, name in enumerate(("Suresh Kumar", "Priya Nair", "Karan Malhotra")):
        role = Role.VALET_MANAGER if i == 2 else Role.VALET_OPERATOR
        user = User(
            id=_demo_id("valet", str(i)),
            email=f"valet{i + 1}@example.com",
            password_hash=password,
            full_name=name,
            role=str(role),
            status=str(UserStatus.ACTIVE),
            phone=f"+91 90000 0001{i}",
        )
        db.session.add(user)
        staff.append(user)
    db.session.flush()
    return staff


def _customer(rng: random.Random, index: int) -> User:
    """One demo customer. Reused across several visits, because the same people
    come back — a customer who never returns would make the vehicle mix look
    random in a way real operations are not."""
    uid = _demo_id("customer", str(index))
    existing = db.session.get(User, uid)
    if existing is not None:
        return existing

    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    user = User(
        id=uid,
        email=f"demo.{first.lower()}.{last.lower()}{index}@example.com",
        password_hash=hash_password("Demo#Passw0rd"),
        full_name=f"{first} {last}",
        role=str(Role.CUSTOMER),
        status=str(UserStatus.ACTIVE),
        phone=f"+91 9{rng.randint(100000000, 999999999)}",
        notes=DEMO_NOTE,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _vehicle(rng: random.Random, owner: User, index: int) -> Vehicle:
    """One demo vehicle belonging to ``owner``."""
    vid = _demo_id("vehicle", f"{owner.id}:{index}")
    existing = db.session.get(Vehicle, vid)
    if existing is not None:
        return existing

    make, models = rng.choice(MAKES)
    raw = _plate(rng)
    vehicle = Vehicle(
        id=vid,
        owner_id=owner.id,
        plate_number=f"{raw[:2]} {raw[2:4]} {raw[4:6]} {raw[6:]}",
        normalized_plate=raw,
        make=make,
        model=rng.choice(models),
        year=rng.randint(2016, 2025),
        color=rng.choice(("White", "Silver", "Grey", "Black", "Red", "Blue")),
        status=str(VehicleStatus.REGISTERED),
        notes=DEMO_NOTE,
    )
    db.session.add(vehicle)
    db.session.flush()
    return vehicle


# ---------------------------------------------------------------------------
# One day of operations
# ---------------------------------------------------------------------------
def _generate_day(
    day: datetime,
    *,
    rng: random.Random,
    zones: list[ParkingZone],
    valets: list[User],
    verifiers: list[User],
    open_vehicle_ids: set,
    stats: Stats,
) -> None:
    weekday = day.weekday()
    expected = BASE_DAILY_VEHICLES * WEEKDAY_DEMAND[weekday]
    # Poisson-ish count: the number of arrivals on a day varies around the mean.
    count = max(0, int(round(rng.gauss(expected, math.sqrt(expected)))))
    stats.by_day[day.date().isoformat()] = count

    for seq in range(count):
        key = f"{day.date().isoformat()}:{seq}"
        ticket_id = _demo_id("ticket", key)
        if db.session.get(ValetTicket, ticket_id) is not None:
            stats.skipped += 1
            continue

        customer_index = rng.randint(0, 39)
        owner = _customer(rng, customer_index)
        vehicle = _vehicle(rng, owner, rng.randint(0, 1))
        zone = rng.choice(zones)
        valet = rng.choice(valets)
        reviewer = rng.choice(verifiers)

        dropped_off = _drop_off_time(day, rng)
        # Do not write a drop-off in the future: "today" is partly elapsed.
        now = datetime.now(timezone.utc)
        if dropped_off > now - timedelta(minutes=5):
            stats.skipped += 1
            continue

        duration_hours = _lognormal_hours(rng)
        # Most cars have come and gone; the last few hours of today are still open.
        is_open = (day.date() == now.date()) and rng.random() < 0.55

        retrieval_requested = dropped_off + timedelta(hours=duration_hours)
        hour_pressure = _queue_pressure(retrieval_requested.hour, weekday)
        queue_position = max(1, int(rng.gauss(2.2 * hour_pressure, 1.1)))
        # The thing the retrieval chart measures: minutes from request to ready.
        retrieval_minutes = max(
            3.0, rng.gauss(6.5 + 2.9 * queue_position * hour_pressure, 2.4)
        )
        ready_at = retrieval_requested + timedelta(minutes=retrieval_minutes)
        picked_up = ready_at + timedelta(minutes=max(1.0, rng.gauss(6.0, 4.0)))

        if picked_up > now:
            is_open = True

        # A vehicle may hold only one open custody session at a time - the
        # schema enforces it with a partial unique index, and it is the real
        # rule too: a car cannot be parked in two bays. If this vehicle already
        # has an open session, this visit is recorded as completed instead of
        # failing the whole run.
        if is_open and (
            vehicle.id in open_vehicle_ids
            or db.session.execute(
                select(func.count(CustodySession.id)).where(
                    CustodySession.vehicle_id == vehicle.id,
                    CustodySession.status != CustodyStatus.CHECKED_OUT.value,
                )
            ).scalar_one()
        ):
            is_open = False
        if is_open:
            open_vehicle_ids.add(vehicle.id)

        ticket = ValetTicket(
            id=ticket_id,
            ticket_code=f"VT-{key.replace(':', '')[-6:].upper()}",
            customer_id=owner.id,
            vehicle_id=vehicle.id,
            status=str(TicketStatus.ACTIVE if is_open else TicketStatus.COMPLETED),
            issued_at=dropped_off - timedelta(minutes=rng.randint(2, 20)),
            expected_return_at=dropped_off + timedelta(hours=duration_hours + 1),
            actual_drop_off_at=dropped_off,
            actual_pickup_at=None if is_open else picked_up,
            qr_payload=f"VALET:VT-{key.replace(':', '')[-6:].upper()}",
            qr_code_path=f"tickets/demo/{key.replace(':', '-')}.png",
            special_instructions="[DEMO] Generated operational history.",
            metadata_={"is_demo": True, "origin": ORIGIN},
        )
        db.session.add(ticket)
        db.session.flush()

        status = (
            rng.choice(
                [CustodyStatus.PARKED, CustodyStatus.CHECKED_IN, CustodyStatus.IN_TRANSIT]
            )
            if is_open
            else CustodyStatus.CHECKED_OUT
        )
        session = CustodySession(
            id=_demo_id("custody", key),
            ticket_id=ticket.id,
            vehicle_id=vehicle.id,
            zone_id=zone.id,
            assigned_valet_id=valet.id,
            received_by_id=valet.id,
            status=str(status),
            dropped_off_at=dropped_off,
            zone_assigned_at=dropped_off + timedelta(minutes=rng.randint(3, 9)),
            retrieval_requested_at=None if is_open else retrieval_requested,
            retrieval_started_at=(
                None
                if is_open
                else retrieval_requested + timedelta(minutes=retrieval_minutes * 0.45)
            ),
            ready_for_pickup_at=None if is_open else ready_at,
            picked_up_at=None if is_open else picked_up,
            expected_retrieval_minutes=int(round(retrieval_minutes)),
            handover_point=rng.choice(("Main Lobby - Bay 1", "Main Lobby - Bay 2", "Gate 3")),
            odometer_at_drop_off=rng.randint(8_000, 92_000),
            fuel_level_percent=rng.randint(15, 95),
            pre_existing_damage_notes="[DEMO] Generated operational history.",
            condition_accepted=None if is_open else True,
            mileage_driven=None if is_open else rng.randint(0, 2),
        )
        db.session.add(session)
        db.session.flush()
        stats.tickets += 1
        stats.sessions += 1

        # Decide up front whether this custody misbehaves, then let the
        # telemetry and the alert describe the same event. Choosing them
        # independently produced alerts with no supporting GPS history and
        # breaches with nobody looking at them.
        suspicious = (not is_open) and rng.random() < 0.055
        breaching_fix = _add_telemetry(
            session, zone, rng=rng, stats=stats, suspicious=suspicious
        )

        _add_inspections(
            session, rng=rng, stats=stats, closed=not is_open, reviewer=reviewer
        )

        if not is_open:
            _add_prediction(session, rng=rng, stats=stats,
                            queue_position=queue_position, actual=retrieval_minutes)
            # Alerts only make sense for a session that ran; an open car has had
            # less time to misbehave.
            if suspicious:
                _add_alert(session, zone, rng=rng, stats=stats, triager=reviewer,
                           gps_event=breaching_fix)


#: Which generated track a routine custody gets. These three stay inside the
#: zone, so they produce the NORMAL/WARNING readings that make up the bulk of
#: a real operating day. 'arrival' and 'retrieval' are deliberately absent:
#: they drive the vehicle out of the yard, and the distance ladder correctly
#: reports BREACH for a fix 300 m outside the ring even though the movement is
#: authorised. Seeding those against routine jobs would scatter red breaches
#: across ordinary work - the false positive that teaches people to ignore a
#: dashboard. The /gps/simulate endpoint still exposes all six scenarios for a
#: live demonstration.
NORMAL_TRACK_SCENARIOS = (
    ("parked", 0.62),
    ("jitter", 0.26),
    ("telemetry_loss", 0.12),
)

#: The one scenario that genuinely leaves the zone without authority. Used for
#: the small share of sessions that also carry a security alert, so an alert
#: always has telemetry behind it.
SUSPICIOUS_TRACK_SCENARIO = "unauthorised"


def _add_telemetry(
    session: CustodySession,
    zone: ParkingZone,
    *,
    rng: random.Random,
    stats: Stats,
    suspicious: bool,
) -> GpsEvent | None:
    """Write a simulated GPS track for one custody session.

    Returns the fix furthest outside the zone, or ``None`` when the track never
    left it. Callers attach that fix to the security alert so the dashboard can
    show the reading that triggered the alert rather than an alert with no
    supporting telemetry.

    Every fix is labelled ``SIMULATED`` and carries a device id that says so.
    Provenance is stored per row rather than inferred from a setting, because
    the project mandate is that generated data must never be mistaken for
    hardware data.
    """
    scenario = (
        SUSPICIOUS_TRACK_SCENARIO if suspicious else _weighted(NORMAL_TRACK_SCENARIOS, rng)
    )

    centre_lat = float(zone.centre_latitude)
    centre_lon = float(zone.centre_longitude)
    track = generate_track(
        scenario,
        centre_latitude=centre_lat,
        centre_longitude=centre_lon,
        radius_metres=zone_radius_metres(zone),
        start_time=session.dropped_off_at,
        # Keyed on the session, so re-running regenerates the identical track
        # instead of a new random one for the same custody window.
        seed=f"{ORIGIN}:{session.id}",
    )

    worst: GpsEvent | None = None
    worst_distance = 0.0
    priors: list[geofence_engine.PriorFix] = []

    for index, fix in enumerate(track.fixes):
        verdict = geofence_engine.evaluate_fix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            zone_ring=zone.geofence_polygon,
            zone_centre=(centre_lat, centre_lon),
            zone_radius_metres=None,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            recorded_at=fix.recorded_at,
            prior_fixes=tuple(priors),
            movement_expected=track.movement_expected,
        )
        priors.append(
            geofence_engine.PriorFix(
                fix.latitude, fix.longitude, fix.recorded_at, fix.speed_kmph
            )
        )
        priors = priors[-4:]

        distance = verdict.distance_from_boundary_metres or 0.0
        event = GpsEvent(
            id=_demo_id("gps", f"{session.id}:{index}"),
            custody_session_id=session.id,
            recorded_at=fix.recorded_at,
            latitude=fix.latitude,
            longitude=fix.longitude,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            accuracy_metres=fix.accuracy_metres,
            source=str(TelemetrySource.SIMULATED),
            device_id=f"SIM-TRACK-{scenario.upper()}",
            zone_id=zone.id,
            is_within_zone=verdict.is_within_zone,
            distance_from_zone_metres=verdict.distance_from_boundary_metres,
            movement_status=verdict.status,
        )
        db.session.add(event)
        stats.gps_events += 1

        if distance > worst_distance:
            worst, worst_distance = event, distance

    db.session.flush()
    return worst


def _add_inspections(
    session: CustodySession,
    *,
    rng: random.Random,
    stats: Stats,
    closed: bool,
    reviewer: User,
) -> None:
    """A drop-off set always; a pickup set once the car is back.

    The verification split matters more than the volume. A generator that
    confirmed every finding would imply the detector is never wrong, and one
    that left everything unverified would imply nobody ever reviews it. The
    realistic shape is: most findings confirmed, some rejected as false
    positives, and a tail still awaiting review.
    """
    drop_off = VehicleInspection(
        id=_demo_id("inspection", f"{session.id}:drop"),
        custody_session_id=session.id,
        stage=str(InspectionStage.DROP_OFF),
        outcome=str(InspectionOutcome.CLEAN),
        captured_at=session.dropped_off_at,
        location=session.handover_point,
        photo_count=4,
        damage_count=0,
        inspector_notes="[DEMO] Generated operational history.",
        photo_refs=[],
    )
    db.session.add(drop_off)
    db.session.flush()
    stats.inspections += 1

    if not closed:
        return

    pickup = VehicleInspection(
        id=_demo_id("inspection", f"{session.id}:pickup"),
        custody_session_id=session.id,
        stage=str(InspectionStage.PICKUP),
        captured_at=session.picked_up_at or session.dropped_off_at,
        location=session.handover_point,
        photo_count=4,
        inspector_notes="[DEMO] Generated operational history.",
        photo_refs=[],
    )
    db.session.add(pickup)
    db.session.flush()
    stats.inspections += 1

    # Roughly a quarter of returns come back with something flagged.
    if rng.random() > 0.26:
        pickup.outcome = str(InspectionOutcome.CLEAN)
        pickup.damage_count = 0
        return

    found = 0
    for _ in range(max(1, int(rng.gauss(1.5, 0.7)))):
        roll = rng.random()
        if roll < 0.58:
            verification = VerificationStatus.CONFIRMED
        elif roll < 0.80:
            verification = VerificationStatus.REJECTED
        else:
            verification = VerificationStatus.UNVERIFIED

        confidence = round(rng.uniform(0.42, 0.96), 3)
        severity = _weighted(
            (
                (DamageSeverity.COSMETIC, 0.42),
                (DamageSeverity.MINOR, 0.33),
                (DamageSeverity.MODERATE, 0.19),
                (DamageSeverity.SEVERE, 0.06),
            ),
            rng,
        )
        detection = DamageDetection(
            id=_demo_id("detection", f"{pickup.id}:{found}"),
            inspection_id=pickup.id,
            damage_type=str(_weighted(DAMAGE_TYPES, rng)),
            region=str(rng.choice(REGIONS)),
            severity=str(severity),
            confidence=confidence,
            is_pre_existing=rng.random() < 0.12,
            requires_customer_attention=severity
            in (DamageSeverity.MODERATE, DamageSeverity.SEVERE),
            bounding_box={
                "x": round(rng.uniform(0.05, 0.6), 3),
                "y": round(rng.uniform(0.05, 0.6), 3),
                "width": round(rng.uniform(0.08, 0.35), 3),
                "height": round(rng.uniform(0.06, 0.3), 3),
            },
            detection_reasoning=(
                "[DEMO] Generated finding: localised pixel-difference cluster "
                "exceeded the configured threshold against the drop-off frame."
            ),
            method=str(DetectionMethod.CLASSICAL_CV),
            pixel_area=rng.randint(900, 26_000),
            type_confidence=confidence,
            verification_status=str(verification),
            # The schema refuses a verified finding with no reviewer, which is
            # the rule the product enforces too: only a manager or admin may
            # stand behind a machine's guess, so the row has to say which one.
            verified_by_id=(
                reviewer.id if verification is not VerificationStatus.UNVERIFIED else None
            ),
            verified_at=(
                pickup.captured_at + timedelta(hours=rng.randint(1, 20))
                if verification is not VerificationStatus.UNVERIFIED
                else None
            ),
            verification_notes=(
                "[DEMO] Confirmed against the drop-off photograph."
                if verification is VerificationStatus.CONFIRMED
                else "[DEMO] False positive: shadow across the panel."
                if verification is VerificationStatus.REJECTED
                else None
            ),
        )
        db.session.add(detection)
        found += 1
        stats.detections += 1

    pickup.damage_count = found
    pickup.outcome = str(
        InspectionOutcome.REVIEWED if found else InspectionOutcome.CLEAN
    )
    db.session.flush()


def _add_prediction(
    session: CustodySession,
    *,
    rng: random.Random,
    stats: Stats,
    queue_position: int,
    actual: float,
) -> None:
    """A fulfilled prediction, with a predicted time that is *near* the actual.

    The error is deliberately not zero. A predictor that matches every outcome
    exactly would be an obvious fabrication, and the whole point of storing both
    numbers is so the dashboard can show how far off the estimate was.
    """
    predicted = max(3, int(round(actual + rng.gauss(0.4, 2.6))))
    requested = session.retrieval_requested_at
    db.session.add(
        RetrievalPrediction(
            id=_demo_id("prediction", str(session.id)),
            custody_session_id=session.id,
            requested_by_id=session.ticket.customer_id,
            assigned_valet_id=session.assigned_valet_id,
            status=str(PredictionStatus.FULFILLED),
            requested_at=requested,
            predicted_ready_at=requested + timedelta(minutes=predicted),
            actual_ready_at=session.ready_for_pickup_at,
            dispatch_after=requested + timedelta(minutes=max(0, predicted - 9)),
            predicted_wait_minutes=predicted,
            min_wait_minutes=max(1, predicted - 3),
            max_wait_minutes=predicted + 4,
            confidence=round(rng.uniform(0.52, 0.84), 3),
            queue_position=queue_position,
            method="ridge-regression",
            model_version="retrieval-baseline/1.0.0",
            inputs_snapshot={"generated_by": ORIGIN, "queue_position": queue_position},
            explanation=(
                "[DEMO] Generated for operational history. Baseline ridge model "
                "trained on synthetic data; not an operational forecast."
            ),
        )
    )
    stats.predictions += 1


def _add_alert(
    session: CustodySession,
    zone: ParkingZone,
    *,
    rng: random.Random,
    stats: Stats,
    triager: User,
    gps_event: GpsEvent | None = None,
) -> None:
    # Select the whole profile, so the severity stays bound to the type that
    # was chosen. Picking the type and looking its severity up separately would
    # work, but re-deriving a pairing is how the two drift apart.
    alert_type, _weight, severity = _weighted(
        ((profile, profile[1]) for profile in ALERT_PROFILES), rng
    )
    # Critical alerts are rare and get dealt with; low ones often just sit.
    roll = rng.random()
    if severity is AlertSeverity.CRITICAL:
        status = AlertStatus.RESOLVED if roll < 0.85 else AlertStatus.ACKNOWLEDGED
    elif severity is AlertSeverity.HIGH:
        status = (
            AlertStatus.RESOLVED if roll < 0.6
            else AlertStatus.ACKNOWLEDGED if roll < 0.85
            else AlertStatus.OPEN
        )
    else:
        status = (
            AlertStatus.RESOLVED if roll < 0.35
            else AlertStatus.ACKNOWLEDGED if roll < 0.55
            else AlertStatus.OPEN
        )

    occurred = (session.retrieval_requested_at or session.dropped_off_at) - timedelta(
        minutes=rng.randint(10, 180)
    )
    terminal = status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED)

    # Derived from each other rather than drawn independently. Two separate
    # offsets from `occurred` can land in either order, and the schema requires
    # resolution to follow acknowledgement - which is the real sequence of
    # events, not an arbitrary database rule.
    acknowledged_at = (
        occurred + timedelta(minutes=rng.randint(2, 40))
        if status is not AlertStatus.OPEN
        else None
    )
    resolved_at = (
        acknowledged_at + timedelta(minutes=rng.randint(5, 120)) if terminal else None
    )
    db.session.add(
        SecurityAlert(
            id=_demo_id("alert", f"{session.id}:{alert_type.value}"),
            custody_session_id=session.id,
            vehicle_id=session.vehicle_id,
            zone_id=zone.id,
            # The reading that triggered it, when the generated track produced
            # one, so the alert can be traced to actual telemetry.
            gps_event_id=gps_event.id if gps_event else None,
            alert_type=str(alert_type),
            severity=str(severity),
            status=str(status),
            title=f"[DEMO] {alert_type.value.replace('_', ' ').title()}",
            description="[DEMO] Generated by the operational history generator.",
            reason=(
                f"[DEMO] Simulated {alert_type.value.lower().replace('_', ' ')} "
                f"on zone {zone.code} during the custody window."
            ),
            confidence=round(rng.uniform(0.55, 0.97), 3),
            occurred_at=occurred,
            # The schema refuses an acknowledged alert with no acknowledger, and
            # a resolved one with no resolver. Same rule as damage verification:
            # "someone dealt with this" has to name the someone.
            acknowledged_by_id=triager.id if status is not AlertStatus.OPEN else None,
            acknowledged_at=acknowledged_at,
            resolved_by_id=triager.id if terminal else None,
            resolved_at=resolved_at,
            resolution_notes="[DEMO] Closed during generated history." if terminal else None,
        )
    )
    stats.alerts += 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _reset() -> None:
    """Delete previously generated rows, newest first.

    Scoped by the origin marker, so hand-made and seeded demo data survive. This
    is what makes the script safe to re-run after changing the profile.
    """
    generated_sessions = (
        select(CustodySession.id)
        .join(ValetTicket, ValetTicket.id == CustodySession.ticket_id)
        .where(ValetTicket.metadata_["origin"].as_string() == ORIGIN)
    )
    db.session.execute(delete(GpsEvent).where(GpsEvent.custody_session_id.in_(generated_sessions)))
    db.session.execute(
        delete(DamageDetection).where(
            DamageDetection.inspection_id.in_(
                select(VehicleInspection.id).where(
                    VehicleInspection.custody_session_id.in_(generated_sessions)
                )
            )
        )
    )
    db.session.execute(
        delete(VehicleInspection).where(
            VehicleInspection.custody_session_id.in_(generated_sessions)
        )
    )
    db.session.execute(
        delete(RetrievalPrediction).where(
            RetrievalPrediction.custody_session_id.in_(generated_sessions)
        )
    )
    db.session.execute(
        delete(SecurityAlert).where(
            SecurityAlert.custody_session_id.in_(generated_sessions)
        )
    )
    db.session.execute(delete(CustodySession).where(CustodySession.id.in_(generated_sessions)))
    db.session.execute(
        delete(ValetTicket).where(ValetTicket.metadata_["origin"].as_string() == ORIGIN)
    )
    db.session.execute(delete(Vehicle).where(Vehicle.notes == DEMO_NOTE))
    db.session.execute(delete(User).where(User.notes == DEMO_NOTE))
    db.session.commit()


def generate(*, days: int, seed: int, reset: bool) -> Stats:
    # Not cryptographic, deliberately: this seeds a demo dataset, and a
    # reproducible Mersenne Twister is the right tool. Nothing security-bearing
    # is derived from it - passwords use utils.security.hash_password.
    rng = random.Random(seed)  # noqa: S311
    stats = Stats(days=days)

    if reset:
        _reset()

    zones = _ensure_zones(rng)
    valets = _ensure_valets(rng)
    # Only a manager or admin may verify a damage finding, so the reviewer pool
    # is narrower than the valet pool. Without one, every generated finding
    # would have to stay UNVERIFIED and the damage chart would be one bar.
    verifiers = list(
        db.session.execute(
            select(User).where(User.role.in_([Role.VALET_MANAGER.value, Role.ADMIN.value]))
        ).scalars()
    )
    if not verifiers:
        raise SystemExit(
            "No manager or admin account exists to attribute findings to. "
            "Run `python utils/scripts/init_db.py --seed` first."
        )

    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Vehicles with an open session right now, so a repeat visit is recorded as
    # completed rather than colliding with the partial unique index.
    open_vehicle_ids: set = set()

    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        # A fresh RNG per day keeps each day reproducible on its own, so
        # extending the window does not rewrite the history already written.
        day_rng = random.Random(f"{ORIGIN}:{seed}:{day.date().isoformat()}")  # noqa: S311
        _generate_day(
            day,
            rng=day_rng,
            zones=zones,
            valets=valets,
            verifiers=verifiers,
            open_vehicle_ids=open_vehicle_ids,
            stats=stats,
        )
        if offset % 15 == 0:
            db.session.flush()

    # Occupancy must describe open sessions only; generated history is mostly
    # closed, so recounting avoids leaving zones "full" of cars that left.
    for zone in zones:
        open_count = db.session.execute(
            select(db.func.count(CustodySession.id)).where(
                CustodySession.zone_id == zone.id,
                CustodySession.picked_up_at.is_(None),
                CustodySession.status.in_(
                    [s.value for s in CustodyStatus if s.is_open]
                ),
            )
        ).scalar_one()
        zone.current_occupancy = min(zone.capacity, open_count)

    db.session.execute(
        text(
            "INSERT INTO schema_meta (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {
            "k": "operational_history",
            "v": (
                f'{{"generated_at": "{datetime.now(timezone.utc).isoformat()}", '
                f'"days": {days}, "seed": {seed}, "synthetic": true, '
                f'"tickets": {stats.tickets}, "sessions": {stats.sessions}, '
                f'"alerts": {stats.alerts}, "detections": {stats.detections}}}'
            ),
        },
    )
    db.session.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="days of history (default 90)")
    parser.add_argument("--seed", type=int, default=20260909, help="RNG seed")
    parser.add_argument(
        "--reset", action="store_true", help="delete previously generated rows first"
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        stats = generate(days=args.days, seed=args.seed, reset=args.reset)

    print()
    print(f"  days of history       {stats.days}")
    print(f"  tickets               {stats.tickets}")
    print(f"  custody sessions      {stats.sessions}")
    print(f"  inspections           {stats.inspections}")
    print(f"  damage findings       {stats.detections}")
    print(f"  security alerts       {stats.alerts}")
    print(f"  retrieval predictions {stats.predictions}")
    print(f"  gps fixes (simulated) {stats.gps_events}")
    if stats.skipped:
        print(f"  skipped (dup/future)  {stats.skipped}")
    print()
    print("  " + "!" * 70)
    print("  SYNTHETIC DATA. Generated for demonstration so the manager dashboard")
    print("  has enough history to chart. Every row is marked is_demo/origin and")
    print("  must not be presented as a record of a real valet operation.")
    print("  " + "!" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
