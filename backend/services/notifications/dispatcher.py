"""Routing alerts to people, and people to channels.

Two separate decisions happen here, and keeping them apart is the point:

1. **Who should hear about this alert?** A policy question, answered from the
   alert's ``audiences`` and the roles in the system. It knows nothing about
   transports.
2. **How do we reach them?** A transport question, answered by iterating the
   registered channels. It knows nothing about alerts.

Business logic only ever calls :func:`dispatch`. Swapping the in-app channel
for email changes nothing above this line, which is the requirement the
abstraction exists to satisfy.

**A manager is always a recipient of a staff alert, even if the publisher only
said "operator".** Otherwise every publisher has to remember to list the manager
role, and the first one that forgets silently hides incidents from the person
accountable for them. Failing towards over-notification on the management side
is the safer error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.extensions import db
from models.alert import Alert, AlertAudience
from models.enums import UserStatus
from models.notification import NotificationChannel
from models.user import User
from services.notifications.base import DeliveryResult, NotificationPayload
from services.notifications.email import EmailChannel
from services.notifications.in_app import InAppChannel
from services.notifications.stubs import PushChannel, SmsChannel
from utils.security import Role

logger = logging.getLogger(__name__)

#: Every registered channel, in the order they are tried. Only enabled channels
#: are used; the disabled ones are kept in the list so the health endpoint can
#: report what exists and what is not wired up.
CHANNELS = (InAppChannel(), EmailChannel(), SmsChannel(), PushChannel())

#: Roles treated as "staff" for routing purposes.
STAFF_ROLES = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)

#: A manager sees every staff alert, not only the ones explicitly addressed to
#: the manager role. See the module docstring for why this is the safe default.
ESCALATED_TO_MANAGER = (
    AlertAudience.VALET_OPERATOR.value,
    AlertAudience.VALET_MANAGER.value,
    AlertAudience.ADMIN.value,
)


@dataclass
class DispatchSummary:
    """What a dispatch produced, for the caller and for the audit trail."""

    recipients: int
    delivered: int
    failed: int
    results: list[DeliveryResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "recipients": self.recipients,
            "delivered": self.delivered,
            "failed": self.failed,
        }


def describe_channels() -> list[dict[str, Any]]:
    """Self-description of every channel, enabled or not.

    Surfaced on the health endpoint so it is visible which transports are live,
    rather than leaving a reviewer to infer it from the absence of emails.
    """
    return [channel.describe() for channel in CHANNELS]


def _audience_roles(alert: Alert) -> set[str]:
    """The roles an alert is addressed to, with manager escalation applied."""
    audiences = {str(value) for value in (alert.audiences or [])}
    if audiences & set(ESCALATED_TO_MANAGER):
        audiences.add(Role.VALET_MANAGER.value)
    return audiences


def resolve_recipients(alert: Alert) -> list[User]:
    """The users who should hear about ``alert``.

    Customers are resolved by *ownership* rather than by the custody session's
    customer, so an alert about a vehicle reaches the person who owns it even if
    the session row is missing or stale.

    Deactivated and suspended accounts are excluded. Notifying a suspended
    account is how a security alert ends up in the inbox of someone who should
    no longer have access.
    """
    roles = _audience_roles(alert)
    if not roles:
        return []

    conditions = [
        User.status.in_([UserStatus.ACTIVE.value, UserStatus.PENDING.value]),
    ]

    staff = list(
        db.session.execute(
            select(User).where(User.role.in_(sorted(roles & set(STAFF_ROLES))), *conditions)
        ).scalars()
    )

    customers: list[User] = []
    if AlertAudience.CUSTOMER.value in roles:
        # Prefer the customer on the ticket, falling back to the vehicle owner.
        # Both are consulted because an alert can outlive the session that
        # raised it, and the owner is the durable relationship.
        customer_ids: set = set()
        if alert.custody_session_id is not None:
            from models.custody_session import CustodySession

            session = db.session.get(CustodySession, alert.custody_session_id)
            if session and session.ticket and session.ticket.customer_id:
                customer_ids.add(session.ticket.customer_id)
        if alert.vehicle_id is not None:
            from models.vehicle import Vehicle

            vehicle = db.session.get(Vehicle, alert.vehicle_id)
            if vehicle and vehicle.owner_id:
                customer_ids.add(vehicle.owner_id)

        if customer_ids:
            customers = list(
                db.session.execute(
                    select(User).where(
                        User.id.in_(customer_ids),
                        User.role == Role.CUSTOMER.value,
                        *conditions,
                    )
                ).scalars()
            )

    # De-duplicated: an admin can appear via both the staff query and an
    # explicit audience entry, and nobody wants the same alert twice.
    seen: set[str] = set()
    unique: list[User] = []
    for user in staff + customers:
        if str(user.id) not in seen:
            seen.add(str(user.id))
            unique.append(user)
    return unique


def dispatch(
    alert: Alert,
    *,
    channels: tuple = CHANNELS,
) -> DispatchSummary:
    """Notify everyone who should hear about ``alert``.

    Never raises. A transport failure is recorded on the ledger and counted in
    the summary, because the alert itself is already committed and losing it
    over a broken push gateway would be the wrong trade.
    """
    recipients = resolve_recipients(alert)
    active = [channel for channel in channels if channel.enabled]
    results: list[DeliveryResult] = []

    vehicle_info = None
    if alert.vehicle:
        vehicle_info = f"{alert.vehicle.make} {alert.vehicle.model} ({alert.vehicle.plate_number})"
    elif alert.vehicle_id:
        from models.vehicle import Vehicle

        v = db.session.get(Vehicle, alert.vehicle_id)
        if v:
            vehicle_info = f"{v.make} {v.model} ({v.plate_number})"

    zone_name = None
    if alert.custody_session_id:
        from models.custody_session import CustodySession

        cs = db.session.get(CustodySession, alert.custody_session_id)
        if cs and cs.zone:
            zone_name = f"{cs.zone.name} ({cs.zone.code})"

    location_str = None
    if alert.metadata_ and "latitude" in alert.metadata_ and "longitude" in alert.metadata_:
        location_str = f"{float(alert.metadata_['latitude']):.5f}, {float(alert.metadata_['longitude']):.5f}"

    for user in recipients:
        payload = NotificationPayload(
            user_id=str(user.id),
            title=_title_for(alert),
            body=alert.message,
            alert_id=str(alert.id),
            severity=alert.severity,
            context={
                "type": alert.type,
                "source_entity_type": alert.source_entity_type,
                "source_id": str(alert.source_id) if alert.source_id else None,
                "vehicle_id": str(alert.vehicle_id) if alert.vehicle_id else None,
                "vehicle_info": vehicle_info or "Valet Vehicle",
                "zone_name": zone_name or "Authorized Safe Zone",
                "custody_session_id": str(alert.custody_session_id) if alert.custody_session_id else None,
                "location_str": location_str or "See live tracking map",
                "tracking_url": f"/tracking/{alert.custody_session_id}" if alert.custody_session_id else "/tracking",
                "event_time": alert.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if alert.created_at else None,
                "recipient_email": user.email,
                "recipient_name": user.full_name,
            },
        )
        for channel in active:
            results.append(channel.send(payload))

    delivered = sum(1 for result in results if result.delivered)
    return DispatchSummary(
        recipients=len(recipients),
        delivered=delivered,
        failed=len(results) - delivered,
        results=results,
    )


def _title_for(alert: Alert) -> str:
    """A short human title for the notification centre.

    Derived from the category rather than stored separately, so the two cannot
    drift apart when the vocabulary changes.
    """
    labels = {
        "GEOFENCE_BREACH": "Vehicle left its zone",
        "SUSPICIOUS_MOVEMENT": "Suspicious movement detected",
        "OVERSPEED": "Vehicle moving too fast",
        "POTENTIAL_DAMAGE": "Potential damage found",
        "RETRIEVAL_READY": "Your vehicle is ready",
        "TAMPER_ALERT": "Vehicle tampering / sensor alert",
        "SECURITY_ALERT": "Security alert detected",
    }
    return labels.get(alert.type, "Vehicle alert")


__all__ = [
    "CHANNELS",
    "DispatchSummary",
    "NotificationChannel",
    "describe_channels",
    "dispatch",
    "resolve_recipients",
]
