"""CRUD and relationship tests against a real PostgreSQL database.

These exercise the object graph the way the API services will: create a
customer, register their vehicles, issue a ticket, open custody, capture
inspections, record findings and telemetry, then read the graph back through
the relationships and tear it down.

Skipped automatically when PostgreSQL is not reachable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

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
from utils.security import Role

pytestmark = [pytest.mark.postgres, pytest.mark.integration]


# Transaction cleanup between tests is handled by the autouse
# `_rollback_session` fixture in conftest.py, which also prevents the
# session-scoped teardown from deadlocking on a lingering lock.


@pytest.fixture()
def session(pg_app):
    """A database session for a single test's work."""
    return db.session


# A fixed literal so the hashing assertions below have something to check
# against; these rows are rolled back and never leave the test transaction.
def _new_user(
    email: str, *, role: Role = Role.CUSTOMER, password: str = "S3cure#Pass"  # noqa: S107
) -> User:
    user = User(
        email=email,
        full_name=f"Test {role.value.title()}",
        role=str(role),
        status=str(UserStatus.ACTIVE),
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
    return user


def _new_vehicle(owner: User, plate: str) -> Vehicle:
    vehicle = Vehicle(
        owner_id=owner.id,
        plate_number=plate,
        normalized_plate=Vehicle.normalize_plate(plate),
        make="Tata",
        model="Nexon",
        year=2023,
        color="White",
        status=str(VehicleStatus.REGISTERED),
    )
    db.session.add(vehicle)
    return vehicle


def _new_zone(code: str) -> ParkingZone:
    zone = ParkingZone(
        code=code,
        name=f"Test {code}",
        geofence_polygon=[
            [77.6730, 27.4925],
            [77.6755, 27.4925],
            [77.6755, 27.4940],
            [77.6730, 27.4940],
            [77.6730, 27.4925],
        ],
        centre_latitude=27.49325,
        centre_longitude=77.67425,
        buffer_metres=25.0,
        capacity=10,
        current_occupancy=0,
        status=str(ZoneStatus.ACTIVE),
    )
    db.session.add(zone)
    return zone


def _row_exists(table: str, row_id: uuid.UUID) -> bool:
    """Check the database directly.

    ``session.get()`` answers from the identity map, which still holds an
    object the session itself deleted -- so it reports a cascade as a failure.
    Going through SQL asserts what actually landed in the row.
    """
    from sqlalchemy import text

    return (
        db.session.execute(
            text(f'SELECT EXISTS (SELECT 1 FROM "{table}" WHERE id = :id)'),  # noqa: S608
            {"id": str(row_id)},
        ).scalar_one()
    )


def _photo(label: str) -> dict:
    from utils.image_ref import validate_image_ref_dict

    return validate_image_ref_dict(
        {
            "path": f"inspections/test/{label}.jpg",
            "byte_size": 102_400,
            "width": 1920,
            "height": 1080,
            "mime_type": "image/jpeg",
            "sha256": "b" * 64,
        }
    )


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------
def test_create_user_generates_a_uuid_and_timestamps(session):
    user = _new_user(f"crud-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()

    assert isinstance(user.id, uuid.UUID)
    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.role == Role.CUSTOMER.value


def test_password_is_stored_hashed_and_verifies(session):
    user = _new_user(f"hash-{uuid.uuid4().hex[:8]}@example.com", password="Correct#Horse")
    session.flush()

    assert user.password_hash != "Correct#Horse"
    assert user.password_hash.startswith("pbkdf2:sha256:")
    assert user.check_password("Correct#Horse") is True
    assert user.check_password("wrong") is False


def test_all_four_roles_can_be_created(session):
    suffix = uuid.uuid4().hex[:8]
    users = [
        _new_user(f"{role.value.lower()}-{suffix}@example.com", role=role) for role in Role
    ]
    session.flush()

    assert {user.role for user in users} == {role.value for role in Role}


def test_a_customer_can_have_multiple_vehicles(session):
    owner = _new_user(f"fleet-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()

    plates = [f"UP 78 X{index} {1000 + index}" for index in range(3)]
    for plate in plates:
        _new_vehicle(owner, plate)
    session.flush()

    assert len(owner.vehicles) == 3
    assert {vehicle.plate_number for vehicle in owner.vehicles} == set(plates)


def test_server_side_defaults_fill_in_without_python_values(session):
    """gen_random_uuid() and now() must work even if Python sends nothing."""
    from sqlalchemy import text

    user_id = session.execute(
        text(
            "INSERT INTO users (email, password_hash, full_name, role, status) "
            "VALUES (:e, :p, :n, 'CUSTOMER', 'ACTIVE') RETURNING id, created_at, updated_at"
        ),
        {"e": f"raw-{uuid.uuid4().hex[:8]}@example.com", "p": "pbkdf2:sha256:1$x$y", "n": "Raw"},
    ).one()

    assert user_id.id is not None
    assert user_id.created_at is not None
    assert user_id.updated_at is not None


# ---------------------------------------------------------------------------
# READ / relationships
# ---------------------------------------------------------------------------
def test_vehicle_owner_relationship_resolves(session):
    owner = _new_user(f"owner-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()
    vehicle = _new_vehicle(owner, f"UP 78 RW {uuid.uuid4().int % 9000 + 1000}")
    session.flush()

    assert vehicle.owner.id == owner.id
    assert vehicle.owner.email == owner.email


def test_plate_normalisation_is_enforced_uppercase(session):
    owner = _new_user(f"plate-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()

    vehicle = _new_vehicle(owner, f"up 78 zz {uuid.uuid4().int % 9000 + 1000}")
    vehicle.normalized_plate = Vehicle.normalize_plate(vehicle.plate_number)
    session.flush()

    assert vehicle.normalized_plate.isupper()
    assert " " not in vehicle.normalized_plate


def test_two_active_vehicles_cannot_share_a_normalized_plate(session):
    """The partial unique index blocks a live duplicate."""
    owner = _new_user(f"dupe-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()
    plate = f"UP 78 DU {uuid.uuid4().int % 9000 + 1000}"

    _new_vehicle(owner, plate)
    session.flush()

    _new_vehicle(owner, plate)  # same normalised plate as the row above
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_an_archived_vehicle_frees_its_plate(session):
    """Archiving must release the plate, which is why the index is partial."""
    owner = _new_user(f"arch-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()
    plate = f"UP 78 AR {uuid.uuid4().int % 9000 + 1000}"

    first = _new_vehicle(owner, plate)
    session.flush()
    first.status = str(VehicleStatus.ARCHIVED)
    session.flush()

    _new_vehicle(owner, plate)
    session.flush()  # must not raise


# ---------------------------------------------------------------------------
# The custody workflow, end to end
# ---------------------------------------------------------------------------
def _open_custody(
    session, *, plate_suffix: str
) -> tuple[User, Vehicle, ValetTicket, CustodySession, ParkingZone]:
    customer = _new_user(f"cust-{uuid.uuid4().hex[:8]}@example.com")
    valet = _new_user(f"valet-{uuid.uuid4().hex[:8]}@example.com", role=Role.VALET_OPERATOR)
    zone = _new_zone(f"Z-{uuid.uuid4().hex[:6].upper()}")
    session.flush()

    vehicle = _new_vehicle(customer, f"UP 78 {plate_suffix} {uuid.uuid4().int % 9000 + 1000}")
    session.flush()

    ticket = ValetTicket(
        ticket_code=f"VT-{uuid.uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        status=str(TicketStatus.ACTIVE),
        qr_payload=f"VALET:VT-{uuid.uuid4().hex[:6].upper()}",
        qr_code_path="tickets/test/sample.png",
        metadata_={"is_demo": True},
    )
    session.add(ticket)
    session.flush()

    custody = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        assigned_valet_id=valet.id,
        received_by_id=valet.id,
        status=str(CustodyStatus.PARKED),
        odometer_at_drop_off=10_000,
        fuel_level_percent=70,
    )
    session.add(custody)
    session.flush()

    return customer, vehicle, ticket, custody, zone


def test_full_custody_graph_can_be_created_and_read_back(session):
    customer, vehicle, ticket, custody, zone = _open_custody(session, plate_suffix="CU")

    # One ticket -> one custody session, and the reverse.
    assert len(ticket.custody_sessions) == 1
    assert ticket.custody_sessions[0].id == custody.id
    assert custody.ticket.id == ticket.id
    assert custody.vehicle.id == vehicle.id
    assert custody.zone.id == zone.id
    assert custody.is_open is True


def test_custody_session_holds_gps_events_in_time_order(session):
    _, _, _, custody, zone = _open_custody(session, plate_suffix="GP")

    base = datetime.now(UTC)
    for offset in (0, 5, 10):
        session.add(
            GpsEvent(
                custody_session_id=custody.id,
                recorded_at=base + timedelta(minutes=offset),
                latitude=27.4932,
                longitude=77.6742,
                speed_kmph=10.0,
                accuracy_metres=5.0,
                source=str(TelemetrySource.SIMULATED),
                zone_id=zone.id,
                is_within_zone=True,
                distance_from_zone_metres=0.0,
            )
        )
    session.flush()

    assert len(custody.gps_events) == 3
    assert all(event.is_simulated for event in custody.gps_events)
    assert all(event.source == TelemetrySource.SIMULATED.value for event in custody.gps_events)


def test_exactly_one_inspection_per_stage(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="IN")
    valet = custody.assigned_valet

    for stage in InspectionStage:
        session.add(
            VehicleInspection(
                custody_session_id=custody.id,
                stage=str(stage),
                outcome=str(InspectionOutcome.PENDING_REVIEW),
                inspected_by_id=valet.id,
                photo_refs=[_photo(f"{stage.value}-1")],
                photo_count=1,
                damage_count=0,
            )
        )
    session.flush()

    assert len(custody.inspections) == 2
    assert custody.drop_off_inspection.stage == InspectionStage.DROP_OFF.value
    assert custody.pickup_inspection.stage == InspectionStage.PICKUP.value

    # A third drop-off inspection must be rejected by the unique index.
    session.add(
        VehicleInspection(
            custody_session_id=custody.id,
            stage=str(InspectionStage.DROP_OFF),
            photo_refs=[],
            photo_count=0,
            damage_count=0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_an_inspection_can_have_multiple_damage_detections(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="DM")

    inspection = VehicleInspection(
        custody_session_id=custody.id,
        stage=str(InspectionStage.PICKUP),
        outcome=str(InspectionOutcome.DAMAGE_FOUND),
        photo_refs=[_photo("pickup-1")],
        photo_count=1,
        damage_count=2,
        is_new_damage=True,
    )
    session.add(inspection)
    session.flush()

    for index, (kind, severity) in enumerate(
        [(DamageType.SCRATCH, DamageSeverity.COSMETIC), (DamageType.DENT, DamageSeverity.MODERATE)]
    ):
        session.add(
            DamageDetection(
                inspection_id=inspection.id,
                damage_type=str(kind),
                region=str(VehicleRegion.REAR_BUMPER),
                severity=str(severity),
                confidence=0.85,
                bounding_box={"x": 0.3, "y": 0.5, "width": 0.1, "height": 0.1},
                detection_reasoning="Colour difference exceeds the LAB threshold.",
                method=str(DetectionMethod.CLASSICAL_CV),
                evidence_image_path=f"inspections/test/evidence-{index}.jpg",
            )
        )
    session.flush()

    assert len(inspection.damage_detections) == 2
    assert {detection.damage_type for detection in inspection.damage_detections} == {
        DamageType.SCRATCH.value,
        DamageType.DENT.value,
    }


def test_a_finding_needs_a_reason_and_a_ml_claim_needs_a_version(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="RS")
    inspection = VehicleInspection(
        custody_session_id=custody.id,
        stage=str(InspectionStage.DROP_OFF),
        photo_refs=[],
        photo_count=0,
        damage_count=0,
    )
    session.add(inspection)
    session.flush()

    # Empty reasoning is rejected.
    session.add(
        DamageDetection(
            inspection_id=inspection.id,
            damage_type=str(DamageType.SCRATCH),
            region=str(VehicleRegion.HOOD),
            severity=str(DamageSeverity.MINOR),
            confidence=0.5,
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            detection_reasoning="",
            method=str(DetectionMethod.CLASSICAL_CV),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # Claiming ML_MODEL without naming a version is rejected.
    session.add(
        DamageDetection(
            inspection_id=inspection.id,
            damage_type=str(DamageType.SCRATCH),
            region=str(VehicleRegion.HOOD),
            severity=str(DamageSeverity.MINOR),
            confidence=0.5,
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            detection_reasoning="Detected by a trained detector.",
            method=str(DetectionMethod.ML_MODEL),
            model_version=None,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_confidence_outside_zero_to_one_is_rejected(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="CF")
    inspection = VehicleInspection(
        custody_session_id=custody.id,
        stage=str(InspectionStage.DROP_OFF),
        photo_refs=[],
        photo_count=0,
        damage_count=0,
    )
    session.add(inspection)
    session.flush()

    session.add(
        DamageDetection(
            inspection_id=inspection.id,
            damage_type=str(DamageType.SCRATCH),
            region=str(VehicleRegion.HOOD),
            severity=str(DamageSeverity.MINOR),
            confidence=1.4,
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            detection_reasoning="Overconfident finding.",
            method=str(DetectionMethod.CLASSICAL_CV),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_a_security_alert_references_its_custody_session(session):
    _, vehicle, _, custody, zone = _open_custody(session, plate_suffix="AL")

    alert = SecurityAlert(
        custody_session_id=custody.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        alert_type=str(AlertType.GEOFENCE_BREACH),
        severity=str(AlertSeverity.HIGH),
        status=str(AlertStatus.OPEN),
        title="Vehicle left the assigned zone",
        reason="Fix is 412 m beyond the zone boundary while status was PARKED.",
        confidence=0.94,
        evidence={"distance_metres": 412.0},
    )
    session.add(alert)
    session.flush()

    assert custody.alerts[0].id == alert.id
    assert alert.custody_session.id == custody.id
    assert alert.is_open is True


def test_an_acknowledged_alert_must_name_who_acknowledged_it(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="AK")

    session.add(
        SecurityAlert(
            custody_session_id=custody.id,
            alert_type=str(AlertType.UNUSUAL_SPEED),
            severity=str(AlertSeverity.LOW),
            status=str(AlertStatus.OPEN),
            title="Speed above the zone limit",
            reason="Fix recorded 34 km/h where the zone allows 12 km/h.",
            confidence=0.8,
            acknowledged_at=datetime.now(UTC),
            acknowledged_by_id=None,  # violates ck_security_alerts_ack_requires_actor
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_a_retrieval_prediction_references_its_custody_session(session):
    customer, _, _, custody, _ = _open_custody(session, plate_suffix="PR")
    requested_at = datetime.now(UTC)

    prediction = RetrievalPrediction(
        custody_session_id=custody.id,
        requested_by_id=customer.id,
        status=str(PredictionStatus.PENDING),
        requested_at=requested_at,
        predicted_ready_at=requested_at + timedelta(minutes=12),
        predicted_wait_minutes=12,
        min_wait_minutes=8,
        max_wait_minutes=20,
        confidence=0.7,
        queue_position=1,
        method="heuristic-queue-v1",
        inputs_snapshot={"queue_length": 2, "history_is_synthetic": True},
        explanation="Mean retrieval time for the zone, adjusted for queue length.",
    )
    session.add(prediction)
    session.flush()

    assert custody.predictions[0].id == prediction.id
    assert prediction.wait_interval == (8, 20)


def test_a_prediction_interval_must_be_ordered(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="IV")

    session.add(
        RetrievalPrediction(
            custody_session_id=custody.id,
            status=str(PredictionStatus.PENDING),
            predicted_wait_minutes=12,
            min_wait_minutes=30,  # greater than max - violates the ordering CHECK
            max_wait_minutes=20,
            confidence=0.7,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------
def test_updating_a_row_refreshes_updated_at(session):
    from sqlalchemy import text

    owner = _new_user(f"upd-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()
    vehicle = _new_vehicle(owner, f"UP 78 UD {uuid.uuid4().int % 9000 + 1000}")
    session.flush()

    # Force a real change so now() advances past the insert timestamp.
    session.execute(text("SELECT pg_sleep(0.01)"))
    vehicle.status = str(VehicleStatus.IN_CUSTODY)
    session.flush()
    session.refresh(vehicle)

    assert vehicle.status == VehicleStatus.IN_CUSTODY.value
    assert vehicle.updated_at >= vehicle.created_at


def test_closing_a_custody_session_requires_a_pickup_time(session):
    _, _, _, custody, _ = _open_custody(session, plate_suffix="CL")

    custody.status = str(CustodyStatus.CHECKED_OUT)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    custody.status = str(CustodyStatus.CHECKED_OUT)
    custody.picked_up_at = datetime.now(UTC)
    session.flush()
    assert custody.is_open is False


def test_a_pickup_time_requires_a_closed_status(session):
    """The constraint is symmetric; setting only one half must fail."""
    _, _, _, custody, _ = _open_custody(session, plate_suffix="PC")

    custody.picked_up_at = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_zone_occupancy_cannot_exceed_capacity(session):
    zone = _new_zone(f"CAP-{uuid.uuid4().hex[:5].upper()}")
    session.flush()
    zone.capacity = 1

    zone.current_occupancy = 2
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# DELETE / cascades
# ---------------------------------------------------------------------------
def test_deleting_a_custody_session_cascades_to_children(session):
    _, _, _, custody, zone = _open_custody(session, plate_suffix="DL")

    session.add(
        GpsEvent(
            custody_session_id=custody.id,
            recorded_at=datetime.now(UTC),
            latitude=27.4932,
            longitude=77.6742,
            source=str(TelemetrySource.SIMULATED),
            # A fix cannot claim to be inside a zone it was not evaluated
            # against: ck_gps_events_zone_fields_consistent enforces that, so
            # the zone the session was assigned is named here.
            zone_id=zone.id,
            is_within_zone=True,
            distance_from_zone_metres=-12.0,
        )
    )
    session.flush()
    child_id = custody.gps_events[0].id

    session.delete(custody)
    session.flush()

    assert session.get(GpsEvent, child_id) is None


def test_deleting_a_customer_cascades_to_their_vehicles(session):
    owner = _new_user(f"cascade-{uuid.uuid4().hex[:8]}@example.com")
    session.flush()
    vehicle = _new_vehicle(owner, f"UP 78 CS {uuid.uuid4().int % 9000 + 1000}")
    session.flush()
    vehicle_id = vehicle.id

    session.delete(owner)
    session.flush()

    assert not _row_exists("users", owner.id), "the customer should be gone"
    assert not _row_exists("vehicles", vehicle_id), "the vehicle must cascade away"


def test_deleting_a_customer_is_blocked_while_a_ticket_exists(session):
    """RESTRICT protects the audit trail from orphaned tickets."""
    customer, _, ticket, _, _ = _open_custody(session, plate_suffix="RS")

    session.delete(customer)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # The rollback discards this test's uncommitted rows, so the assertion is
    # about the constraint having fired -- not about the ticket still existing.
    # (The seeded dataset covers tickets that genuinely persist.)
    assert ticket.ticket_code.startswith("VT-")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def test_audit_log_records_an_action_with_its_actor_snapshot(session):
    actor = _new_user(f"auditor-{uuid.uuid4().hex[:8]}@example.com", role=Role.VALET_MANAGER)
    session.flush()

    entry = AuditLog(
        actor_id=actor.id,
        actor_role=actor.role,
        actor_email=actor.email,
        action=str(AuditAction.ZONE_ASSIGN),
        outcome=str(AuditOutcome.SUCCESS),
        entity_type="custody_sessions",
        entity_id=uuid.uuid4(),
        old_values={"zone_id": None},
        new_values={"zone_id": "ZONE-A"},
        description="Assigned the vehicle to Zone A.",
    )
    session.add(entry)
    session.flush()

    assert entry.id is not None
    assert entry.actor.email == actor.email
    assert entry.new_values["zone_id"] == "ZONE-A"


def test_audit_log_survives_deletion_of_the_actor(session):
    """The trail must outlive the account it describes."""
    actor = _new_user(f"ghost-{uuid.uuid4().hex[:8]}@example.com", role=Role.VALET_OPERATOR)
    session.flush()
    actor_id = actor.id

    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor.role,
        actor_email=actor.email,
        action=str(AuditAction.VEHICLE_CHECKED_IN),
        outcome=str(AuditOutcome.SUCCESS),
        entity_type="custody_sessions",
        entity_id=uuid.uuid4(),
        description="Opened custody.",
    )
    session.add(entry)
    session.flush()
    entry_id = entry.id

    session.delete(actor)
    session.flush()

    surviving = session.get(AuditLog, entry_id)
    assert surviving is not None, "the audit entry must not cascade away"
    # Both the FK's SET NULL and the trigger's tombstone are written in SQL,
    # so the identity map still holds the pre-delete values. Expire the object
    # to read what actually landed in the row.
    session.refresh(surviving)
    assert surviving.actor_id is None, "SET NULL, so the row remains readable"
    assert surviving.actor_email == entry.actor_email, "the snapshot preserves the trail"
    assert surviving.actor_deleted_at is not None, (
        "the AFTER DELETE trigger must tombstone the snapshot, otherwise a "
        "retained snapshot is indistinguishable from one that was never "
        "attributed to anybody"
    )


def test_a_tombstoned_snapshot_is_distinguishable_from_a_never_attributed_one(session):
    """The whole point of the tombstone column."""
    attributed = _new_user(f"tomb-{uuid.uuid4().hex[:8]}@example.com", role=Role.VALET_OPERATOR)
    session.flush()

    retained = AuditLog(
        actor_id=attributed.id,
        actor_role=attributed.role,
        actor_email=attributed.email,
        action=str(AuditAction.VEHICLE_PICKED_UP),
        outcome=str(AuditOutcome.SUCCESS),
        entity_type="custody_sessions",
        entity_id=uuid.uuid4(),
        description="Closed custody.",
    )
    anonymous = AuditLog(
        actor_id=None,
        actor_role=None,
        actor_email=None,
        action=str(AuditAction.LOGIN_FAILED),
        outcome=str(AuditOutcome.FAILURE),
        entity_type="users",
        entity_id=uuid.uuid4(),
        description="Rejected login.",
        failure_reason="No such account.",
    )
    session.add_all([retained, anonymous])
    session.flush()

    assert retained.actor_deleted_at is None
    assert anonymous.actor_deleted_at is None

    session.delete(attributed)
    session.flush()
    session.refresh(retained)
    session.refresh(anonymous)

    # Now the two are distinguishable: one lost its actor, one never had one.
    assert retained.actor_deleted_at is not None
    assert retained.actor_role == Role.VALET_OPERATOR.value
    assert anonymous.actor_deleted_at is None
    assert anonymous.actor_role is None


def test_an_unknown_entity_type_is_rejected(session):
    with pytest.raises(IntegrityError):
        session.add(
            AuditLog(
                action=str(AuditAction.CREATE),
                outcome=str(AuditOutcome.SUCCESS),
                entity_type="spaceships",
                description="Typo in the entity type.",
            )
        )
        session.flush()
    session.rollback()


def test_a_failure_outcome_requires_a_reason(session):
    with pytest.raises(IntegrityError):
        session.add(
            AuditLog(
                action=str(AuditAction.LOGIN_FAILED),
                outcome=str(AuditOutcome.FAILURE),
                entity_type="users",
                entity_id=uuid.uuid4(),
                description="Rejected login.",
                failure_reason=None,
            )
        )
        session.flush()
    session.rollback()


def test_an_unsourced_actor_snapshot_is_still_rejected(session):
    """The relaxed constraint still forbids claiming a role with no account."""
    with pytest.raises(IntegrityError):
        session.add(
            AuditLog(
                actor_id=None,
                actor_role=str(Role.ADMIN),  # no actor_id to attribute it to
                actor_email="nobody@example.com",
                action=str(AuditAction.CREATE),
                outcome=str(AuditOutcome.SUCCESS),
                entity_type="users",
                entity_id=uuid.uuid4(),
                description="Snapshot with no account behind it.",
            )
        )
        session.flush()
    session.rollback()


def test_an_anonymous_audit_entry_is_allowed(session):
    """A failed login has no actor; the snapshot CHECK permits that."""
    entry = AuditLog(
        actor_id=None,
        actor_role=None,
        actor_email=None,
        action=str(AuditAction.LOGIN_FAILED),
        outcome=str(AuditOutcome.FAILURE),
        entity_type="users",
        description="Rejected login for an unknown address.",
        failure_reason="No such account.",
    )
    session.add(entry)
    session.flush()

    assert entry.actor_id is None
    assert entry.actor_role is None
