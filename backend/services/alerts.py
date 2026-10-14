"""The centralized alert feed: create, list, mark read, resolve.

**Role scoping is a filter on the query, not a filter on the response.** Each
actor sees a different slice, and the slice is decided in SQL. Filtering after
fetching would mean a customer's request still loaded every alert in the system
and then threw most of them away — which is both slow and one forgotten
predicate away from leaking a security alert to the wrong person.

**Read state is per recipient.** ``mark_read`` writes a row in ``alert_reads``
for the calling user only. Marking an alert read never changes the alert itself,
so a customer opening the app cannot clear a CRITICAL breach from the manager's
unread count.

**Resolution is manager-only.** An operator can read and act on an alert, but
closing it is a decision that should be attributable to someone accountable —
the same split the damage-verification flow already uses.

**Duplication is prevented at creation.** One alert per (source row, category),
enforced by a partial unique index. Without it, every GPS fix on a breaching car
would raise a fresh alert and bury the one real event under hundreds of copies.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select

from app.extensions import db
from models.alert import Alert, AlertAudience, AlertCategory, AlertRead, AlertStatus, category_for
from models.audit_log import AuditAction, AuditOutcome
from models.enums import AlertSeverity
from models.vehicle import Vehicle
from services.notifications import dispatch
from services.tickets import _audit  # noqa: PLC2701 - shared audit helper
from utils.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from utils.security import AuthContext, Role

logger = logging.getLogger(__name__)

#: Who may close an alert. Deliberately narrower than who may read one.
TRIAGE_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)

#: Roles that count as staff for scoping purposes.
STAFF_ROLES = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)

VALID_STATUSES = tuple(member.value for member in AlertStatus)
VALID_CATEGORIES = tuple(member.value for member in AlertCategory)
VALID_SEVERITIES = tuple(member.value for member in AlertSeverity)


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: Worst first, so someone scanning the feed meets the thing that needs them at
#: the top. Written out explicitly rather than derived from the enum, so the
#: ordering cannot silently change if the enum is ever reordered.
_SEVERITY_RANK = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _severity_rank(column):
    """A CASE expression ordering a severity column worst-first."""
    return case(
        *[(column == value, index) for index, value in enumerate(_SEVERITY_RANK)],
        else_=len(_SEVERITY_RANK),
    )


def is_staff(actor: AuthContext) -> bool:
    return actor.role in STAFF_ROLES


def is_manager(actor: AuthContext) -> bool:
    return actor.role in TRIAGE_ROLES


def _actor_id(actor: AuthContext) -> uuid.UUID:
    return uuid.UUID(actor.user_id)


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------
def _visibility_conditions(actor: AuthContext) -> list:
    """The predicate that decides which alerts ``actor`` may see.

    Applied to every list and count query so the two can never disagree — a
    badge saying "3 unread" above a list showing five is the kind of
    inconsistency that makes people stop trusting the number.
    """
    role = actor.role

    if role in (Role.VALET_MANAGER.value, Role.ADMIN.value):
        # Management sees the whole feed. An incident hidden from the person
        # accountable for it is worse than a noisy inbox.
        return []

    if role == Role.VALET_OPERATOR.value:
        return [Alert.audiences.contains([AlertAudience.VALET_OPERATOR.value])]

    if role == Role.CUSTOMER.value:
        # Only alerts about a vehicle they own, and only if the alert was
        # addressed to customers at all. An internal operational alert is not
        # the customer's business even when it concerns their car.
        owned = select(Vehicle.id).where(Vehicle.owner_id == _actor_id(actor))
        return [
            Alert.audiences.contains([AlertAudience.CUSTOMER.value]),
            Alert.vehicle_id.in_(owned),
        ]

    raise AuthorizationError(
        "This role cannot read the alert feed.", details={"role": role}
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def create_alert(
    *,
    category: str | AlertCategory,
    message: str,
    severity: str | AlertSeverity = AlertSeverity.MEDIUM,
    custody_session_id: uuid.UUID | str | None = None,
    vehicle_id: uuid.UUID | str | None = None,
    audiences: list[str] | None = None,
    source_entity_type: str | None = None,
    source_id: uuid.UUID | str | None = None,
    source_type: str | None = None,
    actor: AuthContext | None = None,
    notify: bool = True,
) -> tuple[Alert | None, bool]:
    """Raise an alert and notify whoever should hear about it.

    Returns ``(alert, created)``. ``created`` is ``False`` when an alert for the
    same source and category already exists, which is the normal case for a
    condition that persists across several readings — the caller gets the
    existing row back rather than a duplicate.

    Callers should not need to know about notifications. Passing ``notify=False``
    is for tests and for bulk backfills where dispatching thousands of
    notifications would be noise.
    """
    category_value = category.value if isinstance(category, AlertCategory) else str(category)
    if category_value not in VALID_CATEGORIES:
        raise ValidationError(
            f"'type' must be one of {list(VALID_CATEGORIES)}.",
            details={"field": "type", "allowed": list(VALID_CATEGORIES)},
        )

    severity_value = severity.value if isinstance(severity, AlertSeverity) else str(severity)
    if severity_value not in VALID_SEVERITIES:
        raise ValidationError(
            f"'severity' must be one of {list(VALID_SEVERITIES)}.",
            details={"field": "severity", "allowed": list(VALID_SEVERITIES)},
        )

    if not message or not message.strip():
        raise ValidationError("An alert must carry a message.", details={"field": "message"})

    audience_values = [str(a) for a in (audiences or [])]
    if not audience_values:
        # An alert addressed to nobody is written and then read by nobody.
        # Refusing is better than silently creating a row no one will see.
        raise ValidationError(
            "An alert must be addressed to at least one audience.",
            details={"field": "audiences", "allowed": [a.value for a in AlertAudience]},
        )
    for value in audience_values:
        if value not in {a.value for a in AlertAudience}:
            raise ValidationError(
                f"Unknown audience {value!r}.",
                details={"field": "audiences", "allowed": [a.value for a in AlertAudience]},
            )

    source_uuid = uuid.UUID(str(source_id)) if source_id else None

    # De-duplicate before inserting. The unique index would catch it, but
    # catching it here means the caller gets the existing alert back instead of
    # an IntegrityError to interpret.
    if source_uuid is not None and source_entity_type:
        existing = db.session.execute(
            select(Alert).where(
                Alert.source_entity_type == source_entity_type,
                Alert.source_id == source_uuid,
                Alert.type == category_value,
            )
        ).scalars().first()
        if existing is not None:
            return existing, False

    alert = Alert(
        custody_session_id=uuid.UUID(str(custody_session_id)) if custody_session_id else None,
        vehicle_id=uuid.UUID(str(vehicle_id)) if vehicle_id else None,
        type=category_value,
        severity=severity_value,
        status=AlertStatus.OPEN.value,
        message=message.strip()[:500],
        source_entity_type=source_entity_type,
        source_id=source_uuid,
        source_type=source_type,
        audiences=audience_values,
        raised_by_id=_actor_id(actor) if actor else None,
    )
    db.session.add(alert)
    db.session.flush()

    _audit(
        action=str(AuditAction.SECURITY_ALERT_CREATED),
        actor_context=actor,
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="alerts",
        entity_id=alert.id,
        description=f"Raised {category_value} alert ({severity_value}): {alert.message[:200]}",
    )

    summary = None
    if notify:
        summary = dispatch(alert)

    db.session.commit()
    db.session.refresh(alert)

    if summary is not None:
        logger.info(
            "alert %s dispatched to %d recipient(s), %d delivered",
            alert.id,
            summary.recipients,
            summary.delivered,
        )

    return alert, True


def raise_from_security_alert(security_alert, *, actor: AuthContext | None = None):
    """Project a geofence ``SecurityAlert`` into the unified feed.

    The precise detector type is preserved in ``source_type``; the five-category
    projection is what users see. Both are kept because the projection is lossy
    by design and the original must stay recoverable.
    """
    category = category_for(security_alert.alert_type)
    return create_alert(
        category=category,
        message=security_alert.title or security_alert.reason or category.value,
        severity=security_alert.severity,
        custody_session_id=security_alert.custody_session_id,
        vehicle_id=security_alert.vehicle_id,
        audiences=[
            AlertAudience.VALET_OPERATOR.value,
            AlertAudience.VALET_MANAGER.value,
            AlertAudience.CUSTOMER.value,
        ],
        source_entity_type="security_alerts",
        source_id=security_alert.id,
        source_type=security_alert.alert_type,
        actor=actor,
    )


def raise_potential_damage(detection, *, actor: AuthContext | None = None):
    """Project an unverified damage finding into the feed.

    Wording matters: this says *potential*, because the detector flagged it and
    nobody has confirmed it. Only a manager's verification turns it into damage.
    """
    from models.damage_detection import DamageSeverity

    severity_map = {
        DamageSeverity.COSMETIC.value: AlertSeverity.LOW.value,
        DamageSeverity.MINOR.value: AlertSeverity.LOW.value,
        DamageSeverity.MODERATE.value: AlertSeverity.MEDIUM.value,
        DamageSeverity.SEVERE.value: AlertSeverity.HIGH.value,
    }
    inspection = detection.inspection
    session = inspection.custody_session if inspection else None
    plate = session.vehicle.plate_number if session and session.vehicle else "a vehicle"

    return create_alert(
        category=AlertCategory.POTENTIAL_DAMAGE,
        message=(
            f"Potential {detection.damage_type.lower().replace('_', ' ')} found on "
            f"{plate} ({detection.region.lower().replace('_', ' ')}). "
            f"Awaiting manager verification."
        ),
        severity=severity_map.get(detection.severity, AlertSeverity.LOW.value),
        custody_session_id=session.id if session else None,
        vehicle_id=session.vehicle_id if session else None,
        # Staff first: this is a task for a manager, not news for a customer
        # until somebody has confirmed it.
        audiences=[AlertAudience.VALET_MANAGER.value, AlertAudience.ADMIN.value],
        source_entity_type="damage_detections",
        source_id=detection.id,
        source_type=detection.damage_type,
        actor=actor,
    )


def raise_retrieval_ready(session, *, actor: AuthContext | None = None):
    """Tell the customer their car is staged for handover."""
    plate = session.vehicle.plate_number if session.vehicle else "Your vehicle"
    point = session.handover_point or "the handover point"

    return create_alert(
        category=AlertCategory.RETRIEVAL_READY,
        message=f"{plate} is ready for collection at {point}.",
        severity=AlertSeverity.LOW.value,
        custody_session_id=session.id,
        vehicle_id=session.vehicle_id,
        audiences=[
            AlertAudience.CUSTOMER.value,
            AlertAudience.VALET_OPERATOR.value,
        ],
        source_entity_type="custody_sessions",
        source_id=session.id,
        source_type="READY_FOR_PICKUP",
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def alert_dict(alert: Alert, *, actor: AuthContext | None = None) -> dict[str, Any]:
    """Serialise one alert.

    ``is_read`` is relative to ``actor`` — the same alert is read for one person
    and unread for another, which is the whole point of per-recipient state.
    """
    body: dict[str, Any] = {
        "alert_id": str(alert.id),
        "custody_id": str(alert.custody_session_id) if alert.custody_session_id else None,
        "vehicle_id": str(alert.vehicle_id) if alert.vehicle_id else None,
        "type": alert.type,
        "severity": alert.severity,
        "message": alert.message,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "status": alert.status,
        "vehicle": (
            {
                "plate_number": alert.vehicle.plate_number,
                "make": alert.vehicle.make,
                "model": alert.vehicle.model,
            }
            if alert.vehicle
            else None
        ),
        "source": {
            "entity_type": alert.source_entity_type,
            "id": str(alert.source_id) if alert.source_id else None,
            "type": alert.source_type,
        },
        "audiences": list(alert.audiences or []),
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolved_by": alert.resolved_by.email if alert.resolved_by else None,
        "resolution_notes": alert.resolution_notes,
        "is_open": alert.is_open,
    }
    if actor is not None:
        body["is_read"] = alert.is_read_by(actor.user_id)
        body["read_at"] = next(
            (
                read.created_at.isoformat()
                for read in alert.reads
                if str(read.user_id) == actor.user_id and read.created_at
            ),
            None,
        )
    return body


def list_alerts(
    actor: AuthContext,
    *,
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    unread_only: bool = False,
    vehicle_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """The alerts ``actor`` is allowed to see, newest first.

    ``unread_only`` powers the notification badge. It is computed from
    ``alert_reads`` rather than from the alert's status, because "unread" is a
    fact about this person, not about the alert.
    """
    conditions = _visibility_conditions(actor)

    if status:
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"'status' must be one of {list(VALID_STATUSES)}.",
                details={"field": "status", "allowed": list(VALID_STATUSES)},
            )
        conditions.append(Alert.status == status)
    if category:
        if category not in VALID_CATEGORIES:
            raise ValidationError(
                f"'type' must be one of {list(VALID_CATEGORIES)}.",
                details={"field": "type", "allowed": list(VALID_CATEGORIES)},
            )
        conditions.append(Alert.type == category)
    if severity:
        if severity not in VALID_SEVERITIES:
            raise ValidationError(
                f"'severity' must be one of {list(VALID_SEVERITIES)}.",
                details={"field": "severity", "allowed": list(VALID_SEVERITIES)},
            )
        conditions.append(Alert.severity == severity)
    if vehicle_id:
        conditions.append(Alert.vehicle_id == uuid.UUID(vehicle_id))
    if unread_only:
        conditions.append(
            ~Alert.reads.any(AlertRead.user_id == _actor_id(actor))
        )

    total = db.session.execute(
        select(func.count(Alert.id)).where(*conditions)
    ).scalar_one()

    rows = list(
        db.session.execute(
            select(Alert)
            .where(*conditions)
            # Worst first, then newest: someone scanning the feed should meet
            # the thing that needs them at the top.
            .order_by(_severity_rank(Alert.severity), Alert.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )

    return {
        "items": [alert_dict(alert, actor=actor) for alert in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def unread_count(actor: AuthContext) -> int:
    """How many alerts this actor has not read yet."""
    conditions = _visibility_conditions(actor)
    conditions.append(Alert.status == AlertStatus.OPEN.value)
    conditions.append(~Alert.reads.any(AlertRead.user_id == _actor_id(actor)))
    return db.session.execute(
        select(func.count(Alert.id)).where(*conditions)
    ).scalar_one()


def _owned_or_visible(actor: AuthContext, alert: Alert) -> None:
    """Refuse access to an alert outside the actor's scope.

    Checked explicitly rather than relying on the list filter, because fetching
    by id is a separate path and must not become a way around the scoping.
    """
    conditions = _visibility_conditions(actor)
    if not conditions:
        return
    allowed = db.session.execute(
        select(func.count(Alert.id)).where(Alert.id == alert.id, *conditions)
    ).scalar_one()
    if not allowed:
        # 404 rather than 403: confirming that an alert exists but is hidden
        # leaks the existence of incidents the actor has no business knowing
        # about.
        raise NotFoundError("Alert not found.", details={"alert_id": str(alert.id)})


def get_alert(actor: AuthContext, alert_id: str) -> dict[str, Any]:
    alert = db.session.get(Alert, uuid.UUID(alert_id))
    if alert is None:
        raise NotFoundError("Alert not found.", details={"alert_id": alert_id})
    _owned_or_visible(actor, alert)
    return alert_dict(alert, actor=actor)


def mark_read(actor: AuthContext, alert_id: str) -> dict[str, Any]:
    """Record that this actor has seen the alert. Idempotent.

    Does not change the alert's status: read state belongs to the reader, and
    clearing the alert itself would remove it from everyone else's queue.
    """
    alert = db.session.get(Alert, uuid.UUID(alert_id))
    if alert is None:
        raise NotFoundError("Alert not found.", details={"alert_id": alert_id})
    _owned_or_visible(actor, alert)

    read = alert.mark_read_by(actor.user_id)
    if read is not None:
        db.session.add(read)
        db.session.commit()
        db.session.refresh(alert)

    return {"alert_id": str(alert.id), "is_read": True, "already_read": read is None}


def mark_all_read(actor: AuthContext) -> dict[str, Any]:
    """Mark every alert currently visible to this actor as read.

    Scoped by the same predicate as the list, so "mark all read" cannot
    accidentally clear alerts the actor was never shown.
    """
    conditions = _visibility_conditions(actor)
    conditions.append(~Alert.reads.any(AlertRead.user_id == _actor_id(actor)))

    pending = list(db.session.execute(select(Alert).where(*conditions)).scalars())
    now = _now()
    for alert in pending:
        db.session.add(AlertRead(alert_id=alert.id, user_id=_actor_id(actor), created_at=now))
    db.session.commit()
    return {"marked_read": len(pending)}


def resolve_alert(
    actor: AuthContext,
    alert_id: str,
    *,
    notes: str | None = None,
    dismiss: bool = False,
) -> dict[str, Any]:
    """Close an alert. Manager and admin only.

    Resolution and dismissal are different acts and are recorded differently:
    ``RESOLVED`` means it was dealt with, ``DISMISSED`` means it was judged not
    to need action. Collapsing them would make "we fixed it" and "we ignored
    it" indistinguishable in the audit trail.
    """
    if not is_manager(actor):
        raise AuthorizationError(
            "Only a manager or administrator may close an alert.",
            details={"role": actor.role, "required": list(TRIAGE_ROLES)},
        )

    alert = db.session.get(Alert, uuid.UUID(alert_id))
    if alert is None:
        raise NotFoundError("Alert not found.", details={"alert_id": alert_id})

    if not alert.is_open:
        raise ConflictError(
            f"This alert is already {alert.status.lower()}.",
            details={"alert_id": alert_id, "status": alert.status},
        )

    if notes is not None and len(notes) > 500:
        raise ValidationError(
            "Resolution notes must be 500 characters or fewer.",
            details={"field": "notes", "max_length": 500},
        )

    alert.status = (
        AlertStatus.DISMISSED.value if dismiss else AlertStatus.RESOLVED.value
    )
    alert.resolved_by_id = _actor_id(actor)
    alert.resolved_at = _now()
    alert.resolution_notes = notes.strip()[:500] if notes else None

    # Mirror the decision onto the underlying security alert, so the geofence
    # record and the feed cannot drift apart about whether this is closed.
    if alert.source_entity_type == "security_alerts" and alert.source_id:
        from models.enums import AlertStatus as SecurityStatus
        from models.security_alert import SecurityAlert

        source = db.session.get(SecurityAlert, alert.source_id)
        if source is not None and source.status not in (
            SecurityStatus.RESOLVED.value,
            SecurityStatus.DISMISSED.value,
        ):
            from models.user import User

            source.status = (
                SecurityStatus.DISMISSED.value if dismiss else SecurityStatus.RESOLVED.value
            )
            # The source row requires its own attributed resolver.
            source.resolved_by_id = db.session.get(User, _actor_id(actor)).id
            source.resolved_at = alert.resolved_at
            source.resolution_notes = alert.resolution_notes

    _audit(
        action=str(
            AuditAction.ALERT_RESOLVED
        ),
        actor_context=actor,
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="alerts",
        entity_id=alert.id,
        description=(
            f"{'Dismissed' if dismiss else 'Resolved'} {alert.type} alert"
            + (f": {notes[:200]}" if notes else "")
        ),
    )
    db.session.commit()
    db.session.refresh(alert)
    return alert_dict(alert, actor=actor)


__all__ = [
    "TRIAGE_ROLES",
    "alert_dict",
    "create_alert",
    "get_alert",
    "is_manager",
    "is_staff",
    "list_alerts",
    "mark_all_read",
    "mark_read",
    "raise_from_security_alert",
    "raise_potential_damage",
    "raise_retrieval_ready",
    "resolve_alert",
    "unread_count",
]
