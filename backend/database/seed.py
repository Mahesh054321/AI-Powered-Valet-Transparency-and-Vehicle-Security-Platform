"""Idempotent seeding of reference and demonstration data.

Two responsibilities, kept deliberately separate:

``bootstrap_admin()``
    Creates the platform administrator from ``SEED_ADMIN_EMAIL`` /
    ``SEED_ADMIN_PASSWORD``. If no password is configured one is generated and
    printed **once**, so a weak default never exists. Nothing is hardcoded.

``seed_demo_data()``
    Creates a small but complete demonstration dataset that exercises the whole
    custody workflow: customers, vehicles, zones, tickets, an open and a closed
    custody session, telemetry, inspections, damage findings, an alert, a
    retrieval prediction and audit entries.

Honesty rules this module follows, because the project mandate requires them:

* Every GPS fix is labelled ``SIMULATED``. Nothing here pretends to be hardware
  telemetry, and ``TelemetrySource.SIMULATED`` is stored per row.
* Every damage detection records ``method=CLASSICAL_CV`` with its reasoning and
  parameters. No finding claims to come from a trained model.
* Demo rows are tagged ``is_demo`` in their JSON metadata columns where the
  schema has one, and demo users carry a ``[DEMO]`` marker in ``notes``.
* Photographs are represented by validated **references** (path, size,
  dimensions, MIME type, SHA-256), never by image bytes.

Identifiers are derived deterministically from a fixed UUID5 namespace so the
dataset is stable across runs and tests can reference specific rows. Every step
is idempotent: re-running ``seed_demo_data()`` creates nothing twice.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ai.anomaly import geofence as geofence_engine
from ai.synthetic.gps_tracks import generate_track
from app.extensions import db
from models import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    AuditAction,
    AuditLog,
    AuditOutcome,
    CustodySession,
    CustodyStatus,
    DamageDetection,
    DamageSeverity,
    DamageType,
    DetectionMethod,
    GpsEvent,
    InspectionOutcome,
    InspectionStage,
    ParkingZone,
    PredictionStatus,
    RetrievalPrediction,
    SecurityAlert,
    TelemetrySource,
    TicketStatus,
    User,
    UserStatus,
    ValetTicket,
    Vehicle,
    VehicleInspection,
    VehicleRegion,
    VehicleStatus,
    ZoneStatus,
)
from utils.image_ref import validate_image_ref_list
from utils.security import Role, hash_password
from utils.zone_geometry import zone_radius_metres

#: Fixed namespace so demo identifiers are stable across runs and machines.
DEMO_NAMESPACE = uuid.UUID("6f1e2d3c-4b5a-4968-8776-655443322110")

#: Demo account passwords. These are demonstration credentials for a local
#: academic deployment, clearly marked as such; production seeding comes from
#: the environment (see ``bootstrap_admin``).
# A fixed, openly documented credential for the seeded demonstration accounts
# on a local academic deployment. It is not a secret and never ships as a
# production default: the real administrator password comes from the
# environment (see `bootstrap_admin`).
DEMO_PASSWORD = "Demo#Passw0rd"  # noqa: S105


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    generated_password: str | None = None

    def merge(self, other: SeedResult) -> SeedResult:
        self.created.extend(other.created)
        self.skipped.extend(other.skipped)
        if other.generated_password and not self.generated_password:
            self.generated_password = other.generated_password
        return self


def _demo_id(kind: str, key: str) -> uuid.UUID:
    """Deterministic UUID for a demo row, stable across runs."""
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{key}")


def _demo_sha256(*parts: str) -> str:
    """Stable placeholder digest for demo image references."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _demo_photo(label: str, *, width: int = 3024, height: int = 4032) -> dict:
    """A validated image *reference* for a demo photograph.

    The bytes do not exist in this repository; the reference is what the schema
    stores, and it passes the same validation real uploads go through.
    """
    refs = validate_image_ref_list(
        [
            {
                "path": f"inspections/demo/{label}.jpg",
                "byte_size": 248_371,
                "width": width,
                "height": height,
                "mime_type": "image/jpeg",
                "sha256": _demo_sha256("demo-photo", label),
                "captured_at": "2026-09-07T09:12:44+00:00",
                "label": label,
            }
        ]
    )
    return refs[0]


