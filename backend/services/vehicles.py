"""Vehicle management: the customer's fleet.

A customer registers the vehicles they may hand over. Every read and write is
scoped by role: a customer sees only their own vehicles, while valet staff can
see any vehicle because check-in starts by selecting a customer and then one of
their vehicles.

Three decisions worth stating:

* **Plates are stored twice.** ``plate_number`` keeps what the customer typed;
  ``normalized_plate`` is the uppercase, separator-free form used for lookups
  and uniqueness. That is why ``UP 78 AB 1234`` and ``up78ab1234`` cannot both
  be registered.
* **The duplicate check is the database's, not ours.** A partial unique index
  covers non-archived rows, so a plate freed by archiving can be re-registered.
  Application code pre-checks for a friendly message and still converts the
  ``IntegrityError`` into a 409, because two concurrent requests can both pass
  the pre-check.
* **Deleting archives.** A vehicle with custody history must not disappear from
  the audit trail, so removal sets ``ARCHIVED`` instead of issuing a DELETE.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from models.audit_log import AuditAction, AuditOutcome
from models.custody_session import CustodySession
from models.enums import VehicleStatus
from models.user import User
from models.valet_ticket import ValetTicket
from models.vehicle import (
    COLOR_MAX_LENGTH,
    MAKE_MAX_LENGTH,
    MODEL_MAX_LENGTH,
    PLATE_MAX_LENGTH,
    Vehicle,
)
from services.audit import record_event
from utils.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from utils.security import AuthContext, Role
from utils.validation import validate_plate_number, validate_text, validate_uuid

NOTES_MAX_LENGTH = 500

#: Roles that may see vehicles belonging to other customers.
STAFF_ROLES: tuple[str, ...] = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)

PLATE_TAKEN_MESSAGE = (
    "That registration number is already registered. "
    "If the vehicle was handed over before, use the existing record."
)


def is_staff(actor: AuthContext | User | None) -> bool:
    """True when the actor may act across customers."""
    role = getattr(actor, "role", None)
    return role in STAFF_ROLES


def _audit(
    *,
    action: str,
    outcome: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor: User | None = None,
    actor_context: AuthContext | None = None,
    failure_reason: str | None = None,
    new_values: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Record what happened to a vehicle. Rejections are logged too.

    A thin delegate to :func:`services.audit.record_event`. The actor is taken
    from the loaded ``User`` row when one is available and from the JWT context
    otherwise, so an entry is attributed even when the caller did not need the
    full user object.
    """
    record_event(
        action=action,
        outcome=outcome,
        entity_type="vehicles",
        entity_id=entity_id,
        description=description,
        actor=actor if actor is not None else actor_context,
        failure_reason=failure_reason,
        new_values=new_values,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def vehicle_dict(vehicle: Vehicle, *, include_owner: bool = False) -> dict[str, Any]:
    """Public representation of a vehicle."""
    payload = vehicle.to_dict()
    payload["notes"] = vehicle.notes
    payload["is_in_custody"] = vehicle.is_in_custody
    if include_owner and vehicle.owner is not None:
        payload["owner"] = {
            "id": str(vehicle.owner.id),
            "email": vehicle.owner.email,
            "full_name": vehicle.owner.full_name,
            "phone": vehicle.owner.phone,
        }
    return payload


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_vehicles(
    actor: AuthContext,
    *,
    owner_id: uuid.UUID | None = None,
    status: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Vehicle], int]:
    """A page of vehicles plus the total matching count.

    Every filter is applied to the row query and the count query together.
    Building them independently is how a list endpoint ends up showing one page
    of correctly scoped rows under a `total_items` that counted the whole
    table - which is exactly the bug this shape invites, so the clauses are
    collected once and applied to both.
    """
    clauses = []

    # Customers are always restricted to their own vehicles. Staff may narrow
    # their list to one customer; operators otherwise see only sessions assigned
    # to them.
    if actor.role == Role.CUSTOMER.value:
        clauses.append(Vehicle.owner_id == uuid.UUID(actor.user_id))
    elif actor.role == Role.VALET_OPERATOR.value:
        if owner_id is not None:
            # The check-in wizard selects a customer before listing their cars.
            # Allow that explicitly scoped lookup so registered vehicles can
            # be checked in before they have an assigned custody session.
            clauses.append(Vehicle.owner_id == owner_id)
        else:
            actor_id = uuid.UUID(actor.user_id)
            assigned_session = select(CustodySession.id).where(
                    CustodySession.vehicle_id == Vehicle.id,
                    or_(
                        CustodySession.assigned_valet_id == actor_id,
                        CustodySession.received_by_id == actor_id,
                    ),
                ).exists()
            clauses.append(assigned_session)
    elif owner_id is not None:
        if actor.role not in (Role.VALET_MANAGER.value, Role.ADMIN.value):
            raise AuthorizationError("You are not permitted to list vehicles.")
        clauses.append(Vehicle.owner_id == owner_id)
    elif actor.role not in (Role.VALET_MANAGER.value, Role.ADMIN.value):
        raise AuthorizationError("You are not permitted to list vehicles.")

    if status:
        clauses.append(Vehicle.status == status)
    if not include_archived:
        clauses.append(Vehicle.status != VehicleStatus.ARCHIVED.value)
    if search:
        needle = f"%{search.strip()}%"
        clauses.append(
            or_(
                func.upper(Vehicle.plate_number).like(needle.upper()),
                func.upper(Vehicle.make).like(needle.upper()),
                func.upper(func.coalesce(Vehicle.model, "")).like(needle.upper()),
            )
        )

    statement = select(Vehicle).where(*clauses)
    count_statement = select(func.count()).select_from(Vehicle).where(*clauses)

    total = int(db.session.execute(count_statement).scalar_one())
    rows = db.session.execute(
        statement.order_by(Vehicle.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return list(rows), total


def get_vehicle(actor: AuthContext, vehicle_id: uuid.UUID | str) -> Vehicle:
    """Fetch a vehicle the actor may see, or raise 404."""
    details = {"vehicle_id": str(vehicle_id)}
    missing = NotFoundError("No vehicle was found with that id.", details=details)

    vehicle = db.session.get(Vehicle, validate_uuid(vehicle_id, field="vehicle_id"))
    if vehicle is None:
        raise missing
    if actor.role == Role.CUSTOMER.value and vehicle.owner_id != uuid.UUID(actor.user_id):
        # Deliberately the same 404 as "does not exist": telling a customer
        # that a plate belongs to someone else would leak information.
        raise missing
    if actor.role == Role.VALET_OPERATOR.value:
        actor_id = uuid.UUID(actor.user_id)
        assigned = db.session.execute(
            select(CustodySession.id)
            .where(
                CustodySession.vehicle_id == vehicle.id,
                or_(
                    CustodySession.assigned_valet_id == actor_id,
                    CustodySession.received_by_id == actor_id,
                ),
            )
            .limit(1)
        ).first()
        if assigned is None:
            raise missing
    elif actor.role not in (
        Role.CUSTOMER.value,
        Role.VALET_MANAGER.value,
        Role.ADMIN.value,
    ):
        raise AuthorizationError("You are not permitted to view vehicles.")
    return vehicle


def list_vehicle_sessions(actor: AuthContext, vehicle_id: uuid.UUID | str) -> list[dict[str, Any]]:
    """Previous valet sessions for a vehicle, newest first.

    Each entry pairs the custody session with the ticket it belongs to, so the
    customer can go straight from "my car was parked in B2" to the ticket.
    """
    vehicle = get_vehicle(actor, vehicle_id)

    rows = db.session.execute(
        select(CustodySession, ValetTicket)
        .join(ValetTicket, ValetTicket.id == CustodySession.ticket_id)
        .where(CustodySession.vehicle_id == vehicle.id)
        .order_by(CustodySession.dropped_off_at.desc())
    ).all()

    return [
        {
            "custody_session": session.to_dict(),
            "ticket": ticket.to_dict(),
        }
        for session, ticket in rows
    ]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def _validated_plate(payload: dict[str, Any], *, field: str = "plate_number") -> tuple[str, str]:
    """Return ``(display, normalized)`` for the plate in ``payload``.

    Two forms, because the schema keeps two columns on purpose:

    * ``display`` is what the customer typed, uppercased with runs of
      whitespace collapsed - so ``up  85 ca 4321`` is shown back as
      ``UP 85 CA 4321`` rather than as an unreadable ``UP85CA4321``;
    * ``normalized`` strips every separator and is the key uniqueness and
      lookup are decided on.

    Validation is done on the normalized form; storing it in ``plate_number``
    too would make the two columns identical and quietly defeat the reason the
    schema has both.
    """
    normalized = validate_plate_number(payload.get(field), field=field)
    display = re.sub(r"\s+", " ", str(payload.get(field)).strip().upper())
    return display, Vehicle.normalize_plate(normalized)


def _assert_plate_available(
    normalized_plate: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    statement = select(Vehicle.id).where(
        Vehicle.normalized_plate == normalized_plate,
        Vehicle.status != VehicleStatus.ARCHIVED.value,
    )
    if exclude_id is not None:
        statement = statement.where(Vehicle.id != exclude_id)
    if db.session.execute(statement).first() is not None:
        raise ConflictError(PLATE_TAKEN_MESSAGE, details={"field": "plate_number"})


def _resolve_owner(actor: AuthContext, payload: dict[str, Any]) -> User:
    """Who the vehicle belongs to.

    A customer may only register for themselves. Staff may register on a
    customer's behalf during check-in, which is what step 2 of the workflow
    needs; the owner must then be named explicitly and must actually exist.
    """
    requested = payload.get("owner_id")
    if requested is None:
        if is_staff(actor):
            raise ValidationError(
                "Staff must name the customer the vehicle belongs to.",
                details={"field": "owner_id"},
            )
        return db.session.get(User, uuid.UUID(actor.user_id))

    owner = db.session.get(User, validate_uuid(requested, field="owner_id"))
    if owner is None:
        raise NotFoundError(
            "No customer was found with that id.", details={"owner_id": str(requested)}
        )
    if not is_staff(actor) and owner.id != uuid.UUID(actor.user_id):
        raise ValidationError(
            "You can only register a vehicle to your own account.",
            details={"field": "owner_id"},
        )
    return owner


def create_vehicle(actor: AuthContext, payload: dict[str, Any], *, actor_user: User | None = None):
    """Register a vehicle. Returns the created row."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    plate_number, normalized_plate = _validated_plate(payload)
    _assert_plate_available(normalized_plate)

    make = validate_text(
        payload.get("make"), field="make", max_length=MAKE_MAX_LENGTH
    )
    model = validate_text(
        payload.get("model"), field="model", max_length=MODEL_MAX_LENGTH, required=False
    )
    color = validate_text(
        payload.get("color"), field="color", max_length=COLOR_MAX_LENGTH, required=False
    )
    notes = validate_text(
        payload.get("notes"), field="notes", max_length=NOTES_MAX_LENGTH, required=False
    )
    year = payload.get("year")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError) as exc:
            raise ValidationError("'year' must be a number.", details={"field": "year"}) from exc
        if not 1950 <= year <= 2100:
            raise ValidationError(
                "'year' must be between 1950 and 2100.", details={"field": "year"}
            )

    owner = _resolve_owner(actor, payload)

    tracking_method = str(payload.get("tracking_method") or "SIMULATED").upper()
    gps_device_id = payload.get("gps_device_id")
    if gps_device_id:
        gps_device_id = str(gps_device_id).strip()[:64]

    vehicle = Vehicle(
        owner_id=owner.id,
        plate_number=plate_number[:PLATE_MAX_LENGTH],
        normalized_plate=normalized_plate[:PLATE_MAX_LENGTH],
        make=make,
        model=model,
        year=year,
        color=color,
        notes=notes,
        status=str(VehicleStatus.REGISTERED),
        tracking_method=tracking_method,
        gps_device_id=gps_device_id,
    )
    db.session.add(vehicle)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError(PLATE_TAKEN_MESSAGE, details={"field": "plate_number"}) from exc

    _audit(
        action=AuditAction.VEHICLE_CREATED,
        outcome=AuditOutcome.SUCCESS,
        entity_id=vehicle.id,
        description=f"Registered vehicle {vehicle.plate_number}",
        actor=actor_user,
        actor_context=actor,
        new_values={"plate_number": vehicle.plate_number, "owner_id": str(owner.id)},
    )
    db.session.commit()
    return vehicle


def update_vehicle(
    actor: AuthContext,
    vehicle_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    actor_user: User | None = None,
):
    """Edit a vehicle's details. Ownership may not be reassigned."""
    vehicle = get_vehicle(actor, vehicle_id)
    if actor.role not in (
        Role.CUSTOMER.value,
        Role.ADMIN.value,
    ):
        raise AuthorizationError("Only the vehicle owner or an administrator may edit it.")

    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    if "owner_id" in payload and str(payload["owner_id"]) != str(vehicle.owner_id):
        raise ValidationError(
            "A vehicle's owner cannot be changed. Register it again under the "
            "other customer instead.",
            details={"field": "owner_id"},
        )

    changes: dict[str, Any] = {}

    if payload.get("plate_number"):
        plate_number, normalized_plate = _validated_plate(payload)
        if normalized_plate != vehicle.normalized_plate:
            _assert_plate_available(normalized_plate, exclude_id=vehicle.id)
            changes["plate_number"] = plate_number[:PLATE_MAX_LENGTH]
            changes["normalized_plate"] = normalized_plate[:PLATE_MAX_LENGTH]

    for field, max_length in (("make", MAKE_MAX_LENGTH), ("model", MODEL_MAX_LENGTH),
                              ("color", COLOR_MAX_LENGTH), ("notes", NOTES_MAX_LENGTH)):
        if field in payload:
            changes[field] = validate_text(
                payload[field], field=field, max_length=max_length,
                required=field == "make",
            )

    if "year" in payload and payload["year"] is not None:
        try:
            year = int(payload["year"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("'year' must be a number.", details={"field": "year"}) from exc
        if not 1950 <= year <= 2100:
            raise ValidationError(
                "'year' must be between 1950 and 2100.", details={"field": "year"}
            )
        changes["year"] = year

    if "status" in payload:
        requested = str(payload["status"]).upper()
        if requested not in {member.value for member in VehicleStatus}:
            raise ValidationError(
                f"'status' must be one of {[m.value for m in VehicleStatus]}.",
                details={"field": "status"},
            )
        if requested == VehicleStatus.IN_CUSTODY.value:
            # This state is derived from an open custody session; setting it by
            # hand would let the UI claim a vehicle is parked when it is not.
            raise ValidationError(
                "'IN_CUSTODY' is managed by the check-in workflow and cannot be set directly.",
                details={"field": "status"},
            )
        changes["status"] = requested

    if "tracking_method" in payload:
        changes["tracking_method"] = str(payload["tracking_method"]).upper()

    if "gps_device_id" in payload:
        changes["gps_device_id"] = (
            str(payload["gps_device_id"]).strip()[:64] if payload["gps_device_id"] else None
        )

    if not changes:
        raise ValidationError("No editable fields were provided.")

    for field, value in changes.items():
        setattr(vehicle, field, value)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError(PLATE_TAKEN_MESSAGE, details={"field": "plate_number"}) from exc

    _audit(
        action=AuditAction.UPDATE,
        outcome=AuditOutcome.SUCCESS,
        entity_id=vehicle.id,
        description=f"Updated vehicle {vehicle.plate_number}: {', '.join(sorted(changes))}",
        actor=actor_user,
        actor_context=actor,
        new_values=changes,
    )
    db.session.commit()
    return vehicle


def archive_vehicle(
    actor: AuthContext,
    vehicle_id: uuid.UUID | str,
    *,
    actor_user: User | None = None,
):
    """Archive a vehicle instead of deleting it.

    Refused while a custody session is open: the platform is still responsible
    for that car, and archiving it would hide it from the people watching it.
    """
    vehicle = get_vehicle(actor, vehicle_id)
    if actor.role not in (
        Role.CUSTOMER.value,
        Role.ADMIN.value,
    ):
        raise AuthorizationError(
            "Only the vehicle owner or an administrator may archive it."
        )

    open_session = db.session.execute(
        select(CustodySession.id).where(
            CustodySession.vehicle_id == vehicle.id,
            CustodySession.status != "CHECKED_OUT",
        )
    ).first()
    if open_session is not None:
        raise ConflictError(
            "This vehicle is currently in custody. Complete or cancel the "
            "session before archiving it.",
            details={"custody_session_id": str(open_session[0])},
        )

    from models.valet_ticket import ValetTicket
    open_ticket = db.session.execute(
        select(ValetTicket.id).where(
            ValetTicket.vehicle_id == vehicle.id,
            ValetTicket.status.in_(["CREATED", "ISSUED"]),
        )
    ).first()
    if open_ticket is not None:
        raise ConflictError(
            "This vehicle has an active ticket. Complete or cancel the "
            "ticket before archiving it.",
            details={"ticket_id": str(open_ticket[0])},
        )

    if vehicle.status == VehicleStatus.ARCHIVED.value:
        raise ConflictError("This vehicle is already archived.")

    # Unlink any paired GPS device so the hardware can be reassigned
    from models.gps_device import GpsDevice
    linked_devices = db.session.execute(
        select(GpsDevice).where(GpsDevice.vehicle_id == vehicle.id)
    ).scalars().all()
    for dev in linked_devices:
        dev.vehicle_id = None
        dev.connection_status = "NOT_CONFIGURED"
    vehicle.gps_device_id = None

    # Clear any zone assigned vehicle pointer
    from models.parking_zone import ParkingZone
    zones = db.session.execute(
        select(ParkingZone).where(ParkingZone.vehicle_id == vehicle.id)
    ).scalars().all()
    for z in zones:
        z.vehicle_id = None

    vehicle.status = str(VehicleStatus.ARCHIVED)
    _audit(
        action=AuditAction.DELETE,
        outcome=AuditOutcome.SUCCESS,
        entity_id=vehicle.id,
        description=f"Archived vehicle {vehicle.plate_number}",
        actor=actor_user,
        actor_context=actor,
        new_values={"status": vehicle.status},
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return vehicle


__all__ = [
    "NOTES_MAX_LENGTH",
    "PLATE_TAKEN_MESSAGE",
    "STAFF_ROLES",
    "archive_vehicle",
    "create_vehicle",
    "get_vehicle",
    "is_staff",
    "list_vehicle_sessions",
    "list_vehicles",
    "update_vehicle",
    "vehicle_dict",
]
