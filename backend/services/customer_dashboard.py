"""The customer dashboard, composed server-side.

One call returns everything the dashboard renders: the vehicle in custody, the
ticket, the custody state, where the car is, which zone it is in, when it was
checked in, its security posture, its condition report, the retrieval estimate
and any open alerts.

**Why the server composes this rather than the browser.** Ten cards fetched as
ten parallel requests are read at ten different moments, so a dashboard can show
a custody status from before a valet moved the car and a queue position from
after. One snapshot cannot disagree with itself. It also means the ownership
rules are applied once, in one place, rather than being re-derived by whichever
card happens to be rendering.

**Every section reports its own state.** A customer with no car in custody is a
normal condition, not an error, and a dashboard that showed "—" for ten cards
would be indistinguishable from one that had failed. Each section therefore
carries a ``state`` of ``ok``, ``empty`` or ``unavailable`` plus a reason, so the
UI can say "no vehicle is in custody" rather than inventing a value. Nothing
here is static: an ``empty`` state is a real answer from the database.

**Authorization is reused, not reimplemented.** Location and security come from
:func:`services.geofence.tracking_view`, the condition report from
:func:`services.inspections.get_inspection_report`, alerts from
:func:`services.geofence.list_alerts`. Each of those already decides what this
actor may see. A dashboard that queried the tables directly would have to get
those rules right a second time, and getting them wrong would leak another
customer's vehicle.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from database import db
from models.custody_session import CustodySession
from models.enums import AlertStatus, CustodyStatus
from models.retrieval_prediction import RetrievalPrediction
from models.valet_ticket import ValetTicket
from services import geofence as geofence_service
from services import inspections as inspection_service
from services import retrieval as retrieval_service
from services import tickets as ticket_service
from services.vehicles import is_staff
from utils.security import AuthContext

logger = logging.getLogger(__name__)

#: Custody states in which the vehicle is still the platform's responsibility.
OPEN_STATUSES = tuple(status.value for status in CustodyStatus if status.is_open)

#: States where a valet is already bringing the car, so requesting again would
#: be refused by the state machine. The UI greys the button and says why.
RETRIEVAL_ALREADY_ACTIVE = (
    CustodyStatus.RETRIEVAL_REQUESTED.value,
    CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
    CustodyStatus.READY_FOR_PICKUP.value,
)


def _section(state: str, reason: str | None = None, **payload: Any) -> dict[str, Any]:
    """One dashboard section, always carrying its own state.

    ``state`` is never omitted. A card that silently dropped a key would render
    as blank, and blank reads as "nothing wrong" rather than "nothing known".
    """
    body: dict[str, Any] = {"state": state}
    if reason:
        body["reason"] = reason
    body.update(payload)
    return body


def _open_custody_for_customer(actor: AuthContext) -> CustodySession | None:
    """The customer's currently open custody session, most recent first.

    Staff see whichever session they ask about, but this endpoint is the
    *customer* dashboard, so for a customer it is scoped to their own tickets by
    the query itself rather than by a check afterwards.
    """
    statement = (
        select(CustodySession)
        .join(ValetTicket, ValetTicket.id == CustodySession.ticket_id)
        .where(CustodySession.status.in_(OPEN_STATUSES))
    )
    if not is_staff(actor):
        statement = statement.where(ValetTicket.customer_id == uuid.UUID(actor.user_id))
    statement = statement.order_by(CustodySession.dropped_off_at.desc()).limit(1)
    return db.session.execute(statement).scalars().first()


def _location_section(session: CustodySession, actor: AuthContext) -> dict[str, Any]:
    """Where the car is, from the most recent GPS fix.

    Delegates to ``tracking_view``, which is the same code path the live tracking
    dashboard uses and which enforces tracking access. Reusing it means a fix to
    the ownership rule there fixes it here too.
    """
    try:
        view = geofence_service.tracking_view(session.id, actor)
    except Exception as exc:  # noqa: BLE001 - one card must not sink the dashboard
        logger.warning("Dashboard location unavailable: %s", exc)
        return _section("unavailable", reason="Location could not be read.")

    latest = view.get("latest_fix")
    if not latest:
        return _section(
            "empty",
            reason="No GPS fix has been recorded for this custody session yet.",
            zone=view.get("zone"),
        )

    return _section(
        "ok",
        fix=latest,
        latitude=latest.get("latitude"),
        longitude=latest.get("longitude"),
        recorded_at=latest.get("recorded_at"),
        movement_status=view.get("movement_status"),
        zone=view.get("zone"),
        fix_count=view.get("fix_count"),
        # Provenance travels with the reading. A simulated track must never be
        # presented as a hardware position, so the dashboard says which it is.
        telemetry_sources=view.get("sources"),
        has_simulated_telemetry=view.get("has_simulated_telemetry"),
        telemetry_note=view.get("telemetry_note"),
        open_alert_count=view.get("open_alert_count"),
    )


def _security_section(session: CustodySession, actor: AuthContext) -> dict[str, Any]:
    """Security posture: the movement verdict plus what is actually open.

    An alert count on its own is not a security status, and a movement verdict
    on its own ignores alerts that are still unresolved. Both are shown, and the
    headline status is the worse of the two rather than an average.
    """
    try:
        listed = geofence_service.list_alerts(
            actor, custody_session_id=str(session.id), page_size=20
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dashboard alerts unavailable: %s", exc)
        return _section("unavailable", reason="Alerts could not be read.")

    # `list_alerts` returns its rows under "alerts", not "items". Reading the
    # wrong key does not raise - it silently yields an empty list, which is how
    # this card reported `open_alert_count: 0` beside a real HIGH breach alert.
    # A security count that can be wrong in the reassuring direction is worse
    # than one that fails loudly, so index it rather than defaulting it.
    alerts = listed["alerts"]
    open_statuses = _open_alert_statuses()
    open_alerts = [alert for alert in alerts if alert.get("status") in open_statuses]

    location = _movement_status_of(session)
    severities = [alert.get("severity") for alert in open_alerts]
    headline = _worst_security_state(location, severities)

    return _section(
        "ok",
        status=headline,
        movement_status=location,
        alerts=alerts[:5],
        open_alert_count=len(open_alerts),
        highest_severity=_worst_severity(severities),
        summary=_security_summary(headline, location, len(open_alerts)),
    )


def _open_alert_statuses() -> tuple[str, ...]:
    return (
        AlertStatus.OPEN.value,
        AlertStatus.ACKNOWLEDGED.value,
        AlertStatus.INVESTIGATING.value,
    )


def _movement_status_of(session: CustodySession) -> str | None:
    """The movement verdict on the most recent fix, read directly.

    ``tracking_view`` already computes this, but calling it a second time for
    the security card would re-read the whole track. One indexed query is enough.
    """
    from models.gps_event import GpsEvent

    row = db.session.execute(
        select(GpsEvent.movement_status)
        .where(GpsEvent.custody_session_id == session.id)
        .order_by(GpsEvent.recorded_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _worst_severity(severities: list[str | None]) -> str | None:
    ranked = [s for s in severities if s in _SEVERITY_RANK]
    if not ranked:
        return None
    return max(ranked, key=lambda value: _SEVERITY_RANK[value])


def _worst_security_state(movement: str | None, severities: list[str | None]) -> str:
    """The headline posture, taken as the worse of movement and open alerts.

    Averaging would be wrong in the dangerous direction: a car inside its
    geofence with an unresolved CRITICAL alert is not "mostly fine".
    """
    if movement == "BREACH" or _SEVERITY_RANK.get(_worst_severity(severities) or "", 0) >= 3:
        return "BREACH"
    if movement in ("SUSPICIOUS", "WARNING") or _worst_severity(severities):
        return "ATTENTION"
    if movement == "NORMAL":
        return "SECURE"
    return "UNKNOWN"


def _security_summary(headline: str, movement: str | None, open_count: int) -> str:
    if headline == "UNKNOWN":
        return "No telemetry has been recorded, so there is no security verdict yet."
    if headline == "BREACH":
        return "A geofence breach or high-severity alert needs attention."
    if headline == "ATTENTION":
        detail = f"{open_count} open alert(s)" if open_count else "unusual movement"
        return f"Movement flagged for review ({detail})."
    return f"Vehicle inside its zone; latest movement verdict {movement}."


def _condition_section(session: CustodySession, actor: AuthContext) -> dict[str, Any]:
    """The condition report, summarised.

    The full report is large and the dashboard only needs the headline, so the
    counts are taken from the same service the report page uses and the
    detector's own disclaimer is passed through rather than paraphrased.
    """
    try:
        report = inspection_service.get_inspection_report(actor, session.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dashboard condition unavailable: %s", exc)
        return _section("unavailable", reason="Condition report could not be read.")

    drop_off = report.get("drop_off")
    if not drop_off:
        return _section(
            "empty",
            reason="No drop-off inspection has been captured for this session.",
        )

    total = report.get("total_detections", 0)
    unverified = report.get("unverified_count", 0)
    confirmed = report.get("confirmed_count", 0)

    return _section(
        "ok",
        total_detections=total,
        unverified_count=unverified,
        confirmed_count=confirmed,
        requires_review=report.get("requires_review", False),
        comparison_ready=report.get("comparison_ready", False),
        pickup_completed=report.get("pickup") is not None,
        status="REVIEW" if unverified else ("DAMAGE_CONFIRMED" if confirmed else "CLEAN"),
        summary=(
            f"{unverified} unverified finding(s) awaiting confirmation."
            if unverified
            else (
                f"{confirmed} confirmed finding(s)."
                if confirmed
                else "No damage detected against the drop-off photographs."
            )
        ),
        # Carried through verbatim: the detector is classical image differencing,
        # not a trained classifier, and the dashboard must not imply otherwise.
        detector_disclaimer=report.get("detector_disclaimer"),
    )


def _retrieval_section(session: CustodySession) -> dict[str, Any]:
    """The stored retrieval estimate, if one has been generated."""
    try:
        prediction = retrieval_service.latest_prediction(str(session.id))
    except Exception:  # noqa: BLE001 - absent is normal before a request is made
        return _section(
            "empty",
            reason="No retrieval estimate yet. One is generated when you request the vehicle.",
        )

    delay = int(prediction.get("dispatch_delay_minutes") or 0)
    return _section(
        "ok",
        prediction=prediction,
        estimated_minutes=prediction.get("predicted_wait_minutes"),
        min_wait_minutes=prediction.get("min_wait_minutes"),
        max_wait_minutes=prediction.get("max_wait_minutes"),
        queue_position=prediction.get("queue_position"),
        predicted_ready_at=prediction.get("predicted_ready_at"),
        dispatch_after=prediction.get("dispatch_after"),
        recommended_dispatch=prediction.get("dispatch_label") or "Now",
        dispatch_delay_minutes=delay,
        confidence=prediction.get("confidence"),
        status=prediction.get("status"),
        # The estimate is from a model trained on synthetic data, and the
        # dashboard is the most screenshot-able surface in the product, so the
        # disclosure is part of the section rather than left to the card.
        trained_on_synthetic_data=True,
        disclaimer=prediction.get("disclaimer"),
    )


def _action_section(session: CustodySession) -> dict[str, Any]:
    """Whether the customer may request the vehicle right now.

    Computed from the custody state machine rather than assumed, so the button
    is disabled for the same reason the backend would refuse the request.
    Showing an enabled button that 409s is worse than a greyed one that explains
    itself.
    """
    status = session.status
    if status in RETRIEVAL_ALREADY_ACTIVE:
        return _section(
            "empty",
            reason="A retrieval is already in progress for this vehicle.",
            can_request=False,
            custody_status=status,
        )
    allowed = ticket_service.ALLOWED_CUSTODY_TRANSITIONS.get(status, ())
    can_request = CustodyStatus.RETRIEVAL_REQUESTED.value in allowed
    return _section(
        "ok" if can_request else "empty",
        reason=None if can_request else f"Cannot request a retrieval from status {status}.",
        can_request=can_request,
        custody_status=status,
        already_requested=session.retrieval_requested_at is not None,
    )


def build_dashboard(actor: AuthContext) -> dict[str, Any]:
    """Compose the whole customer dashboard in one snapshot."""
    session = _open_custody_for_customer(actor)

    if session is None:
        # A real answer, not a failure: this customer has nothing in custody.
        return {
            "has_active_custody": False,
            "custody": _section(
                "empty",
                reason="No vehicle is currently in valet custody.",
            ),
            "vehicle": _section("empty", reason="No vehicle is in custody."),
            "location": _section("empty", reason="No vehicle is in custody."),
            "security": _section("empty", reason="No vehicle is in custody."),
            "condition": _section("empty", reason="No vehicle is in custody."),
            "retrieval": _section("empty", reason="No vehicle is in custody."),
            "actions": _section(
                "empty",
                reason="Check a vehicle in to see it here.",
                can_request=False,
            ),
        }

    ticket = session.ticket
    vehicle = session.vehicle
    zone = session.zone

    ticket_payload = (
        ticket_service.ticket_dict(ticket, qr_url=None) if ticket is not None else None
    )

    return {
        "has_active_custody": True,
        "custody_session_id": str(session.id),
        "custody": _section(
            "ok",
            custody_session_id=str(session.id),
            status=session.status,
            # Both vocabularies, so the projection is visible rather than
            # something a reviewer has to take on trust.
            projected_status=ticket_payload.get("status") if ticket_payload else None,
            stored_status=ticket_payload.get("stored_status") if ticket_payload else None,
            ticket_code=ticket.ticket_code if ticket else None,
            ticket_id=str(ticket.id) if ticket else None,
            checked_in_at=(
                session.dropped_off_at.isoformat() if session.dropped_off_at else None
            ),
            retrieval_requested_at=(
                session.retrieval_requested_at.isoformat()
                if session.retrieval_requested_at
                else None
            ),
            ready_for_pickup_at=(
                session.ready_for_pickup_at.isoformat()
                if session.ready_for_pickup_at
                else None
            ),
            expected_retrieval_minutes=session.expected_retrieval_minutes,
            handover_point=session.handover_point,
        ),
        "vehicle": _section(
            "ok" if vehicle else "empty",
            reason=None if vehicle else "This session has no vehicle record.",
            id=str(vehicle.id) if vehicle else None,
            plate_number=vehicle.plate_number if vehicle else None,
            make=vehicle.make if vehicle else None,
            model=vehicle.model if vehicle else None,
            color=vehicle.color if vehicle else None,
            status=vehicle.status if vehicle else None,
        ),
        "zone": _section(
            "ok" if zone else "empty",
            reason=None if zone else "No zone has been assigned to this vehicle yet.",
            id=str(zone.id) if zone else None,
            code=zone.code if zone else None,
            name=zone.name if zone else None,
            capacity=zone.capacity if zone else None,
            current_occupancy=zone.current_occupancy if zone else None,
        ),
        "location": _location_section(session, actor),
        "security": _security_section(session, actor),
        "condition": _condition_section(session, actor),
        "retrieval": _retrieval_section(session),
        "actions": _action_section(session),
    }


def pending_retrievals(actor: AuthContext, *, limit: int = 25) -> dict[str, Any]:
    """The retrieval queue staff act on.

    This is what the valet dashboard polls. It is deliberately a plain query
    rather than a push channel: the project has no websocket or SSE
    infrastructure, and a poll that is honest about being a poll is better than a
    notification path that silently drops events.

    Ordered oldest first, because that is the order the queue is served in.
    """
    statement = (
        select(CustodySession)
        .where(
            CustodySession.status.in_(
                [
                    CustodyStatus.RETRIEVAL_REQUESTED.value,
                    CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
                ]
            ),
            CustodySession.retrieval_requested_at.is_not(None),
        )
        .order_by(CustodySession.retrieval_requested_at.asc())
        .limit(limit)
    )
    sessions = db.session.execute(statement).scalars().all()

    items: list[dict[str, Any]] = []
    for position, session in enumerate(sessions, start=1):
        prediction = _latest_prediction_row(session.id)
        items.append(
            {
                "queue_position": position,
                "custody_session_id": str(session.id),
                "custody_status": session.status,
                "requested_at": (
                    session.retrieval_requested_at.isoformat()
                    if session.retrieval_requested_at
                    else None
                ),
                "started_at": (
                    session.retrieval_started_at.isoformat()
                    if session.retrieval_started_at
                    else None
                ),
                "vehicle": (
                    {
                        "id": str(session.vehicle.id),
                        "plate_number": session.vehicle.plate_number,
                        "make": session.vehicle.make,
                        "model": session.vehicle.model,
                    }
                    if session.vehicle
                    else None
                ),
                "zone": (
                    {"id": str(session.zone.id), "code": session.zone.code}
                    if session.zone
                    else None
                ),
                "assigned_valet_id": (
                    str(session.assigned_valet_id) if session.assigned_valet_id else None
                ),
                "estimated_minutes": (
                    prediction.predicted_wait_minutes if prediction else None
                ),
                "estimated_ready_at": (
                    prediction.predicted_ready_at.isoformat()
                    if prediction and prediction.predicted_ready_at
                    else None
                ),
                "prediction_status": prediction.status if prediction else None,
                # So the valet can tell a waiting customer from one whose car is
                # already on its way.
                "in_progress": session.status == CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
            }
        )

    return {
        "items": items,
        "count": len(items),
        "waiting": sum(1 for item in items if not item["in_progress"]),
        # Stated so the UI can label itself truthfully.
        "delivery": "polled",
        "delivery_note": (
            "This queue is read on a short poll rather than pushed. There is no "
            "websocket channel, so a new request appears here within one poll "
            "interval, not instantly."
        ),
    }


def _latest_prediction_row(custody_id: uuid.UUID) -> RetrievalPrediction | None:
    """The estimate the queue should quote for this session.

    Uses the shared ordering from the retrieval service rather than a local one:
    the valet's queue and the customer's card must agree on which prediction is
    current, and two independently written queries are how they stop agreeing.
    """
    return (
        db.session.execute(
            select(RetrievalPrediction)
            .where(RetrievalPrediction.custody_session_id == custody_id)
            .order_by(*retrieval_service.prediction_recency_order(RetrievalPrediction))
            .limit(1)
        )
        .scalars()
        .first()
    )


def open_alert_count_for(actor: AuthContext) -> int:
    """How many unresolved alerts this actor may see, for a badge."""
    try:
        listed = geofence_service.list_alerts(actor, page_size=100)
    except Exception:  # noqa: BLE001
        return 0
    return sum(
        1 for alert in listed["alerts"] if alert.get("status") in _open_alert_statuses()
    )


__all__ = [
    "build_dashboard",
    "open_alert_count_for",
    "pending_retrievals",
]
