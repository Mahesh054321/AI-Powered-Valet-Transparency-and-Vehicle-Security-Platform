"""Valet ticket issuance, custody lifecycle, and ticket completion.

THE STATUS PROJECTION
---------------------
The database keeps the vocabulary created by migration ``0002``
(``ISSUED``/``ACTIVE``/``COMPLETED``/``CANCELLED``/``EXPIRED``) and this module
presents a different, coarser vocabulary to API clients:

    ISSUED    -> CREATED            ticket exists, vehicle not yet handed over
    ACTIVE    -> IN_CUSTODY         we are responsible for the car
    ACTIVE    -> READY_FOR_PICKUP   staged at the handover point
    COMPLETED -> COMPLETED
    CANCELLED -> CANCELLED
    EXPIRED   -> EXPIRED            passed through; see below

The projected status is **derived, never stored**. It is a pure function of the
ticket's stored status and, for the two states that only exist while custody is
open, the custody session's status. That is why ``READY_FOR_PICKUP`` needs no
new column: the ticket is still ``ACTIVE``, and it is the *session* that has
reached ``READY_FOR_PICKUP``. :func:`project_ticket_status` is pure so the whole
table above is unit-testable without a database.

``EXPIRED`` is passed through rather than hidden. It is a terminal state the
schema can hold, and silently reporting an expired ticket as ``CREATED`` would
tell a customer their car is waiting when the platform stopped tracking it. It
is documented as a sixth value of the API vocabulary.

WHY NOT STORE IT
----------------
Storing a derived field means two rows can disagree, and the disagreement is
always discovered at the worst moment. Deriving it costs one joined read.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from models.audit_log import AuditAction, AuditOutcome
from models.custody_session import CustodySession
from models.enums import (
    CustodyStatus,
    InspectionOutcome,
    InspectionStage,
    TicketStatus,
    VehicleStatus,
    ZoneStatus,
)
from models.parking_zone import ParkingZone
from models.user import User
from models.valet_ticket import ValetTicket
from models.vehicle import Vehicle
from models.vehicle_inspection import VehicleInspection
from services import qr_codes
from services.audit import record_event
from services.vehicles import is_staff
from utils.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from utils.image_ref import validate_image_ref_list
from utils.security import AuthContext, Role
from utils.validation import validate_text, validate_uuid

TICKET_CODE_LENGTH = 6
TICKET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read off a screen
MAX_CODE_ATTEMPTS = 12
INSTRUCTIONS_MAX_LENGTH = 500
#: Kilometres of movement in custody tolerated before the vehicle is flagged.
#: Shunting a car between bays can register a kilometre on its own, so the
#: threshold is deliberately small but not zero - otherwise every routine
#: re-park would raise a finding and the flag would stop meaning anything.
MILEAGE_TOLERANCE_KM = 1
NOTES_MAX_LENGTH = 1000

#: The statuses API clients see. Derived - see the module docstring.
API_TICKET_STATUSES: tuple[str, ...] = (
    "CREATED",
    "IN_CUSTODY",
    "READY_FOR_PICKUP",
    "COMPLETED",
    "CANCELLED",
    "EXPIRED",
)

#: Custody transitions the API will accept, by current status.
ALLOWED_CUSTODY_TRANSITIONS: dict[str, tuple[str, ...]] = {
    CustodyStatus.CHECKED_IN: (CustodyStatus.PARKED, CustodyStatus.IN_TRANSIT),
    CustodyStatus.PARKED: (
        CustodyStatus.IN_TRANSIT,
        CustodyStatus.RETRIEVAL_REQUESTED,
        CustodyStatus.READY_FOR_PICKUP,
    ),
    CustodyStatus.IN_TRANSIT: (
        CustodyStatus.PARKED,
        CustodyStatus.RETRIEVAL_IN_PROGRESS,
        CustodyStatus.READY_FOR_PICKUP,
    ),
    CustodyStatus.RETRIEVAL_REQUESTED: (
        CustodyStatus.RETRIEVAL_IN_PROGRESS,
        CustodyStatus.READY_FOR_PICKUP,
    ),
    CustodyStatus.RETRIEVAL_IN_PROGRESS: (CustodyStatus.READY_FOR_PICKUP,),
    CustodyStatus.READY_FOR_PICKUP: (
        CustodyStatus.RETRIEVAL_IN_PROGRESS,
        CustodyStatus.CHECKED_OUT,
    ),
    CustodyStatus.DISPUTED: (CustodyStatus.CHECKED_OUT,),
    CustodyStatus.CHECKED_OUT: (),
}

#: Roles permitted to run the valet side of the workflow.
VALET_ROLES: tuple[str, ...] = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)


# ---------------------------------------------------------------------------
# Status projection (pure - no database access)
# ---------------------------------------------------------------------------
def project_ticket_status(ticket_status: str, custody_status: str | None = None) -> str:
    """Map stored statuses onto the vocabulary API clients see.

    Raises :class:`ValueError` for a stored status this module does not know,
    so a future schema change fails loudly here instead of leaking an internal
    value to a client.
    """
    if ticket_status == TicketStatus.ISSUED.value:
        return "CREATED"
    if ticket_status == TicketStatus.ACTIVE.value:
        if custody_status == CustodyStatus.READY_FOR_PICKUP.value:
            return "READY_FOR_PICKUP"
        return "IN_CUSTODY"
    if ticket_status in (TicketStatus.COMPLETED.value, TicketStatus.CANCELLED.value):
        return ticket_status
    if ticket_status == TicketStatus.EXPIRED.value:
        return "EXPIRED"

    raise ValueError(f"Unknown ticket status {ticket_status!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(tz=UTC)


def _audit(
    *,
    action: str,
    outcome: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor: User | None = None,
    actor_context: AuthContext | None = None,
    failure_reason: str | None = None,
    new_values: dict | None = None,
    metadata: dict | None = None,
    custody_session_id: uuid.UUID | str | None = None,
) -> None:
    """Append an audit entry. A thin delegate to :func:`services.audit.record_event`.

    Accepts either a loaded ``User`` row or the JWT ``AuthContext``, because
    some call sites have the row and others only the token.
    """
    record_event(
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        actor=actor if actor is not None else actor_context,
        failure_reason=failure_reason,
        new_values=new_values,
        metadata=metadata,
        custody_session_id=custody_session_id,
    )


def generate_ticket_code() -> str:
    """A unique, human-readable ticket number: ``VT-202609-K7M3QD``.

    The alphabet omits the characters that are easy to misread when a code is
    read aloud or typed from a printed ticket.
    """
    body = "".join(secrets.choice(TICKET_CODE_ALPHABET) for _ in range(TICKET_CODE_LENGTH))
    return f"VT-{_now():%Y%m}-{body}"


def _unique_ticket_code() -> str:
    """Generate a code that is not already in use.

    ``ticket_code`` carries a UNIQUE constraint, so the last line of defence is
    the database; retrying here just keeps a collision from surfacing as a 500.
    """
    for _ in range(MAX_CODE_ATTEMPTS):
        candidate = generate_ticket_code()
        exists = db.session.execute(
            select(ValetTicket.id).where(ValetTicket.ticket_code == candidate)
        ).first()
        if exists is None:
            return candidate
    raise ConflictError(
        "Could not allocate a unique ticket number. Please retry.",
        details={"attempts": MAX_CODE_ATTEMPTS},
    )


def _require_valet(actor: AuthContext) -> None:
    if actor.role not in VALET_ROLES:
        raise AuthorizationError(
            f"Role '{actor.role}' cannot perform valet check-in. "
            f"Required: {', '.join(VALET_ROLES)}.",
            details={"role": actor.role, "required": list(VALET_ROLES)},
        )


def _open_custody_for(ticket: ValetTicket) -> CustodySession | None:
    return db.session.execute(
        select(CustodySession).where(
            CustodySession.ticket_id == ticket.id,
            CustodySession.status != CustodyStatus.CHECKED_OUT.value,
        )
    ).scalar_one_or_none()


def get_ticket(actor: AuthContext, ticket_id: uuid.UUID | str) -> ValetTicket:
    """Fetch a ticket the actor may see, or raise 404."""
    details = {"ticket_id": str(ticket_id)}
    missing = NotFoundError("No ticket was found with that id.", details=details)

    ticket = db.session.get(ValetTicket, validate_uuid(ticket_id, field="ticket_id"))
    if ticket is None:
        raise missing
    if not is_staff(actor) and ticket.customer_id != uuid.UUID(actor.user_id):
        # Same 404 either way: a customer must not learn that a ticket code
        # belongs to someone else.
        raise missing
    return ticket


def _get_zone(zone_id: uuid.UUID | str) -> ParkingZone:
    zone = db.session.get(ParkingZone, validate_uuid(zone_id, field="zone_id"))
    if zone is None:
        raise NotFoundError(
            "No parking zone was found with that id.", details={"zone_id": str(zone_id)}
        )
    return zone


def _assert_zone_parkable(zone: ParkingZone) -> None:
    if not zone.is_parkable:
        raise ConflictError(
            f"Zone {zone.code} is not accepting vehicles (status {zone.status}).",
            details={"zone_id": str(zone.id), "status": zone.status},
        )
    if zone.available_slots <= 0:
        raise ConflictError(
            f"Zone {zone.code} is at capacity ({zone.current_occupancy}/{zone.capacity}).",
            details={"zone_id": str(zone.id), "capacity": zone.capacity},
        )


def _parse_positive_int(value: Any, *, field: str, maximum: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"'{field}' must be a number.", details={"field": field}) from exc
    if number < 0:
        raise ValidationError(f"'{field}' must not be negative.", details={"field": field})
    if maximum is not None and number > maximum:
        raise ValidationError(
            f"'{field}' must be at most {maximum}.", details={"field": field}
        )
    return number


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def ticket_dict(
    ticket: ValetTicket,
    *,
    include_vehicle: bool = True,
    include_customer: bool = True,
    include_zone: bool = True,
    include_custody: bool = True,
    qr_url: str | None = None,
) -> dict[str, Any]:
    """The digital ticket, in the shape the UI and the QR page both render."""
    session = _open_custody_for(ticket)
    if session is None and include_custody:
        session = db.session.execute(
            select(CustodySession)
            .where(CustodySession.ticket_id == ticket.id)
            .order_by(CustodySession.dropped_off_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    stored_status = ticket.status
    payload: dict[str, Any] = {
        "id": str(ticket.id),
        "ticket_code": ticket.ticket_code,
        "status": project_ticket_status(
            stored_status, session.status if session else None
        ),
        # Exposed so a reviewer can see the mapping rather than take it on
        # trust. Clients should display `status`, not this.
        "stored_status": stored_status,
        "customer_id": str(ticket.customer_id),
        "vehicle_id": str(ticket.vehicle_id),
        "issued_at": ticket.issued_at.isoformat() if ticket.issued_at else None,
        "expected_return_at": (
            ticket.expected_return_at.isoformat() if ticket.expected_return_at else None
        ),
        "actual_drop_off_at": (
            ticket.actual_drop_off_at.isoformat() if ticket.actual_drop_off_at else None
        ),
        "actual_pickup_at": (
            ticket.actual_pickup_at.isoformat() if ticket.actual_pickup_at else None
        ),
        "special_instructions": ticket.special_instructions,
        "qr_payload": ticket.qr_payload,
        "qr_code_path": ticket.qr_code_path,
        "qr_url": qr_url or (
            f"/media/{ticket.qr_code_path}" if ticket.qr_code_path else None
        ),
    }

    if include_vehicle and ticket.vehicle is not None:
        payload["vehicle"] = {
            "id": str(ticket.vehicle.id),
            "plate_number": ticket.vehicle.plate_number,
            "make": ticket.vehicle.make,
            "model": ticket.vehicle.model,
            "year": ticket.vehicle.year,
            "color": ticket.vehicle.color,
            "status": ticket.vehicle.status,
        }
    if include_customer and ticket.customer is not None:
        payload["customer"] = {
            "id": str(ticket.customer.id),
            "email": ticket.customer.email,
            "full_name": ticket.customer.full_name,
            "phone": ticket.customer.phone,
        }
    if include_zone and session is not None and session.zone is not None:
        payload["parking_zone"] = {
            "id": str(session.zone.id),
            "code": session.zone.code,
            "name": session.zone.name,
        }
    if include_custody:
        payload["custody_session"] = session.to_dict() if session else None

    return payload


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------
def issue_ticket(
    actor: AuthContext,
    payload: dict[str, Any],
    *,
    actor_user: User | None = None,
) -> ValetTicket:
    """Create a ticket (workflow steps 5, 8 and 9: ticket, number, QR).

    The vehicle must already be registered - step 2 of the workflow. Custody is
    **not** started here; that is :func:`check_in`, because the schema wants a
    drop-off timestamp that only exists once the car is physically handed over.
    """
    _require_valet(actor)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    vehicle_id = payload.get("vehicle_id")
    if not vehicle_id:
        raise ValidationError("'vehicle_id' is required.", details={"field": "vehicle_id"})

    vehicle = db.session.get(Vehicle, validate_uuid(vehicle_id, field="vehicle_id"))
    if vehicle is None:
        raise NotFoundError(
            "No vehicle was found with that id.", details={"vehicle_id": str(vehicle_id)}
        )

    # A vehicle already in someone's custody cannot be ticketed again; this is
    # the same rule the partial unique index enforces, checked early so the
    # message is useful.
    already_open = db.session.execute(
        select(CustodySession.id).where(
            CustodySession.vehicle_id == vehicle.id,
            CustodySession.status != CustodyStatus.CHECKED_OUT.value,
        )
    ).first()
    if already_open is not None:
        raise ConflictError(
            f"{vehicle.plate_number} is already in custody. Complete that "
            "session before issuing a new ticket.",
            details={"custody_session_id": str(already_open[0])},
        )

    existing_ticket = db.session.execute(
        select(ValetTicket.id).where(
            ValetTicket.vehicle_id == vehicle.id,
            ValetTicket.status == TicketStatus.ISSUED.value,
        )
    ).first()
    if existing_ticket is not None:
        raise ConflictError(
            f"{vehicle.plate_number} already has an unstarted ticket.",
            details={"ticket_id": str(existing_ticket[0])},
        )

    instructions = validate_text(
        payload.get("special_instructions"),
        field="special_instructions",
        max_length=INSTRUCTIONS_MAX_LENGTH,
        required=False,
    )

    ticket_code = _unique_ticket_code()
    qr = qr_codes.issue_ticket_qr(ticket_code)

    ticket = ValetTicket(
        ticket_code=ticket_code,
        customer_id=vehicle.owner_id,
        vehicle_id=vehicle.id,
        status=str(TicketStatus.ISSUED),
        special_instructions=instructions,
        qr_payload=qr["qr_payload"],
        qr_code_path=qr["qr_code_path"],
        metadata_={"qr_sha256": qr["qr_image"]["sha256"]},
    )
    db.session.add(ticket)

    try:
        db.session.flush()
    except IntegrityError as exc:  # pragma: no cover - retried above
        db.session.rollback()
        raise ConflictError(
            "That ticket number was taken. Please retry.", details={"ticket_code": ticket_code}
        ) from exc

    _audit(
        action=AuditAction.TICKET_CREATED,
        outcome=AuditOutcome.SUCCESS,
        entity_type="valet_tickets",
        entity_id=ticket.id,
        description=f"Issued ticket {ticket.ticket_code} for {vehicle.plate_number}",
        actor=actor_user,
        actor_context=actor,
        new_values={"ticket_code": ticket.ticket_code, "vehicle_id": str(vehicle.id)},
    )
    db.session.commit()
    return ticket


# ---------------------------------------------------------------------------
# Check-in: custody start (workflow steps 6 and 7)
# ---------------------------------------------------------------------------
def check_in(
    actor: AuthContext,
    ticket_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    actor_user: User | None = None,
) -> ValetTicket:
    """Hand the vehicle over: open custody, assign a zone, record condition.

    One transaction, so a failure anywhere leaves no half-open session: the
    ticket goes ``ACTIVE``, the custody session opens at ``PARKED``, the zone's
    occupancy rises, the vehicle is marked ``IN_CUSTODY`` and the drop-off
    inspection stores the photo references.
    """
    _require_valet(actor)
    payload = payload if isinstance(payload, dict) else {}

    ticket = get_ticket(actor, ticket_id)
    if ticket.status == TicketStatus.CANCELLED.value:
        raise ConflictError("A cancelled ticket cannot be checked in.")
    if ticket.status != TicketStatus.ISSUED.value:
        raise ConflictError(
            f"This ticket is already {ticket.status.lower()}; it cannot be checked in again.",
            details={"status": ticket.status},
        )

    vehicle = db.session.get(Vehicle, ticket.vehicle_id)
    if vehicle is None:  # pragma: no cover - FK guarantees this
        raise NotFoundError("The vehicle on this ticket no longer exists.")

    zone_id = payload.get("zone_id")
    if not zone_id:
        raise ValidationError("'zone_id' is required.", details={"field": "zone_id"})
    zone = _get_zone(zone_id)
    _assert_zone_parkable(zone)

    odometer = _parse_positive_int(
        payload.get("odometer_at_drop_off"), field="odometer_at_drop_off"
    )
    fuel = _parse_positive_int(
        payload.get("fuel_level_percent"), field="fuel_level_percent", maximum=100
    )
    retrieval_minutes = _parse_positive_int(
        payload.get("expected_retrieval_minutes"), field="expected_retrieval_minutes"
    )
    damage_notes = validate_text(
        payload.get("pre_existing_damage_notes"),
        field="pre_existing_damage_notes",
        max_length=NOTES_MAX_LENGTH,
        required=False,
    )
    inspector_notes = validate_text(
        payload.get("inspector_notes"),
        field="inspector_notes",
        max_length=NOTES_MAX_LENGTH,
        required=False,
    )
    photos = validate_image_ref_list(payload.get("photo_refs"), field="photo_refs")

    now = _now()

    session = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        received_by_id=uuid.UUID(actor.user_id),
        status=str(CustodyStatus.PARKED),
        dropped_off_at=now,
        zone_assigned_at=now,
        odometer_at_drop_off=odometer,
        fuel_level_percent=fuel,
        expected_retrieval_minutes=retrieval_minutes,
        pre_existing_damage_notes=damage_notes,
    )
    db.session.add(session)

    # Occupancy is raised in the same transaction. The CHECK
    # `current_occupancy <= capacity` is the final guard; `_assert_zone_parkable`
    # only makes the failure readable.
    zone.current_occupancy = zone.current_occupancy + 1

    ticket.status = str(TicketStatus.ACTIVE)
    ticket.actual_drop_off_at = now

    vehicle.status = str(VehicleStatus.IN_CUSTODY)

    db.session.flush()

    inspection = VehicleInspection(
        custody_session_id=session.id,
        stage=str(InspectionStage.DROP_OFF),
        outcome=str(InspectionOutcome.PENDING_REVIEW),
        inspected_by_id=uuid.UUID(actor.user_id),
        captured_at=now,
        photo_refs=photos,
        photo_count=len(photos),
        odometer_reading=odometer,
        inspector_notes=inspector_notes,
    )
    db.session.add(inspection)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError(
            f"{vehicle.plate_number} already has an open custody session.",
            details={"vehicle_id": str(vehicle.id)},
        ) from exc

    # Two entries, not one. VEHICLE_CHECKED_IN is the gate event -- the car was
    # handed over. CUSTODY_STARTED is the platform's assumption of
    # responsibility, which is what the customer is actually entitled to know
    # the clock started on. They always happen together here, but they answer
    # different questions and an auditor may need to cite either.
    _audit(
        action=AuditAction.VEHICLE_CHECKED_IN,
        outcome=AuditOutcome.SUCCESS,
        entity_type="custody_sessions",
        entity_id=session.id,
        description=(
            f"Checked in {vehicle.plate_number} to zone {zone.code} "
            f"on ticket {ticket.ticket_code}"
        ),
        actor=actor_user,
        actor_context=actor,
        custody_session_id=session.id,
        new_values={
            "zone_id": str(zone.id),
            "status": session.status,
            "photo_count": len(photos),
        },
        metadata={
            "plate_number": vehicle.plate_number,
            "zone_code": zone.code,
            "ticket_code": ticket.ticket_code,
            "odometer_at_drop_off": odometer,
        },
    )
    _audit(
        action=AuditAction.CUSTODY_STARTED,
        outcome=AuditOutcome.SUCCESS,
        entity_type="custody_sessions",
        entity_id=session.id,
        description=(
            f"Custody of {vehicle.plate_number} began in zone {zone.code}; "
            f"the platform is responsible for the vehicle until handover"
        ),
        actor=actor_user,
        actor_context=actor,
        custody_session_id=session.id,
        metadata={
            "plate_number": vehicle.plate_number,
            "zone_code": zone.code,
            "ticket_code": ticket.ticket_code,
            "started_at": session.dropped_off_at.isoformat()
            if session.dropped_off_at
            else None,
        },
    )
    db.session.commit()
    return ticket


# ---------------------------------------------------------------------------
# Custody status
# ---------------------------------------------------------------------------
def get_custody_session(actor: AuthContext, session_id: uuid.UUID | str) -> CustodySession:
    session = db.session.get(
        CustodySession, validate_uuid(session_id, field="session_id")
    )
    if session is None:
        raise NotFoundError(
            "No custody session was found with that id.", details={"session_id": str(session_id)}
        )
    details = {"session_id": str(session_id)}
    missing = NotFoundError("No custody session was found with that id.", details=details)

    ticket = db.session.get(ValetTicket, session.ticket_id)
    foreign = ticket is not None and ticket.customer_id != uuid.UUID(actor.user_id)
    if not is_staff(actor) and foreign:
        # Same 404 either way, so a customer cannot probe for session ids.
        raise missing
    return session


def update_custody_status(
    actor: AuthContext,
    session_id: uuid.UUID | str,
    requested_status: Any,
    *,
    actor_user: User | None = None,
) -> CustodySession:
    """Move a custody session to a new state, refusing illegal jumps.

    The permitted transitions live in ``ALLOWED_CUSTODY_TRANSITIONS`` so the
    state machine is readable in one place. ``CHECKED_OUT`` is deliberately not
    reachable here - completion is :func:`complete_ticket`, because closing
    custody also has to settle the ticket, the zone occupancy and the vehicle
    status.

    **Who may move it depends on where it is going.** Almost every transition is
    a valet act - assigning a zone, driving the car, staging it for handover.
    Asking for your own car back is not: it is the customer's action, and
    requiring a valet to press the button on the customer's behalf would mean
    the customer cannot request a pickup at all. So a customer may move their
    *own* session to ``RETRIEVAL_REQUESTED`` and nothing else; every other
    transition stays staff-only. Ownership is enforced by
    :func:`get_custody_session`, which answers 404 for someone else's session
    rather than confirming it exists.
    """
    target = str(requested_status or "").upper()
    if target not in {member.value for member in CustodyStatus}:
        raise ValidationError(
            f"'status' must be one of {[m.value for m in CustodyStatus]}.",
            details={"field": "status"},
        )
    if target == CustodyStatus.CHECKED_OUT.value:
        raise ValidationError(
            "Use the ticket completion endpoint to close a custody session; it "
            "also settles the ticket, the zone occupancy and the vehicle status.",
            details={"field": "status"},
        )

    if not is_staff(actor) and target != CustodyStatus.RETRIEVAL_REQUESTED.value:
        _require_valet(actor)

    session = get_custody_session(actor, session_id)
    if not session.is_open:
        raise ConflictError("This custody session is already closed.")

    allowed = ALLOWED_CUSTODY_TRANSITIONS.get(session.status, ())
    if target not in allowed:
        raise ConflictError(
            f"Cannot move a session from {session.status} to {target}.",
            details={"from": session.status, "to": target, "allowed": list(allowed)},
        )

    previous = session.status
    now = _now()
    session.status = target
    if target == CustodyStatus.RETRIEVAL_REQUESTED.value and session.retrieval_requested_at is None:
        session.retrieval_requested_at = now
    if target == CustodyStatus.RETRIEVAL_IN_PROGRESS.value and session.retrieval_started_at is None:
        session.retrieval_started_at = now
    if target == CustodyStatus.READY_FOR_PICKUP.value and session.ready_for_pickup_at is None:
        session.ready_for_pickup_at = now

    _audit(
        action=AuditAction.STATUS_CHANGE,
        outcome=AuditOutcome.SUCCESS,
        entity_type="custody_sessions",
        entity_id=session.id,
        description=f"Custody {session.id} moved {previous} -> {target}",
        actor=actor_user,
        actor_context=actor,
        new_values={"status": target, "previous_status": previous},
    )

    # Staging a car for handover is the moment the customer needs to know about
    # it, so it raises a RETRIEVAL_READY alert inside the same transaction. The
    # transition and its notification land together or not at all.
    if target == CustodyStatus.READY_FOR_PICKUP.value:
        _publish_retrieval_ready(session, actor=actor)

    db.session.commit()
    return session


def _publish_retrieval_ready(session: CustodySession, *, actor=None) -> None:
    """Tell the customer their car is staged for collection.

    Best-effort: the custody transition is the fact, and a notification failure
    must not roll it back. Failing to tell someone their car is ready is a poor
    outcome; silently undoing the staging because a message could not be built
    would be worse.
    """
    try:
        from services.alerts import raise_retrieval_ready

        raise_retrieval_ready(session, actor=actor)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "Failed to raise a retrieval-ready alert for custody session %s", session.id
        )


# ---------------------------------------------------------------------------
# Completion and cancellation
# ---------------------------------------------------------------------------
def complete_ticket(
    actor: AuthContext,
    ticket_id: uuid.UUID | str,
    payload: dict[str, Any] | None = None,
    *,
    actor_user: User | None = None,
) -> ValetTicket:
    """Return the vehicle: close custody, free the zone, complete the ticket."""
    _require_valet(actor)
    payload = payload if isinstance(payload, dict) else {}

    ticket = get_ticket(actor, ticket_id)
    if ticket.status == TicketStatus.COMPLETED.value:
        raise ConflictError("This ticket is already completed.")
    if ticket.status == TicketStatus.CANCELLED.value:
        raise ConflictError("A cancelled ticket cannot be completed.")

    session = _open_custody_for(ticket)
    if session is None:
        raise ConflictError(
            "This ticket has no open custody session, so there is nothing to complete. "
            "Check the vehicle in first.",
            details={"ticket_id": str(ticket.id)},
        )

    vehicle = db.session.get(Vehicle, ticket.vehicle_id)
    now = _now()

    odometer = _parse_positive_int(
        payload.get("odometer_at_pickup"), field="odometer_at_pickup"
    )
    if (
        odometer is not None
        and session.odometer_at_drop_off is not None
        and odometer < session.odometer_at_drop_off
    ):
        raise ValidationError(
            "The pickup odometer reading is lower than the drop-off reading.",
            details={
                "field": "odometer_at_pickup",
                "drop_off": session.odometer_at_drop_off,
                "pickup": odometer,
            },
        )
    mileage = odometer - session.odometer_at_drop_off if (
        odometer is not None and session.odometer_at_drop_off is not None
    ) else None

    condition_accepted = payload.get("condition_accepted")
    if condition_accepted is not None:
        condition_accepted = bool(condition_accepted)
    condition_notes = validate_text(
        payload.get("condition_notes"),
        field="condition_notes",
        max_length=NOTES_MAX_LENGTH,
        required=False,
    )

    session.status = str(CustodyStatus.CHECKED_OUT)
    session.picked_up_at = now
    session.mileage_driven = mileage
    session.condition_accepted = condition_accepted
    session.condition_notes = condition_notes

    # Free the slot in the same transaction that closes the session.
    if session.zone is not None and session.zone.current_occupancy > 0:
        session.zone.current_occupancy = session.zone.current_occupancy - 1

    ticket.status = str(TicketStatus.COMPLETED)
    ticket.actual_pickup_at = now

    if vehicle is not None:
        # A car that moved appreciably while we held it is a finding, so it is
        # surfaced on the vehicle rather than buried in the session. The exact
        # figure is always recorded either way; only the flag is thresholded.
        suspicious = mileage is not None and mileage > MILEAGE_TOLERANCE_KM
        vehicle.status = str(
            VehicleStatus.FLAGGED if suspicious else VehicleStatus.REGISTERED
        )

    _audit(
        action=AuditAction.VEHICLE_PICKED_UP,
        outcome=AuditOutcome.SUCCESS,
        entity_type="valet_tickets",
        entity_id=ticket.id,
        description=(
            f"Completed ticket {ticket.ticket_code}"
            + (f"; {mileage} km driven in custody" if mileage else "")
        ),
        actor=actor_user,
        actor_context=actor,
        custody_session_id=session.id,
        new_values={
            "status": ticket.status,
            "mileage_driven": mileage,
            "condition_accepted": condition_accepted,
        },
        metadata={
            "ticket_code": ticket.ticket_code,
            "odometer_at_pickup": odometer,
            "mileage_driven": mileage,
            "condition_accepted": condition_accepted,
        },
    )
    # The mirror of CUSTODY_STARTED. Recording the close explicitly is what lets
    # a timeline show the custody window as a bounded interval rather than
    # leaving the reviewer to infer the end from the absence of later events.
    _audit(
        action=AuditAction.CUSTODY_COMPLETED,
        outcome=AuditOutcome.SUCCESS,
        entity_type="custody_sessions",
        entity_id=session.id,
        description=(
            f"Custody of {vehicle.plate_number if vehicle else 'the vehicle'} ended; "
            f"responsibility returned to the owner"
        ),
        actor=actor_user,
        actor_context=actor,
        custody_session_id=session.id,
        metadata={
            "ticket_code": ticket.ticket_code,
            "mileage_driven": mileage,
            "condition_accepted": condition_accepted,
            "picked_up_at": session.picked_up_at.isoformat()
            if session.picked_up_at
            else None,
            "dropped_off_at": session.dropped_off_at.isoformat()
            if session.dropped_off_at
            else None,
        },
    )
    db.session.commit()
    return ticket


def cancel_ticket(
    actor: AuthContext,
    ticket_id: uuid.UUID | str,
    payload: dict[str, Any] | None = None,
    *,
    actor_user: User | None = None,
) -> ValetTicket:
    """Void a ticket. Closes any open custody session and frees its zone."""
    _require_valet(actor)
    payload = payload if isinstance(payload, dict) else {}

    ticket = get_ticket(actor, ticket_id)
    if ticket.status in (TicketStatus.COMPLETED.value, TicketStatus.CANCELLED.value):
        raise ConflictError(f"A {ticket.status.lower()} ticket cannot be cancelled.")

    reason = validate_text(
        payload.get("reason"),
        field="reason",
        max_length=INSTRUCTIONS_MAX_LENGTH,
        required=False,
    )

    session = _open_custody_for(ticket)
    now = _now()
    if session is not None:
        session.status = str(CustodyStatus.CHECKED_OUT)
        session.picked_up_at = now
        session.condition_notes = reason or session.condition_notes
        if session.zone is not None and session.zone.current_occupancy > 0:
            session.zone.current_occupancy = session.zone.current_occupancy - 1

    vehicle = db.session.get(Vehicle, ticket.vehicle_id)
    if vehicle is not None and vehicle.status == VehicleStatus.IN_CUSTODY.value:
        vehicle.status = str(VehicleStatus.REGISTERED)

    ticket.status = str(TicketStatus.CANCELLED)

    _audit(
        action=AuditAction.STATUS_CHANGE,
        outcome=AuditOutcome.SUCCESS,
        entity_type="valet_tickets",
        entity_id=ticket.id,
        description=f"Cancelled ticket {ticket.ticket_code}" + (f": {reason}" if reason else ""),
        actor=actor_user,
        actor_context=actor,
        new_values={"status": ticket.status, "reason": reason},
    )
    db.session.commit()
    return ticket


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
def list_tickets(
    actor: AuthContext,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Tickets the actor may see, newest first.

    Filtering by ``status`` filters on the *projected* status, because that is
    the vocabulary the caller was given. Doing it in SQL would mean filtering
    on a value the client never sees.
    """
    statement = select(ValetTicket)
    if not is_staff(actor):
        statement = statement.where(ValetTicket.customer_id == uuid.UUID(actor.user_id))

    tickets = list(db.session.execute(statement.order_by(ValetTicket.issued_at.desc())).scalars())

    payload = [ticket_dict(ticket) for ticket in tickets]
    if status:
        wanted = str(status).upper()
        if wanted not in API_TICKET_STATUSES:
            raise ValidationError(
                f"'status' must be one of {list(API_TICKET_STATUSES)}.",
                details={"field": "status", "allowed": list(API_TICKET_STATUSES)},
            )
        payload = [item for item in payload if item["status"] == wanted]

    total = len(payload)
    start = (page - 1) * page_size
    return payload[start : start + page_size], total


