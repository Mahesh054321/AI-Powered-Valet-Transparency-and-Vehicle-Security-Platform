"""Read side of the audit trail: the manager/admin viewer and the custody timeline.

Two views over one append-only table, because they answer two different
questions.

:func:`list_events` is the **log viewer** -- "show me everything that happened,
newest first, filtered by actor or action or date". It is deliberately
read-only and deliberately restricted to managers and administrators. An
operator has no business reading the trail of their colleagues' actions, and a
customer must never see anyone else's; the log contains email addresses, IP
addresses and the internal state of other people's vehicles.

:func:`custody_timeline` is the **story of one vehicle** -- the fourteen
lifecycle events in the order they occurred, from login to custody closed. This
is the view that answers the question the whole project exists to answer: what
happened to my car, and who did it?

**Nothing here writes.** The table is append-only at the database level (see
migration 0008); this module has no session.add and no commit. There is no
update or delete function to call, which is the point -- the UI cannot offer a
control that does not exist.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.extensions import db
from models.audit_log import LIFECYCLE_ACTIONS, AuditAction, AuditLog
from models.custody_session import CustodySession
from models.vehicle import Vehicle
from utils.exceptions import AuthorizationError, NotFoundError, ValidationError
from utils.pagination import PaginationParams
from utils.security import AuthContext, Role

#: Who may read the trail. Deliberately narrow: this is an oversight view, not
#: an operational one, and it exposes other people's email addresses and IP
#: addresses.
AUDIT_VIEW_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)

#: Human-readable labels for the lifecycle, in the order a reviewer reads them.
#: Kept here rather than in the frontend so the API is self-describing and the
#: two cannot drift apart.
LIFECYCLE_LABELS: dict[str, str] = {
    AuditAction.USER_LOGIN.value: "User logged in",
    AuditAction.VEHICLE_CREATED.value: "Vehicle registered",
    AuditAction.TICKET_CREATED.value: "Ticket issued",
    AuditAction.VEHICLE_CHECKED_IN.value: "Vehicle checked in",
    AuditAction.IMAGE_UPLOADED.value: "Images uploaded",
    AuditAction.CUSTODY_STARTED.value: "Custody started",
    AuditAction.GPS_BREACH.value: "GPS breach",
    AuditAction.SECURITY_ALERT_CREATED.value: "Security alert raised",
    AuditAction.PICKUP_REQUESTED.value: "Pickup requested",
    AuditAction.RETRIEVAL_PREDICTED.value: "Retrieval estimate produced",
    AuditAction.PICKUP_IMAGE_CAPTURED.value: "Pickup images captured",
    AuditAction.DAMAGE_DETECTED.value: "Potential damage detected",
    AuditAction.VEHICLE_PICKED_UP.value: "Vehicle picked up",
    AuditAction.CUSTODY_COMPLETED.value: "Custody completed",
}

_MAX_PAGE_SIZE = 200


def _require_viewer(actor: AuthContext) -> None:
    if actor.role not in AUDIT_VIEW_ROLES:
        raise AuthorizationError(
            f"Role '{actor.role}' cannot read the audit trail. "
            f"Required: {', '.join(AUDIT_VIEW_ROLES)}.",
            details={"role": actor.role, "required": list(AUDIT_VIEW_ROLES)},
        )


def _parse_uuid(value: str | None, *, field: str) -> uuid.UUID | None:
    if value is None or not str(value).strip():
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(
            f"'{field}' must be a valid UUID.", details={"field": field}
        ) from exc


def _parse_datetime(value: str | None, *, field: str) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"'{field}' must be an ISO-8601 timestamp.", details={"field": field}
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _entry_dict(entry: AuditLog) -> dict[str, Any]:
    """One log entry as the viewer renders it."""
    payload = entry.to_dict()
    payload["is_lifecycle_event"] = entry.action in LIFECYCLE_LABELS
    payload["label"] = LIFECYCLE_LABELS.get(entry.action, entry.action)
    return payload


def _filters(
    *,
    action: str | None,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    custody_session_id: uuid.UUID | None,
    outcome: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    lifecycle_only: bool,
) -> list[Any]:
    clauses: list[Any] = []
    if action:
        clauses.append(AuditLog.action == action.upper())
    if entity_type:
        clauses.append(AuditLog.entity_type == entity_type.strip())
    if entity_id is not None:
        clauses.append(AuditLog.entity_id == entity_id)
    if actor_id is not None:
        clauses.append(AuditLog.actor_id == actor_id)
    if custody_session_id is not None:
        clauses.append(AuditLog.custody_session_id == custody_session_id)
    if outcome:
        clauses.append(AuditLog.outcome == outcome.upper())
    if date_from is not None:
        clauses.append(AuditLog.occurred_at >= date_from)
    if date_to is not None:
        clauses.append(AuditLog.occurred_at <= date_to)
    if lifecycle_only:
        clauses.append(AuditLog.action.in_(LIFECYCLE_ACTIONS))
    return clauses


def list_events(
    actor: AuthContext,
    *,
    pagination: PaginationParams,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
    custody_session_id: str | None = None,
    outcome: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    lifecycle_only: bool = False,
) -> dict[str, Any]:
    """A page of audit entries, newest first.

    Every filter narrows the same query that produces the count, so the
    pagination total always describes the set actually being paged through.
    """
    _require_viewer(actor)

    if pagination.page_size > _MAX_PAGE_SIZE:
        raise ValidationError(
            f"'page_size' must not exceed {_MAX_PAGE_SIZE}.",
            details={"field": "page_size", "maximum": _MAX_PAGE_SIZE},
        )

    clauses = _filters(
        action=action,
        entity_type=entity_type,
        entity_id=_parse_uuid(entity_id, field="entity_id"),
        actor_id=_parse_uuid(actor_id, field="actor_id"),
        custody_session_id=_parse_uuid(custody_session_id, field="custody_session_id"),
        outcome=outcome,
        date_from=_parse_datetime(date_from, field="date_from"),
        date_to=_parse_datetime(date_to, field="date_to"),
        lifecycle_only=lifecycle_only,
    )

    total = db.session.execute(
        select(func.count()).select_from(AuditLog).where(*clauses)
    ).scalar_one()

    rows = db.session.execute(
        # Ordered by the insertion sequence, not the timestamp. A single request
        # writes several entries inside one transaction and they share an
        # ``occurred_at``, so a timestamp sort falls back to a random UUID and
        # the same row can appear on two pages while another is skipped.
        select(AuditLog)
        .where(*clauses)
        .order_by(AuditLog.seq.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    ).scalars().all()

    return {
        "items": [_entry_dict(row) for row in rows],
        "pagination": {
            "page": pagination.page,
            "page_size": pagination.page_size,
            "total": int(total),
            "pages": (int(total) + pagination.page_size - 1) // pagination.page_size,
        },
        # Stated on every response so a client cannot mistake this for a live
        # feed or offer controls the table does not support.
        "mutable": False,
        "mutability_note": (
            "The audit trail is append-only. There is no update or delete "
            "endpoint, and the database rejects both at the trigger level."
        ),
        "filters": {
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor_id": actor_id,
            "custody_session_id": custody_session_id,
            "outcome": outcome,
            "date_from": date_from,
            "date_to": date_to,
            "lifecycle_only": lifecycle_only,
        },
        "vocabulary": {
            "lifecycle": LIFECYCLE_LABELS,
            "actions": sorted(str(a.value) for a in AuditAction),
        },
    }


def filter_options(actor: AuthContext) -> dict[str, Any]:
    """The distinct values present in the log, so the viewer can build its dropdowns.

    Read from the data rather than hardcoded: a filter offering an action that
    has never occurred is noise, and one missing an action that has is a bug.
    """
    _require_viewer(actor)

    def _distinct(column: Any) -> list[str]:
        rows = db.session.execute(
            select(column).where(column.is_not(None)).distinct().order_by(column)
        ).scalars().all()
        return [str(value) for value in rows]

    return {
        "actions": _distinct(AuditLog.action),
        "entity_types": _distinct(AuditLog.entity_type),
        "outcomes": _distinct(AuditLog.outcome),
        "lifecycle_actions": list(LIFECYCLE_ACTIONS),
    }


def _load_custody(custody_id: uuid.UUID | str) -> CustodySession:
    session = db.session.get(CustodySession, custody_id)
    if session is None:
        raise NotFoundError(
            "No custody session was found with that id.",
            details={"custody_session_id": str(custody_id)},
        )
    return session


def custody_timeline(
    actor: AuthContext, custody_id: uuid.UUID | str
) -> dict[str, Any]:
    """The chronological story of one custody, oldest first.

    Oldest first is the opposite of the log viewer, and deliberately so: a
    timeline is read as a narrative -- checked in, parked, requested, handed
    back -- and reversing it would make the reader reconstruct the order
    mentally on every visit.
    """
    _require_viewer(actor)
    session = _load_custody(custody_id)
    return _build_timeline(session)


def vehicle_timelines(actor: AuthContext, vehicle_id: uuid.UUID | str) -> dict[str, Any]:
    """Every custody timeline for one vehicle, most recent custody first.

    The brief asks for a chronological custody timeline *for each vehicle*. A
    vehicle is held many times, so this returns one timeline per custody
    session rather than flattening them: interleaving two separate visits into
    a single sequence would produce a story that never happened.
    """
    _require_viewer(actor)
    resolved = _parse_uuid(str(vehicle_id), field="vehicle_id")
    vehicle = db.session.get(Vehicle, resolved)
    if vehicle is None:
        raise NotFoundError(
            "No vehicle was found with that id.", details={"vehicle_id": str(vehicle_id)}
        )

    sessions = db.session.execute(
        select(CustodySession)
        .where(CustodySession.vehicle_id == vehicle.id)
        .order_by(CustodySession.dropped_off_at.desc())
    ).scalars().all()

    return {
        "vehicle": {
            "id": str(vehicle.id),
            "plate_number": vehicle.plate_number,
            "make": vehicle.make,
            "model": vehicle.model,
            "color": vehicle.color,
            "status": vehicle.status,
        },
        "custody_count": len(sessions),
        "timelines": [_build_timeline(session) for session in sessions],
    }


def _build_timeline(session: CustodySession) -> dict[str, Any]:
    """Assemble one custody's events into a readable sequence.

    Three event groups are gathered, because the custody window is not the
    whole story:

    * **custody** -- entries stamped with this session's id. The bulk of the
      timeline.
    * **provenance** -- ``TICKET_CREATED`` and ``VEHICLE_CREATED`` cannot carry
      a ``custody_session_id``, because at the moment they are written no
      session exists yet. They are reached through the ticket and vehicle this
      custody belongs to instead. Without them the timeline would begin at
      check-in and could not answer "when was this car registered?".
    * **logins** -- ``USER_LOGIN`` is about a person, not a vehicle, so it is
      scoped twice over: only the actors who actually appear in this custody,
      and only between the ticket being issued and the custody closing. An
      unbounded login list would bury the custody in unrelated sessions.

    Each event is labelled with the group it came from, so a reader can tell an
    event of the custody from context about it.
    """
    core = db.session.execute(
        select(AuditLog)
        .where(AuditLog.custody_session_id == session.id)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()

    clauses: list[Any] = []
    if session.ticket_id is not None:
        clauses.append(
            and_(
                AuditLog.entity_type == "valet_tickets",
                AuditLog.entity_id == session.ticket_id,
            )
        )
    if session.vehicle_id is not None:
        clauses.append(
            and_(
                AuditLog.entity_type == "vehicles",
                AuditLog.entity_id == session.vehicle_id,
            )
        )
    provenance = (
        db.session.execute(
            select(AuditLog).where(or_(*clauses)).order_by(AuditLog.seq.asc())
        )
        .scalars()
        .all()
        if clauses
        else []
    )

    # Logins are about a person, not a vehicle, so they cannot simply be
    # swept in by id. Two bounded groups are included, and nothing else:
    #
    #   * the session each participant *arrived in* -- their most recent login
    #     at or before the custody opened. This is the login the brief's step 1
    #     refers to, and it necessarily predates the ticket, which is why the
    #     window cannot start at issuance.
    #   * any login by a participant *during* the custody, e.g. the customer
    #     signing in to request their car back.
    #
    # An unbounded login list would bury this custody under every unrelated
    # session those people ever opened.
    ticket = session.ticket
    custody_start = session.dropped_off_at or (ticket.issued_at if ticket else None)
    custody_end = session.picked_up_at

    actor_ids = {row.actor_id for row in core if row.actor_id is not None}
    if ticket is not None and ticket.customer_id is not None:
        actor_ids.add(ticket.customer_id)

    logins: list[AuditLog] = []
    if actor_ids and custody_start is not None:
        for actor_id in actor_ids:
            arriving = db.session.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == AuditAction.USER_LOGIN.value,
                    AuditLog.actor_id == actor_id,
                    AuditLog.occurred_at <= custody_start,
                )
                .order_by(AuditLog.seq.desc())
                .limit(1)
            ).scalars().first()
            if arriving is not None:
                logins.append(arriving)

        during_query = select(AuditLog).where(
            AuditLog.action == AuditAction.USER_LOGIN.value,
            AuditLog.actor_id.in_(actor_ids),
            AuditLog.occurred_at > custody_start,
        )
        if custody_end is not None:
            during_query = during_query.where(AuditLog.occurred_at <= custody_end)
        logins.extend(db.session.execute(during_query).scalars().all())

    grouped: list[tuple[int, AuditLog, str]] = []
    for row in core:
        grouped.append((row.seq, row, "custody"))
    for row in provenance:
        grouped.append((row.seq, row, "provenance"))
    for row in logins:
        grouped.append((row.seq, row, "login"))
    grouped.sort(key=lambda item: item[0])

    events = []
    for _seq, row, scope in grouped:
        payload = _entry_dict(row)
        payload["scope"] = scope
        events.append(payload)

    present = {row.action for row in core} | {row.action for row in provenance} | {
        row.action for row in logins
    }
    breach = next(
        (row for row in core if row.action == AuditAction.GPS_BREACH.value), None
    )

    return {
        "custody_session_id": str(session.id),
        "status": session.status,
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
        "dropped_off_at": (
            session.dropped_off_at.isoformat() if session.dropped_off_at else None
        ),
        "picked_up_at": session.picked_up_at.isoformat() if session.picked_up_at else None,
        "event_count": len(events),
        "events": events,
        # Which of the fourteen actually happened. A reviewer scanning a
        # timeline wants to see the gaps -- no pickup image, no damage check --
        # rather than infer them from what is present.
        "lifecycle_progress": {
            action: action in present for action in LIFECYCLE_ACTIONS
        },
        "lifecycle_missing": [a for a in LIFECYCLE_ACTIONS if a not in present],
        "had_security_incident": breach is not None,
        # Stated so the reader knows the timeline reaches beyond the session
        # row, and why -- it is not padding.
        "scope_note": (
            "Includes ticket and vehicle registration events that predate the "
            "custody session, and logins by its participants within the custody "
            "window. Each event is labelled with its scope."
        ),
    }


__all__ = [
    "AUDIT_VIEW_ROLES",
    "LIFECYCLE_LABELS",
    "custody_timeline",
    "filter_options",
    "list_events",
    "vehicle_timelines",
]