# ---------------------------------------------------------------------------
# Administrator
# ---------------------------------------------------------------------------
def bootstrap_admin(app) -> SeedResult:
    """Ensure the bootstrap administrator exists.

    Credentials come from the environment. With no configured password one is
    generated and printed once, so no weak default is ever written.
    """
    result = SeedResult()
    email = app.config["SEED_ADMIN_EMAIL"]
    full_name = app.config.get("SEED_ADMIN_FULL_NAME") or "Platform Administrator"
    password = app.config.get("SEED_ADMIN_PASSWORD") or ""

    if not password:
        password = secrets.token_urlsafe(12)
        result.generated_password = password

    existing = db.session.get(User, _demo_id("user", email))
    if existing is None:
        existing = (
            db.session.query(User).filter(User.email == email).one_or_none()
        )

    if existing is not None:
        result.skipped.append(f"{email} ({Role.ADMIN}) - already present")
        return result

    admin = User(
        id=_demo_id("user", email),
        email=email,
        full_name=full_name,
        role=str(Role.ADMIN),
        status=str(UserStatus.ACTIVE),
        is_active=True,
        notes="Bootstrap administrator created by the seeder.",
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.flush()

    _log(
        action=AuditAction.CREATE,
        entity_type="users",
        entity_id=admin.id,
        description="Bootstrap administrator created by database seeding.",
        actor_id=admin.id,
        actor_role=str(Role.ADMIN),
        actor_email=admin.email,
    )
    result.created.append(f"{email} ({Role.ADMIN})")
    return result


def _log(
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    actor_email: str | None = None,
    custody_session_id: uuid.UUID | None = None,
    outcome: str = str(AuditOutcome.SUCCESS),
    old_values: dict | None = None,
    new_values: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        actor_email=actor_email,
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        old_values=old_values,
        new_values=new_values,
        custody_session_id=custody_session_id,
        request_id=f"seed-{uuid.uuid4().hex[:12]}",
    )
    db.session.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Demonstration dataset
# ---------------------------------------------------------------------------
def _seed_users() -> tuple[SeedResult, dict[str, User]]:
    result = SeedResult()
    specs = [
        ("customer.aisha@example.com", "Aisha Verma", Role.CUSTOMER, "1"),
        ("customer.rohit@example.com", "Rohit Menon", Role.CUSTOMER, "2"),
        ("customer.meera@example.com", "Meera Iyer", Role.CUSTOMER, "6"),
        ("valet.suresh@example.com", "Suresh Yadav", Role.VALET_OPERATOR, "3"),
        ("valet.priya@example.com", "Priya Nair", Role.VALET_OPERATOR, "4"),
        ("manager.karan@example.com", "Karan Bhatt", Role.VALET_MANAGER, "5"),
    ]

    users: dict[str, User] = {}
    password_hash = hash_password(DEMO_PASSWORD)

    for email, full_name, role, marker in specs:
        key = f"demo-{marker}"
        user = db.session.get(User, _demo_id("user", key))
        if user is not None:
            users[key] = user
            result.skipped.append(f"{email} ({role}) - already present")
            continue

        user = User(
            id=_demo_id("user", key),
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            phone=f"+91 98{marker}00 {marker}00 {marker}0",
            role=str(role),
            status=str(UserStatus.ACTIVE),
            is_active=True,
            # Deliberately NOT the password. `notes` is an ordinary text
            # column that an admin screen would render verbatim, so writing
            # the shared demo password here would put a credential back into
            # plain text after all the hashing work. Reviewers get the
            # password from the README and `SEED_DEMO_PASSWORD`.
            notes="[DEMO] Seeded demonstration account. Shared demo password - see README.",
        )
        db.session.add(user)
        users[key] = user
        result.created.append(f"{email} ({role})")

    db.session.flush()
    return result, users


def _seed_zones() -> tuple[SeedResult, dict[str, ParkingZone]]:
    result = SeedResult()
    # A roughly rectangular lot near Mathura, described in GeoJSON order
    # ([longitude, latitude]) so Leaflet can render it without conversion.
    specs = [
        (
            "ZONE-A",
            "Zone A - Covered Parking",
            [
                [77.6730, 27.4925],
                [77.6755, 27.4925],
                [77.6755, 27.4940],
                [77.6730, 27.4940],
                [77.6730, 27.4925],
            ],
            27.49325,
            77.67425,
            40,
            ZoneStatus.ACTIVE,
        ),
        (
            "ZONE-B",
            "Zone B - Open Yard",
            [
                [77.6760, 27.4925],
                [77.6790, 27.4925],
                [77.6790, 27.4945],
                [77.6760, 27.4945],
                [77.6760, 27.4925],
            ],
            27.49350,
            77.67750,
            60,
            ZoneStatus.ACTIVE,
        ),
        (
            "ZONE-C",
            "Zone C - Long Stay",
            [
                [77.6800, 27.4925],
                [77.6820, 27.4925],
                [77.6820, 27.4940],
                [77.6800, 27.4940],
                [77.6800, 27.4925],
            ],
            27.49325,
            77.68100,
            25,
            ZoneStatus.MAINTENANCE,
        ),
    ]

    zones: dict[str, ParkingZone] = {}
    for code, name, polygon, lat, lon, capacity, status in specs:
        zone = db.session.get(ParkingZone, _demo_id("zone", code))
        if zone is not None:
            zones[code] = zone
            result.skipped.append(f"{code} - already present")
            continue

        zone = ParkingZone(
            id=_demo_id("zone", code),
            code=code,
            name=name,
            description=f"[DEMO] Demonstration zone with a {len(polygon) - 1}-sided geofence.",
            geofence_polygon=polygon,
            centre_latitude=lat,
            centre_longitude=lon,
            radius_metres=None,
            buffer_metres=25.0,
            capacity=capacity,
            current_occupancy=0,
            status=str(status),
        )
        db.session.add(zone)
        zones[code] = zone
        result.created.append(f"{code} ({name})")

    db.session.flush()
    return result, zones


def _seed_vehicles(users: dict[str, User]) -> tuple[SeedResult, dict[str, Vehicle]]:
    result = SeedResult()
    specs = [
        ("demo-vehicle-1", "demo-1", "UP 78 AB 1234", "Tata", "Nexon", 2023, "Pearl White"),
        ("demo-vehicle-2", "demo-1", "UP 78 CD 5678", "Hyundai", "Creta", 2022, "Titan Grey"),
        ("demo-vehicle-3", "demo-2", "DL 01 CA 9012", "Maruti Suzuki", "Baleno", 2024, "Nexa Blue"),
        ("demo-vehicle-4", "demo-6", "UP 78 EF 9012", "Mahindra", "XUV700", 2023, "Napoli Black"),
    ]

    vehicles: dict[str, Vehicle] = {}
    for key, owner_key, plate, make, model, year, color in specs:
        vehicle = db.session.get(Vehicle, _demo_id("vehicle", key))
        if vehicle is not None:
            vehicles[key] = vehicle
            result.skipped.append(f"{plate} - already present")
            continue

        vehicle = Vehicle(
            id=_demo_id("vehicle", key),
            owner_id=users[owner_key].id,
            plate_number=plate,
            normalized_plate=Vehicle.normalize_plate(plate),
            make=make,
            model=model,
            year=year,
            color=color,
            status=str(VehicleStatus.REGISTERED),
            notes="[DEMO] Seeded demonstration vehicle.",
        )
        db.session.add(vehicle)
        vehicles[key] = vehicle
        result.created.append(f"{plate} ({make} {model})")

    db.session.flush()
    return result, vehicles


def _seed_ticket_and_custody(
    *,
    key: str,
    customer: User,
    vehicle: Vehicle,
    zone: ParkingZone | None,
    valet: User,
    status: CustodyStatus,
    closed: bool,
    base_time: datetime,
) -> tuple[ValetTicket, CustodySession, bool]:
    """Create one ticket plus its custody session. Returns (ticket, session, created)."""
    ticket = db.session.get(ValetTicket, _demo_id("ticket", key))
    if ticket is not None:
        session = (
            db.session.query(CustodySession)
            .filter(CustodySession.ticket_id == ticket.id)
            .one_or_none()
        )
        if session is not None:
            return ticket, session, False
    else:
        ticket = ValetTicket(
            id=_demo_id("ticket", key),
            ticket_code=f"VT-{key.upper()[-6:]}",
            customer_id=customer.id,
            vehicle_id=vehicle.id,
            status=str(TicketStatus.COMPLETED if closed else TicketStatus.ACTIVE),
            issued_at=base_time,
            expected_return_at=base_time + timedelta(hours=6),
            actual_drop_off_at=base_time + timedelta(minutes=20),
            actual_pickup_at=base_time + timedelta(hours=5) if closed else None,
            qr_payload=f"VALET:{ticket_code_payload(key)}",
            qr_code_path=f"tickets/demo/{key}.png",
            special_instructions="[DEMO] Please handle with care.",
            metadata_={"is_demo": True},
        )
        db.session.add(ticket)
        db.session.flush()

    session = CustodySession(
        id=_demo_id("custody", key),
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id if zone else None,
        assigned_valet_id=valet.id,
        received_by_id=valet.id,
        status=str(status),
        dropped_off_at=base_time + timedelta(minutes=20),
        zone_assigned_at=base_time + timedelta(minutes=26),
        retrieval_requested_at=base_time + timedelta(hours=4, minutes=30),
        retrieval_started_at=base_time + timedelta(hours=4, minutes=45),
        ready_for_pickup_at=base_time + timedelta(hours=4, minutes=55),
        picked_up_at=base_time + timedelta(hours=5) if closed else None,
        expected_retrieval_minutes=15,
        handover_point="Main Lobby - Bay 1",
        odometer_at_drop_off=24_310,
        fuel_level_percent=62,
        pre_existing_damage_notes="[DEMO] None reported at drop-off.",
        condition_accepted=True if closed else None,
        mileage_driven=0 if closed else None,
    )
    db.session.add(session)
    db.session.flush()
    return ticket, session, True


def ticket_code_payload(key: str) -> str:
    """The string encoded into a demo QR code (not the image)."""
    return f"VT-{key.upper()[-6:]}"


def _seed_telemetry(
    session: CustodySession, zone: ParkingZone
) -> tuple[SeedResult, list[GpsEvent]]:
    result = SeedResult()
    existing = (
        db.session.query(GpsEvent)
        .filter(GpsEvent.custody_session_id == session.id)
        .count()
    )
    if existing:
        result.skipped.append(f"{existing} GPS fix(es) already present for this custody")
        return result, []

    fixes: list[GpsEvent] = []
    base = session.dropped_off_at
    centre_lon = float(zone.centre_longitude)
    centre_lat = float(zone.centre_latitude)

    # A valet drives the car in from the edge of the yard to a bay near the
    # centre, leaves it parked, and then -- with no retrieval requested --
    # someone drives it out. The last two fixes are the pair the geofence
    # module is meant to flag.
    #
    # ``movement_expected`` is per fix, not per session: the first four are a
    # legitimate parking manoeuvre, so movement during them is authorised even
    # though no retrieval is in progress. Marking the whole track as
    # unauthorised movement flagged the arrival itself as an anomaly, which is
    # the kind of false positive that teaches people to ignore a dashboard.
    #
    # The offsets are chosen against ZONE-A's real boundary: the vehicle enters
    # just inside it and moves inward, so the outward-drift rule stays quiet
    # during the arrival and only the departure trips it.
    track = [
        # offset_minutes, dlon, dlat, speed_kmph, movement_expected
        (0, 0.00120, 0.00060, 12.0, True),
        (2, 0.00080, 0.00040, 9.0, True),
        (4, 0.00040, 0.00020, 5.0, True),
        (6, 0.00000, 0.00000, 0.0, True),
        (10, 0.00000, 0.00000, 0.0, False),
        (14, 0.00000, 0.00000, 0.0, False),
        (18, 0.00400, 0.00250, 34.0, False),
        (22, 0.00520, 0.00310, 41.0, False),
    ]

    priors: list[geofence_engine.PriorFix] = []
    for offset_minutes, dlon, dlat, speed, movement_expected in track:
        recorded_at = base + timedelta(minutes=offset_minutes)
        latitude = centre_lat + dlat
        longitude = centre_lon + dlon
        # The verdict is computed by the same engine the API uses, on the same
        # coordinates. Seeding a hand-written status alongside a hand-written
        # distance would be demo data that could silently disagree with what
        # the real code produces for the same input -- and a demo whose numbers
        # cannot be reproduced is not a demo of anything.
        verdict = geofence_engine.evaluate_fix(
            latitude=latitude,
            longitude=longitude,
            zone_ring=zone.geofence_polygon,
            zone_centre=(centre_lat, centre_lon),
            zone_radius_metres=None,
            speed_kmph=speed,
            heading_degrees=85.0,
            recorded_at=recorded_at,
            prior_fixes=tuple(priors),
            movement_expected=movement_expected,
        )
        priors.append(
            geofence_engine.PriorFix(latitude, longitude, recorded_at, speed)
        )
        priors = priors[-4:]

        event = GpsEvent(
            id=_demo_id("gps", f"{session.id}:{offset_minutes}"),
            custody_session_id=session.id,
            recorded_at=recorded_at,
            latitude=latitude,
            longitude=longitude,
            speed_kmph=speed,
            heading_degrees=85.0,
            accuracy_metres=4.5,
            # Honest provenance: this is generated data, not a receiver.
            source=str(TelemetrySource.SIMULATED),
            device_id="SIM-TELEMETRY-001",
            zone_id=zone.id,
            is_within_zone=verdict.is_within_zone,
            distance_from_zone_metres=verdict.distance_from_boundary_metres,
            movement_status=verdict.status,
        )
        db.session.add(event)
        fixes.append(event)

    db.session.flush()
    result.created.append(f"{len(fixes)} simulated GPS fix(es)")
    return result, fixes


def _seed_scenario_track(
    session: CustodySession,
    zone: ParkingZone,
    scenario: str,
    *,
    key_prefix: str,
) -> tuple[SeedResult, list[GpsEvent]]:
    """Seed a generated track for one custody session.

    The fixes come from :mod:`ai.synthetic.gps_tracks` and each verdict comes
    from the same :func:`geofence.evaluate_fix` the API uses, against the
    zone's real polygon. Nothing about the outcome is asserted in advance: the
    seeded ``movement_status`` is whatever the engine concludes, so the demo
    cannot drift away from the behaviour it is meant to demonstrate.
    """
    result = SeedResult()
    existing = (
        db.session.query(GpsEvent)
        .filter(GpsEvent.custody_session_id == session.id)
        .count()
    )
    if existing:
        result.skipped.append(f"{existing} GPS fix(es) already present for this custody")
        return result, []

    centre_lat = float(zone.centre_latitude)
    centre_lon = float(zone.centre_longitude)
    # The generator needs a radius to scale its manoeuvres, derived from the
    # zone's own footprint above. The radius only shapes the track; the verdict
    # is still computed against the real ring, so it can never decide a status.
    track = generate_track(
        scenario,
        centre_latitude=centre_lat,
        centre_longitude=centre_lon,
        radius_metres=zone_radius_metres(zone),
        start_time=session.dropped_off_at,
        seed=f"{key_prefix}:{session.id}",
    )

    fixes: list[GpsEvent] = []
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

        event = GpsEvent(
            id=_demo_id("gps", f"{key_prefix}:{session.id}:{index}"),
            custody_session_id=session.id,
            recorded_at=fix.recorded_at,
            latitude=fix.latitude,
            longitude=fix.longitude,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            accuracy_metres=fix.accuracy_metres,
            # Honest provenance: generated data, never a receiver.
            source=str(TelemetrySource.SIMULATED),
            device_id=f"SIM-TELEMETRY-{scenario.upper()}",
            zone_id=zone.id,
            is_within_zone=verdict.is_within_zone,
            distance_from_zone_metres=verdict.distance_from_boundary_metres,
            movement_status=verdict.status,
        )
        db.session.add(event)
        fixes.append(event)

    db.session.flush()
    statuses = {fix.movement_status for fix in fixes}
    result.created.append(
        f"{len(fixes)} simulated GPS fix(es) for the '{scenario}' scenario "
        f"({', '.join(sorted(statuses))})"
    )
    return result, fixes


def _seed_inspections(
    session: CustodySession, valet: User, *, with_damage: bool
) -> tuple[SeedResult, list[VehicleInspection]]:
    result = SeedResult()
    existing = (
        db.session.query(VehicleInspection)
        .filter(VehicleInspection.custody_session_id == session.id)
        .count()
    )
    if existing:
        result.skipped.append(f"{existing} inspection(s) already present for this custody")
        return result, []

    inspections: list[VehicleInspection] = []
    labels = ["front_left", "front_right", "rear_left", "rear_right", "roof"]

    for stage, damage_count, outcome in (
        (InspectionStage.DROP_OFF, 1 if with_damage else 0, InspectionOutcome.REVIEWED),
        (InspectionStage.PICKUP, 2 if with_damage else 0, InspectionOutcome.DAMAGE_FOUND
         if with_damage else InspectionOutcome.CLEAN),
    ):
        photos = [_demo_photo(f"{session.id}-{stage.value}-{label}") for label in labels]
        inspection = VehicleInspection(
            id=_demo_id("inspection", f"{session.id}:{stage.value}"),
            custody_session_id=session.id,
            stage=str(stage),
            outcome=str(outcome),
            inspected_by_id=valet.id,
            reviewed_by_id=valet.id,
            captured_at=session.dropped_off_at
            + (timedelta(hours=5) if stage is InspectionStage.PICKUP else timedelta()),
            reviewed_at=session.dropped_off_at
            + (timedelta(hours=5, minutes=10) if stage is InspectionStage.PICKUP
               else timedelta(minutes=10)),
            location="Inspection Bay 2",
            photo_refs=photos,
            photo_count=len(photos),
            damage_count=damage_count,
            overall_condition_score=88.0 if stage is InspectionStage.DROP_OFF else 74.0,
            odometer_reading=24_310,
            inspector_notes="[DEMO] Condition recorded by the seeded demonstration dataset.",
            is_new_damage=stage is InspectionStage.PICKUP and with_damage,
        )
        db.session.add(inspection)
        inspections.append(inspection)

    db.session.flush()
    result.created.append(f"{len(inspections)} inspection(s) (drop-off + pickup)")
    return result, inspections


def _seed_detections(inspection: VehicleInspection, *, stage: InspectionStage) -> SeedResult:
    result = SeedResult()
    existing = (
        db.session.query(DamageDetection)
        .filter(DamageDetection.inspection_id == inspection.id)
        .count()
    )
    if existing:
        result.skipped.append(f"{existing} detection(s) already present for this inspection")
        return result

    if stage is InspectionStage.DROP_OFF:
        findings = [
            (DamageType.SCRATCH, VehicleRegion.LEFT_FRONT_DOOR, DamageSeverity.COSMETIC,
             0.82, True, "Hairline scratch, ~9 cm, visible under raking light."),
        ]
    else:
        findings = [
            (DamageType.SCRATCH, VehicleRegion.LEFT_FRONT_DOOR, DamageSeverity.COSMETIC,
             0.82, True, "Same hairline scratch recorded at drop-off; unchanged."),
            (DamageType.DENT, VehicleRegion.REAR_BUMPER, DamageSeverity.MODERATE,
             0.91, False,
             "Depression ~6 cm across on the rear bumper. Not present in the "
             "drop-off set; LAB colour difference and edge displacement both "
             "exceed the configured thresholds."),
        ]

    for index, (kind, region, severity, confidence, pre_existing, reasoning) in enumerate(findings):
        detection = DamageDetection(
            id=_demo_id("detection", f"{inspection.id}:{index}"),
            inspection_id=inspection.id,
            damage_type=str(kind),
            region=str(region),
            severity=str(severity),
            confidence=confidence,
            is_pre_existing=pre_existing,
            requires_customer_attention=not pre_existing,
            bounding_box={"x": 0.31, "y": 0.58, "width": 0.14, "height": 0.09},
            detection_reasoning=reasoning,
            # Honest method attribution: classical OpenCV, not a trained model.
            method=str(DetectionMethod.CLASSICAL_CV),
            model_version=None,
            algorithm_params={
                "lab_delta_threshold": 18.0,
                "canny_low": 60,
                "canny_high": 150,
                "min_contour_area_px": 420,
            },
            source_image_path=f"inspections/demo/{inspection.id}-{stage.value}-rear_right.jpg",
            evidence_image_path=f"inspections/demo/{inspection.id}-evidence-{index}.jpg",
            comparison_image_path=f"inspections/demo/{inspection.id}-comparison-{index}.jpg",
            pixel_area=1_840,
        )
        db.session.add(detection)

    db.session.flush()
    result.created.append(f"{len(findings)} damage detection(s) for {stage.value}")
    return result


def _seed_alert(
    session: CustodySession,
    vehicle: Vehicle,
    zone: ParkingZone,
    event: GpsEvent | None,
    *,
    acknowledged_by: User,
) -> SeedResult:
    result = SeedResult()
    alert = db.session.get(SecurityAlert, _demo_id("alert", str(session.id)))
    if alert is not None:
        result.skipped.append("Security alert already present for this custody")
        return result

    occurred_at = (
        event.recorded_at if event else session.dropped_off_at + timedelta(minutes=150)
    )
    alert = SecurityAlert(
        id=_demo_id("alert", str(session.id)),
        custody_session_id=session.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        gps_event_id=event.id if event else None,
        alert_type=str(AlertType.GEOFENCE_BREACH),
        severity=str(AlertSeverity.HIGH),
        status=str(AlertStatus.ACKNOWLEDGED),
        title=f"Vehicle left {zone.code} with no task in progress",
        description=(
            "[DEMO] Two consecutive fixes fell outside the assigned zone while "
            "the custody session had no retrieval in progress."
        ),
        # Derived from the fix the alert points at rather than restated by
        # hand. A demo alert whose distance disagrees with the row it cites is
        # worse than no alert: it teaches a reviewer to distrust the numbers.
        reason=(
            f"Fix at {float(event.latitude):.5f}, {float(event.longitude):.5f} is "
            f"{float(event.distance_from_zone_metres):.1f} m beyond the {zone.name} "
            f"boundary, exceeding the {float(zone.buffer_metres):.0f} m buffer, "
            f"while custody status was {session.status}."
            if event is not None
            else f"No fix was recorded for this custody session, so the vehicle "
            f"left {zone.name} without telemetry."
        ),
        confidence=0.94,
        evidence={
            "zone_code": zone.code,
            "buffer_metres": float(zone.buffer_metres),
            "distance_metres": (
                float(event.distance_from_zone_metres)
                if event is not None and event.distance_from_zone_metres is not None
                else None
            ),
            "speed_kmph": (
                float(event.speed_kmph)
                if event is not None and event.speed_kmph is not None
                else None
            ),
            "movement_status": event.movement_status if event is not None else None,
            "custody_status_at_fix": session.status,
            "telemetry_source": TelemetrySource.SIMULATED.value,
        },
        evidence_image_path=f"alerts/demo/{session.id}-breach.png",
        occurred_at=occurred_at,
        # The status claims ACKNOWLEDGED, so the acknowledgement must actually
        # be attributed and timestamped - the CHECK constraints require it.
        acknowledged_by_id=acknowledged_by.id,
        acknowledged_at=occurred_at + timedelta(minutes=6),
    )
    db.session.add(alert)
    db.session.flush()
    result.created.append("1 geofence-breach alert (HIGH, acknowledged)")
    return result


def _seed_prediction(session: CustodySession, customer: User, valet: User) -> SeedResult:
    result = SeedResult()
    prediction = db.session.get(RetrievalPrediction, _demo_id("prediction", str(session.id)))
    if prediction is not None:
        result.skipped.append("Retrieval prediction already present for this custody")
        return result

    requested_at = session.retrieval_requested_at or session.dropped_off_at
    prediction = RetrievalPrediction(
        id=_demo_id("prediction", str(session.id)),
        custody_session_id=session.id,
        requested_by_id=customer.id,
        assigned_valet_id=valet.id,
        status=str(PredictionStatus.DISPATCHED),
        requested_at=requested_at,
        predicted_ready_at=requested_at + timedelta(minutes=14),
        actual_ready_at=requested_at + timedelta(minutes=25),
        dispatched_at=requested_at + timedelta(minutes=15),
        dispatch_after=requested_at + timedelta(minutes=6),
        predicted_wait_minutes=14,
        min_wait_minutes=10,
        max_wait_minutes=22,
        confidence=0.71,
        queue_position=2,
        # Honest attribution: a heuristic over synthetic history, not a model
        # trained on real operations.
        method="heuristic-queue-v1",
        model_version=None,
        inputs_snapshot={
            "queue_length": 3,
            "zone": "ZONE-A",
            "avg_retrieval_minutes_zone": 11.4,
            "hour_of_day": 14,
            "history_is_synthetic": True,
        },
        explanation=(
            "Estimated from the mean retrieval time for Zone A (11.4 min over "
            "synthetic history), adjusted for a queue of 3 and the current "
            "hour. The 10-22 minute range reflects the observed spread. This "
            "estimate is derived from demonstration data, not from real "
            "operational history."
        ),
    )
    db.session.add(prediction)
    db.session.flush()
    result.created.append("1 retrieval prediction (14 min, confidence 0.71)")
    return result


def seed_demo_data() -> SeedResult:
    """Create the demonstration dataset. Idempotent: safe to run repeatedly."""
    result = SeedResult()
    now = datetime.now(UTC).replace(microsecond=0)

    user_result, users = _seed_users()
    result.merge(user_result)

    zone_result, zones = _seed_zones()
    result.merge(zone_result)

    vehicle_result, vehicles = _seed_vehicles(users)
    result.merge(vehicle_result)

    # An open custody session: currently in the platform's care.
    ticket_open, session_open, open_created = _seed_ticket_and_custody(
        key="demo-open",
        customer=users["demo-1"],
        vehicle=vehicles["demo-vehicle-1"],
        zone=zones["ZONE-A"],
        valet=users["demo-3"],
        status=CustodyStatus.PARKED,
        closed=False,
        base_time=now - timedelta(hours=2),
    )
    if open_created:
        result.created.append(f"ticket {ticket_open.ticket_code} + open custody session (PARKED)")
    else:
        result.skipped.append(f"ticket {ticket_open.ticket_code} - already present")

    # A closed custody session with new damage found at pickup.
    ticket_closed, session_closed, closed_created = _seed_ticket_and_custody(
        key="demo-closed",
        customer=users["demo-2"],
        vehicle=vehicles["demo-vehicle-3"],
        zone=zones["ZONE-B"],
        valet=users["demo-4"],
        status=CustodyStatus.CHECKED_OUT,
        closed=True,
        base_time=now - timedelta(days=2),
    )
    if closed_created:
        result.created.append(
            f"ticket {ticket_closed.ticket_code} + closed custody session (CHECKED_OUT)"
        )
    else:
        result.skipped.append(f"ticket {ticket_closed.ticket_code} - already present")

    # A second open custody session, further along: retrieval has been
    # requested and the vehicle is on its way back to the handover point.
    # Without it the third customer's dashboard would be empty and the
    # "ready for pickup" state would never appear in the demo.
    ticket_ready, session_ready, ready_created = _seed_ticket_and_custody(
        key="demo-ready",
        customer=users["demo-6"],
        vehicle=vehicles["demo-vehicle-4"],
        zone=zones["ZONE-B"],
        valet=users["demo-3"],
        status=CustodyStatus.READY_FOR_PICKUP,
        closed=False,
        base_time=now - timedelta(hours=5),
    )
    if ready_created:
        result.created.append(
            f"ticket {ticket_ready.ticket_code} + custody session (READY_FOR_PICKUP)"
        )
    else:
        result.skipped.append(f"ticket {ticket_ready.ticket_code} - already present")

    # Keep vehicles.status consistent with their custody state.
    vehicles["demo-vehicle-1"].status = str(VehicleStatus.IN_CUSTODY)
    vehicles["demo-vehicle-3"].status = str(VehicleStatus.FLAGGED)
    vehicles["demo-vehicle-4"].status = str(VehicleStatus.IN_CUSTODY)
    zones["ZONE-A"].current_occupancy = 1
    zones["ZONE-B"].current_occupancy = 1

    telemetry_result, fixes = _seed_telemetry(session_open, zones["ZONE-A"])
    result.merge(telemetry_result)

    # Normal-movement counterparts. The open session above carries the one
    # suspicious track; these two are ordinary operations, so the dashboard
    # shows the routine case as well as the exception. Both come from the same
    # simulator the /gps/simulate endpoint exposes, and each verdict is the
    # real engine's, so nothing here is a hand-written status.
    #
    # 'parked' and 'jitter' were chosen over 'arrival'/'retrieval' after
    # measuring them: those two legitimately drive the vehicle out of the
    # yard, and the engine correctly reports BREACH for fixes 300 m outside
    # the ring. Seeding that against a routine custody would put a red breach
    # on an ordinary job, which is exactly the false positive that teaches
    # people to ignore a dashboard.
    closed_track_result, _ = _seed_scenario_track(
        session_closed, zones["ZONE-B"], "parked", key_prefix="closed"
    )
    result.merge(closed_track_result)

    ready_track_result, _ = _seed_scenario_track(
        session_ready, zones["ZONE-B"], "jitter", key_prefix="ready"
    )
    result.merge(ready_track_result)

    open_inspections_result, open_inspections = _seed_inspections(
        session_open, users["demo-3"], with_damage=False
    )
    result.merge(open_inspections_result)

    closed_inspections_result, closed_inspections = _seed_inspections(
        session_closed, users["demo-4"], with_damage=True
    )
    result.merge(closed_inspections_result)

    ready_inspections_result, ready_inspections = _seed_inspections(
        session_ready, users["demo-3"], with_damage=False
    )
    result.merge(ready_inspections_result)

    for inspection in open_inspections + closed_inspections + ready_inspections:
        stage = InspectionStage(inspection.stage)
        result.merge(_seed_detections(inspection, stage=stage))

    breaching_fix = next((fix for fix in fixes if not fix.is_within_zone), None)
    result.merge(
        _seed_alert(
            session_open,
            vehicles["demo-vehicle-1"],
            zones["ZONE-A"],
            breaching_fix,
            acknowledged_by=users["demo-5"],
        )
    )
    result.merge(_seed_prediction(session_open, users["demo-1"], users["demo-3"]))
    result.merge(_seed_prediction(session_ready, users["demo-6"], users["demo-3"]))

    # Audit entries are written once, guarded on the same custody sessions the
    # rest of the demo data hangs off, so a repeated run appends nothing.
    already_logged = (
        db.session.query(AuditLog)
        .filter(
            AuditLog.custody_session_id.in_(
                [session_open.id, session_closed.id, session_ready.id]
            )
        )
        .count()
    )
    if already_logged:
        result.skipped.append(f"{already_logged} audit entr(ies) already present")
    else:
        _log(
            action=AuditAction.VEHICLE_CHECKED_IN,
            entity_type="custody_sessions",
            entity_id=session_open.id,
            custody_session_id=session_open.id,
            description="[DEMO] Vehicle dropped off and custody opened by the seeder.",
            actor_id=users["demo-3"].id,
            actor_role=str(Role.VALET_OPERATOR),
            actor_email=users["demo-3"].email,
            new_values={"status": CustodyStatus.PARKED.value, "zone": zones["ZONE-A"].code},
        )
        _log(
            action=AuditAction.DAMAGE_DETECTED,
            entity_type="damage_detections",
            entity_id=None,
            custody_session_id=session_closed.id,
            description="[DEMO] New rear-bumper dent found at pickup; absent at drop-off.",
            actor_id=users["demo-4"].id,
            actor_role=str(Role.VALET_OPERATOR),
            actor_email=users["demo-4"].email,
            new_values={
                "damage_type": DamageType.DENT.value,
                "severity": DamageSeverity.MODERATE.value,
            },
        )
        _log(
            action=AuditAction.SECURITY_ALERT_CREATED,
            entity_type="security_alerts",
            entity_id=_demo_id("alert", str(session_open.id)),
            custody_session_id=session_open.id,
            description="[DEMO] Geofence breach raised from simulated telemetry.",
            actor_id=None,
            actor_role=None,
            actor_email=None,
            new_values={
                "alert_type": AlertType.GEOFENCE_BREACH.value,
                "severity": AlertSeverity.HIGH.value,
            },
        )
        result.created.append("3 audit log entries for the demo custody history")

    db.session.flush()
    return result


def run(app) -> SeedResult:
    """Run every seeding step in order. Safe to execute repeatedly.

    Pushes its own application context so CLI scripts, the test suite and the
    API can all call it identically. A nested context is harmless, so callers
    that already have one need not care.
    """
    with app.app_context():
        result = bootstrap_admin(app)
        result.merge(seed_demo_data())
        db.session.commit()
    return result


__all__ = [
    "DEMO_NAMESPACE",
    "DEMO_PASSWORD",
    "SeedResult",
    "bootstrap_admin",
    "seed_demo_data",
    "run",
]
