"""The valet operator dashboard, composed server-side.

One call returns everything the operator screen renders: vehicles expected but
not yet handed over, every vehicle currently in custody with its GPS and
security posture, cars staged for handover, cars a valet still has to fetch,
open security alerts, condition state, and zone occupancy.

**Why the server composes this rather than the browser.** The customer
dashboard makes the same argument and it applies with more force here: an
operator acts on this screen. If the ACTIVE VEHICLES list and the SECURITY
ALERTS list were fetched separately, a car could appear calm in one and
breaching in the other, and the operator would act on whichever they read
last. One snapshot cannot disagree with itself.

**Every section reports its own state.** ``ok``, ``empty`` or ``unavailable``,
plus a reason. An empty retrieval queue is a good outcome and must not look
like a failed request. Each section is also wrapped, so one broken dependency
degrades one panel instead of taking the whole operations screen down — an
operator who has lost the condition panel still needs the security one.

**Authorization is reused, not reimplemented.** Alerts come from
:func:`services.geofence.list_alerts`, condition from
:func:`services.inspections.get_inspection_report`, the queue from
:func:`services.customer_dashboard.pending_retrievals`. Those already decide
what an actor may see.

**This service only reads.** Every mutating action on the operator screen —
status change, mark ready, complete pickup, upload photos, acknowledge an
alert — calls the endpoint that already exists for it, so there is exactly one
state machine and exactly one audit path. A dashboard that performed its own
writes would be a second, less-tested copy of those rules.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from database import db
from models.custody_session import CustodySession
from models.enums import AlertStatus, CustodyStatus, MovementStatus, TicketStatus
from models.gps_event import GpsEvent
from models.parking_zone import ParkingZone
from models.security_alert import SecurityAlert
from models.valet_ticket import ValetTicket
from models.vehicle_inspection import VehicleInspection
from services import customer_dashboard as customer_dashboard_service
from services import geofence as geofence_service
from services import inspections as inspection_service
from services.vehicles import is_staff
from utils.exceptions import AuthorizationError
from utils.security import AuthContext

logger = logging.getLogger(__name__)

#: Custody states that mean the vehicle is physically with us right now.
OPEN_STATUSES = tuple(status.value for status in CustodyStatus if status.is_open)

#: Custody state → the badge an operator reads at a glance.
#:
#: Deliberately coarser than :class:`CustodyStatus`. An operator scanning twenty
#: rows does not need to distinguish ``CHECKED_IN`` from ``PARKED``; they need
#: to know whether a car is sitting still, being moved, or ready to hand back.
#: ``ALERT`` is not in this map because it is not a custody state at all — it is
#: a security override applied on top of whichever state the car is in.
DISPLAY_STATUS_BY_CUSTODY = {
    CustodyStatus.CHECKED_IN.value: "IN_CUSTODY",
    CustodyStatus.PARKED.value: "IN_CUSTODY",
    CustodyStatus.IN_TRANSIT.value: "MOVING",
    CustodyStatus.RETRIEVAL_REQUESTED.value: "IN_CUSTODY",
    CustodyStatus.RETRIEVAL_IN_PROGRESS.value: "MOVING",
    CustodyStatus.READY_FOR_PICKUP.value: "READY",
    CustodyStatus.CHECKED_OUT.value: "COMPLETED",
    CustodyStatus.DISPUTED.value: "ALERT",
}

#: Labels are sent with the payload so the UI does not keep a second copy of
#: this vocabulary that can drift away from the backend's.
DISPLAY_STATUS_LABELS = {
    "IN_CUSTODY": "In custody",
    "MOVING": "Moving",
    "READY": "Ready",
    "ALERT": "Alert",
    "COMPLETED": "Completed",
    "UNKNOWN": "Unknown",
}

#: Movement verdicts that mean the car is doing something it should not be.
_ALARMING_MOVEMENT = (MovementStatus.BREACH.value, MovementStatus.SUSPICIOUS.value)


def _section(state: str, reason: str | None = None, **payload: Any) -> dict[str, Any]:
    """One dashboard section, always carrying its own state."""
    body: dict[str, Any] = {"state": state}
    if reason:
        body["reason"] = reason
    body.update(payload)
    return body


def _require_staff(actor: AuthContext) -> None:
    """This screen is the operator's. A customer has no business on it."""
    if not is_staff(actor):
        raise AuthorizationError(
            "The valet dashboard is restricted to staff.",
            details={"role": actor.role},
        )


