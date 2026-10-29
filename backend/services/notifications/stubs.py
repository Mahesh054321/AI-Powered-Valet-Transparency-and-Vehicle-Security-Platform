"""Channel stubs for transports that are not wired up in this MVP.

These exist for one reason: to prove the abstraction is real rather than a
promise. Each is a complete, correct implementation of ``BaseChannel`` whose
only missing part is a provider. Wiring email means filling in ``send`` with an
SMTP or API call and flipping ``enabled`` — nothing in the alert logic, the
auditing, or the role scoping changes, because none of it mentions a transport.

**They refuse rather than pretend.** A stub that returned success would write
``DELIVERED`` rows for messages nobody received, which is worse than no email
at all: it turns "we could not reach you" into a lie on the ledger. So each
returns an explicit failure, and the dispatcher skips disabled channels
entirely so those failures are never even generated in normal operation.
"""

from __future__ import annotations

from models.notification import NotificationChannel
from services.notifications.base import BaseChannel, DeliveryResult, NotificationPayload

#: Why each transport is absent, surfaced on the health endpoint so nobody has
#: to read the source to learn that push notifications do not exist here.
NOT_CONFIGURED = "not configured in this deployment"


class EmailChannel(BaseChannel):
    """SMTP/API email. Not wired up in the MVP.

    To enable: set ``enabled = True`` and implement ``send`` against a real
    provider. The payload already carries everything an email needs — recipient,
    title as subject, body — so no caller changes are required.
    """

    name = NotificationChannel.EMAIL
    enabled = False

    def send(self, payload: NotificationPayload) -> DeliveryResult:
        return DeliveryResult.failed(self.name.value, f"email channel {NOT_CONFIGURED}")


class SmsChannel(BaseChannel):
    """SMS. Not wired up in the MVP.

    Needs a phone number, which ``User`` already stores, and a provider. The
    payload's ``context`` is the intended place for a short-link or template id.
    """

    name = NotificationChannel.SMS
    enabled = False

    def send(self, payload: NotificationPayload) -> DeliveryResult:
        return DeliveryResult.failed(self.name.value, f"SMS channel {NOT_CONFIGURED}")


class PushChannel(BaseChannel):
    """Mobile/web push. Not wired up in the MVP.

    Needs a device-token registry, which this schema deliberately does not have
    yet: adding one is a data-model change, not a channel change, and inventing
    a column for it now would be speculative.
    """

    name = NotificationChannel.PUSH
    enabled = False

    def send(self, payload: NotificationPayload) -> DeliveryResult:
        return DeliveryResult.failed(self.name.value, f"push channel {NOT_CONFIGURED}")


__all__ = ["EmailChannel", "PushChannel", "SmsChannel"]
