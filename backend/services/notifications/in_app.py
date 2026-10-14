"""The in-app notification channel — the only transport wired up for the MVP.

Delivery here means writing a row the recipient's own client will read. It is a
real channel rather than a special case in the alert service: the alert code
does not know it exists, which is the whole point of the abstraction.

**Why it returns a result rather than just inserting.** Keeping the same
contract as a future SMTP or push channel means swapping transports is a
configuration change, and means the ledger records in-app delivery the same way
it will record everything else — so "did the customer actually get told" has one
answer regardless of how they were told.
"""

from __future__ import annotations

import logging

from database import db
from models.notification import Notification, NotificationChannel, NotificationStatus
from services.notifications.base import BaseChannel, DeliveryResult, NotificationPayload

logger = logging.getLogger(__name__)


class InAppChannel(BaseChannel):
    """Writes a notification row for the recipient's client to fetch."""

    name = NotificationChannel.IN_APP
    enabled = True

    def send(self, payload: NotificationPayload) -> DeliveryResult:
        from datetime import datetime, timezone

        try:
            notification = Notification(
                alert_id=payload.alert_id,
                user_id=payload.user_id,
                channel=self.name.value,
                title=payload.title[:200],
                body=payload.body[:1000],
                status=NotificationStatus.DELIVERED.value,
                # Delivered immediately: there is no provider in between, so
                # claiming a later delivery time would be invented.
                delivered_at=datetime.now(timezone.utc),
                metadata_=payload.context or None,
            )
            db.session.add(notification)
            db.session.flush()
            return DeliveryResult.ok(self.name.value, provider_message_id=str(notification.id))
        except Exception as exc:  # noqa: BLE001
            # A transport failure must not take the alert down with it. The
            # alert is the fact; this is an attempt to surface it.
            logger.warning("in-app notification failed: %s", exc)
            db.session.rollback()
            return DeliveryResult.failed(self.name.value, str(exc))


__all__ = ["InAppChannel"]