def list_customers(actor: AuthContext, *, search: str | None = None) -> list[dict[str, Any]]:
    """The customer directory the check-in workflow searches (step 1).

    Staff-only, and it returns the minimum needed to pick a person: no password
    hash, no notes, no role history.
    """
    if not is_staff(actor):
        raise AuthorizationError("Only valet staff may browse the customer directory.")

    statement = select(User).where(User.role == Role.CUSTOMER.value)
    if search:
        needle = f"%{search.strip()}%"
        statement = statement.where(
            (User.email.ilike(needle)) | (User.full_name.ilike(needle))
        )

    return [
        {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "phone": user.phone,
            "status": user.status,
        }
        for user in db.session.execute(statement.order_by(User.full_name)).scalars()
    ]


def list_zones(actor: AuthContext, *, parkable_only: bool = False) -> list[dict[str, Any]]:
    """Parking zones, for the assignment step."""
    statement = select(ParkingZone).order_by(ParkingZone.code)
    if parkable_only:
        statement = statement.where(
            ParkingZone.status.in_([ZoneStatus.ACTIVE.value, ZoneStatus.FULL.value])
        )
    return [zone.to_dict() for zone in db.session.execute(statement).scalars()]


__all__ = [
    "ALLOWED_CUSTODY_TRANSITIONS",
    "API_TICKET_STATUSES",
    "MILEAGE_TOLERANCE_KM",
    "VALET_ROLES",
    "cancel_ticket",
    "check_in",
    "complete_ticket",
    "generate_ticket_code",
    "get_custody_session",
    "get_ticket",
    "issue_ticket",
    "list_customers",
    "list_tickets",
    "list_zones",
    "project_ticket_status",
    "ticket_dict",
    "update_custody_status",
]