# ---------------------------------------------------------------------------
# Per-row helpers
# ---------------------------------------------------------------------------
def _vehicle_of(session: CustodySession) -> dict[str, Any] | None:
    vehicle = session.vehicle
    if vehicle is None:
        return None
    return {
        "id": str(vehicle.id),
        "plate_number": vehicle.plate_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "status": vehicle.status,
    }


def _customer_of(session: CustodySession) -> dict[str, Any] | None:
    """Who to hand the keys back to.

    An operator completing a pickup needs to check they are giving the car to
    the right person, so the name is here rather than behind another click.
    """
    ticket = session.ticket
    customer = ticket.customer if ticket else None
    if customer is None:
        return None
    return {
        "id": str(customer.id),
        "name": customer.full_name,
        "email": customer.email,
        "phone": customer.phone,
    }


def _latest_fix(custody_id: Any) -> dict[str, Any] | None:
    """The most recent GPS fix, and whether anything has been recorded at all.

    A session with no fixes is not the same as a session whose last fix was
    clean, and the difference matters: one means "we have no idea where this
    car is", the other means "we do, and it is where it should be".
    """
    row = db.session.execute(
        select(
            GpsEvent.recorded_at,
            GpsEvent.latitude,
            GpsEvent.longitude,
            GpsEvent.movement_status,
            GpsEvent.source,
            GpsEvent.is_within_zone,
        )
        .where(GpsEvent.custody_session_id == custody_id)
        .order_by(GpsEvent.recorded_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return {
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "latitude": float(row.latitude) if row.latitude is not None else None,
        "longitude": float(row.longitude) if row.longitude is not None else None,
        "movement_status": row.movement_status,
        "source": row.source,
        "is_within_zone": row.is_within_zone,
    }


def _open_alert_statuses() -> tuple[str, ...]:
    return (
        AlertStatus.OPEN.value,
        AlertStatus.ACKNOWLEDGED.value,
        AlertStatus.INVESTIGATING.value,
    )


def _display_status(custody_status: str, *, alarming: bool) -> str:
    """The badge an operator reads.

    A security problem overrides the custody state rather than sitting beside
    it. A car that is ``PARKED`` and breaching its geofence is not primarily
    "in custody" — the thing the operator must act on is the breach, and a
    badge that said ``IN CUSTODY`` with a small warning icon next to it would
    be read as fine at a glance, which is exactly the wrong failure mode.
    """
    if alarming:
        return "ALERT"
    return DISPLAY_STATUS_BY_CUSTODY.get(custody_status, "UNKNOWN")


def _row(session: CustodySession, *, open_alert_count: int = 0) -> dict[str, Any]:
    """One vehicle row, shared by the active, pickup and incoming sections."""
    fix = _latest_fix(session.id)
    movement = fix["movement_status"] if fix else None
    alarming = bool(open_alert_count) or movement in _ALARMING_MOVEMENT

    return {
        "custody_session_id": str(session.id),
        "ticket_id": str(session.ticket_id) if session.ticket_id else None,
        "ticket_code": session.ticket.ticket_code if session.ticket else None,
        "custody_status": session.status,
        "display_status": _display_status(session.status, alarming=alarming),
        "display_label": DISPLAY_STATUS_LABELS.get(
            _display_status(session.status, alarming=alarming), "Unknown"
        ),
        "vehicle": _vehicle_of(session),
        "customer": _customer_of(session),
        "zone": (
            {"id": str(session.zone.id), "code": session.zone.code, "name": session.zone.name}
            if session.zone
            else None
        ),
        "dropped_off_at": (
            session.dropped_off_at.isoformat() if session.dropped_off_at else None
        ),
        "retrieval_requested_at": (
            session.retrieval_requested_at.isoformat()
            if session.retrieval_requested_at
            else None
        ),
        "ready_for_pickup_at": (
            session.ready_for_pickup_at.isoformat() if session.ready_for_pickup_at else None
        ),
        "assigned_valet_id": (
            str(session.assigned_valet_id) if session.assigned_valet_id else None
        ),
        "handover_point": session.handover_point,
        "gps": fix,
        # Stated per row rather than once for the page: a track can mix real
        # and simulated fixes, and the operator needs to know which row is
        # which before they act on a movement verdict.
        "gps_has_data": fix is not None,
        "open_alert_count": open_alert_count,
        "alarming": alarming,
    }


def _open_alert_counts(custody_ids: list[Any]) -> dict[Any, int]:
    """Open alert counts for a batch of sessions, in one query.

    Counting per row would be one query per vehicle. Twenty cars is twenty
    round trips on a screen that polls.
    """
    if not custody_ids:
        return {}
    rows = db.session.execute(
        select(SecurityAlert.custody_session_id, func.count(SecurityAlert.id))
        .where(
            SecurityAlert.custody_session_id.in_(custody_ids),
            SecurityAlert.status.in_(_open_alert_statuses()),
        )
        .group_by(SecurityAlert.custody_session_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _open_sessions(actor: AuthContext) -> list[CustodySession]:
    """Every session with a vehicle physically in our custody."""
    statement = (
        select(CustodySession)
        .where(CustodySession.status.in_(OPEN_STATUSES))
        .order_by(CustodySession.dropped_off_at.asc().nulls_last())
    )
    return list(db.session.execute(statement).scalars())


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _incoming_section(actor: AuthContext) -> dict[str, Any]:
    """Vehicles expected but not yet handed over.

    "Incoming" means a ticket exists and custody has not started: the customer
    has been issued a ticket, and the car is either on its way or at the door.
    This is the only arrival state the data model actually has — there is no
    separate "expected arrival" record, so inventing one would have meant
    showing vehicles that were never coming.
    """
    tickets = list(
        db.session.execute(
            select(ValetTicket)
            .where(ValetTicket.status == TicketStatus.ISSUED.value)
            .order_by(ValetTicket.issued_at.desc())
        )
        .scalars()
    )

    items = [
        {
            "ticket_id": str(ticket.id),
            "ticket_code": ticket.ticket_code,
            "status": "CREATED",
            "issued_at": ticket.issued_at.isoformat() if ticket.issued_at else None,
            "expected_return_at": (
                ticket.expected_return_at.isoformat()
                if ticket.expected_return_at
                else None
            ),
            "vehicle": (
                {
                    "id": str(ticket.vehicle.id),
                    "plate_number": ticket.vehicle.plate_number,
                    "make": ticket.vehicle.make,
                    "model": ticket.vehicle.model,
                    "color": ticket.vehicle.color,
                }
                if ticket.vehicle
                else None
            ),
            "customer": (
                {
                    "id": str(ticket.customer.id),
                    "name": ticket.customer.full_name,
                    "email": ticket.customer.email,
                }
                if ticket.customer
                else None
            ),
            "qr_url": ticket.qr_code_path,
        }
        for ticket in tickets
    ]

    if not items:
        return _section(
            "empty",
            reason="No vehicles are waiting to be checked in.",
            items=[],
            count=0,
        )
    return _section("ok", items=items, count=len(items))


def _active_section(actor: AuthContext, sessions: list[CustodySession]) -> dict[str, Any]:
    """Every vehicle in custody, with GPS and security state."""
    counts = _open_alert_counts([session.id for session in sessions])
    items = [_row(session, open_alert_count=counts.get(session.id, 0)) for session in sessions]

    if not items:
        return _section(
            "empty",
            reason="No vehicles are currently in custody.",
            items=[],
            count=0,
        )
    return _section(
        "ok",
        items=items,
        count=len(items),
        moving=sum(1 for item in items if item["display_status"] == "MOVING"),
        alarming=sum(1 for item in items if item["alarming"]),
    )


def _pickup_section(actor: AuthContext, sessions: list[CustodySession]) -> dict[str, Any]:
    """Cars staged at the handover point, waiting for the customer.

    Distinct from the retrieval queue on purpose: these are the two ends of the
    same job. The queue is work a valet still has to do (go and get the car);
    this is work already done and waiting on the customer to sign for it. An
    operator who could not tell those apart would either fetch a car twice or
    leave one standing.
    """
    staged = [
        session
        for session in sessions
        if session.status == CustodyStatus.READY_FOR_PICKUP.value
    ]
    counts = _open_alert_counts([session.id for session in staged])
    items = [_row(session, open_alert_count=counts.get(session.id, 0)) for session in staged]

    if not items:
        return _section(
            "empty",
            reason="No vehicles are staged for handover.",
            items=[],
            count=0,
        )
    return _section("ok", items=items, count=len(items))


def _queue_section(actor: AuthContext) -> dict[str, Any]:
    """Cars a valet still has to fetch.

    Reuses the customer dashboard's queue builder rather than re-querying, so
    the operator and the customer are guaranteed to be looking at the same
    queue in the same order with the same estimate.
    """
    queue = customer_dashboard_service.pending_retrievals(actor)
    if not queue.get("items"):
        return _section(
            "empty",
            reason="No retrieval requests are waiting.",
            items=[],
            count=0,
            waiting=0,
            delivery=queue.get("delivery"),
            delivery_note=queue.get("delivery_note"),
        )
    return _section(
        "ok",
        items=queue["items"],
        count=queue["count"],
        waiting=queue["waiting"],
        delivery=queue.get("delivery"),
        delivery_note=queue.get("delivery_note"),
    )


def _alerts_section(actor: AuthContext) -> dict[str, Any]:
    """Open security alerts across every vehicle in custody."""
    listed = geofence_service.list_alerts(actor, page_size=50)
    # "alerts", not "items" - and indexed, not defaulted. A wrong key here
    # would read as "no open alerts", which is the one wrong answer an
    # operator screen must never give.
    items = listed["alerts"]
    open_statuses = _open_alert_statuses()
    open_items = [alert for alert in items if alert.get("status") in open_statuses]

    if not open_items:
        return _section(
            "empty",
            reason="No open security alerts.",
            items=[],
            count=0,
        )
    return _section(
        "ok",
        items=open_items,
        count=len(open_items),
        highest_severity=customer_dashboard_service._worst_severity(  # noqa: SLF001
            [alert.get("severity") for alert in open_items]
        ),
    )


def _inspection_section(actor: AuthContext, sessions: list[CustodySession]) -> dict[str, Any]:
    """Condition state per vehicle: photographed, compared, and awaiting review.

    Read straight from the inspection rows rather than through
    ``get_inspection_report`` per vehicle — that report renders photo
    comparisons, and pulling it twenty times to display a count would be
    wasteful. The full report is still one click away on the row.
    """
    if not sessions:
        return _section(
            "empty",
            reason="No vehicles in custody to inspect.",
            items=[],
            count=0,
        )

    inspections = list(
        db.session.execute(
            select(VehicleInspection).where(
                VehicleInspection.custody_session_id.in_([s.id for s in sessions])
            )
        ).scalars()
    )
    by_session: dict[Any, dict[str, VehicleInspection]] = {}
    for inspection in inspections:
        by_session.setdefault(inspection.custody_session_id, {})[inspection.stage] = inspection

    items = []
    for session in sessions:
        stages = by_session.get(session.id, {})
        drop_off = stages.get("DROP_OFF")
        pickup = stages.get("PICKUP")
        # Unverified findings are *potential* damage. Counting them as damage
        # would overstate what the detector established, and the operator is
        # the person who has to go and look.
        unverified = sum(
            inspection.damage_count or 0
            for inspection in (drop_off, pickup)
            if inspection is not None
        )
        items.append(
            {
                "custody_session_id": str(session.id),
                "vehicle": _vehicle_of(session),
                "drop_off_captured": drop_off is not None,
                "drop_off_photo_count": drop_off.photo_count if drop_off else 0,
                "drop_off_outcome": drop_off.outcome if drop_off else None,
                "pickup_captured": pickup is not None,
                "pickup_photo_count": pickup.photo_count if pickup else 0,
                "pickup_outcome": pickup.outcome if pickup else None,
                "comparison_ready": pickup is not None and drop_off is not None,
                "unverified_detections": unverified,
                # The single most actionable column: a car about to be handed
                # back with unconfirmed findings is a dispute waiting to happen.
                "needs_pickup_photos": pickup is None,
                "needs_review": unverified > 0,
            }
        )

    return _section(
        "ok",
        items=items,
        count=len(items),
        awaiting_pickup_photos=sum(1 for item in items if item["needs_pickup_photos"]),
        awaiting_review=sum(1 for item in items if item["needs_review"]),
    )


def _zones_section(actor: AuthContext) -> dict[str, Any]:
    """Zone occupancy, so an operator can see where there is room."""
    zones = list(db.session.execute(select(ParkingZone).order_by(ParkingZone.code)).scalars())
    items = [
        {
            "id": str(zone.id),
            "code": zone.code,
            "name": zone.name,
            "status": zone.status,
            "capacity": zone.capacity,
            "current_occupancy": zone.current_occupancy,
            "available_slots": zone.available_slots,
            "occupancy_percent": (
                round(100 * zone.current_occupancy / zone.capacity) if zone.capacity else 0
            ),
            "parkable": zone.is_parkable,
        }
        for zone in zones
    ]
    if not items:
        return _section("empty", reason="No parking zones are configured.", items=[], count=0)
    return _section(
        "ok",
        items=items,
        count=len(items),
        total_capacity=sum(zone.capacity for zone in zones),
        total_occupancy=sum(zone.current_occupancy for zone in zones),
    )


def _safe(build, name: str, empty_reason: str) -> dict[str, Any]:
    """Run one section builder, degrading to ``unavailable`` instead of a 500."""
    try:
        return build()
    except Exception:  # noqa: BLE001 - one broken panel must not blank the screen
        logger.exception("Staff dashboard section %r failed", name)
        return _section("unavailable", reason=empty_reason)


def _capabilities(actor: AuthContext) -> dict[str, bool]:
    """What this actor is actually permitted to do.

    Not every staff role can do everything on this screen. A valet operator may
    move a car and photograph it, but acknowledging or resolving a security
    alert is a manager act, and verifying a damage finding is deliberately
    narrower than capturing one. Rather than let the UI guess, the flags come
    from the same role tuples the endpoints enforce, so a button is shown
    exactly when pressing it would succeed.

    These mirror the guards rather than restating them from memory: importing
    the tuples means a future narrowing of a role shows up here automatically
    instead of leaving a button that 403s.
    """
    from services.geofence import ALERT_TRIAGE_ROLES
    from services.inspections import INSPECTION_ROLES, VERIFICATION_ROLES
    from services.tickets import VALET_ROLES

    role = actor.role
    return {
        "can_move_vehicle": role in VALET_ROLES,
        "can_check_in": role in VALET_ROLES,
        "can_complete_pickup": role in VALET_ROLES,
        "can_capture_inspection": role in INSPECTION_ROLES,
        "can_verify_damage": role in VERIFICATION_ROLES,
        "can_triage_alerts": role in ALERT_TRIAGE_ROLES,
    }


def build_dashboard(actor: AuthContext) -> dict[str, Any]:
    """Compose the whole operator screen in one consistent snapshot."""
    _require_staff(actor)

    sessions = _open_sessions(actor)

    return {
        "generated_for": {"user_id": actor.user_id, "role": actor.role},
        "capabilities": _capabilities(actor),
        "display_status_labels": DISPLAY_STATUS_LABELS,
        # How the client should refresh. Stated rather than assumed: this is a
        # poll, and an operator needs to know how stale the screen can be.
        "delivery": "polled",
        "counts": {
            "incoming": None,
            "active": len(sessions),
            "pending_pickups": sum(
                1 for s in sessions if s.status == CustodyStatus.READY_FOR_PICKUP.value
            ),
            "open_alerts": None,
        },
        "incoming": _safe(
            lambda: _incoming_section(actor),
            "incoming",
            "The arrivals list could not be read.",
        ),
        "active_vehicles": _safe(
            lambda: _active_section(actor, sessions),
            "active_vehicles",
            "The active vehicle list could not be read.",
        ),
        "pending_pickups": _safe(
            lambda: _pickup_section(actor, sessions),
            "pending_pickups",
            "The pickup list could not be read.",
        ),
        "retrieval_queue": _safe(
            lambda: _queue_section(actor),
            "retrieval_queue",
            "The retrieval queue could not be read.",
        ),
        "security_alerts": _safe(
            lambda: _alerts_section(actor),
            "security_alerts",
            "Security alerts could not be read.",
        ),
        "inspections": _safe(
            lambda: _inspection_section(actor, sessions),
            "inspections",
            "Inspection state could not be read.",
        ),
        "zones": _safe(
            lambda: _zones_section(actor),
            "zones",
            "Zone occupancy could not be read.",
        ),
    }


def condition_report(actor: AuthContext, custody_id: str) -> dict[str, Any]:
    """The full before/after comparison for one vehicle.

    A thin pass-through so the operator screen has one place to call, but the
    access decision stays inside the inspection service where it already lives.
    """
    _require_staff(actor)
    return inspection_service.get_inspection_report(actor, custody_id)


__all__ = [
    "DISPLAY_STATUS_BY_CUSTODY",
    "DISPLAY_STATUS_LABELS",
    "OPEN_STATUSES",
    "build_dashboard",
    "condition_report",
]
