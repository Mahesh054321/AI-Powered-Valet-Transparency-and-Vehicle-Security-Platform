"""The notification channel abstraction.

**Why this exists.** The requirement is that email, SMS and push can be added
later without rewriting business logic. For that to be true rather than
aspirational, the code that decides *who should hear about an alert* must never
mention a transport. So it doesn't: `services.alerts` calls
`dispatcher.dispatch(alert)` and has no idea whether the result went to a
database row, an SMTP server, or a push gateway.

A channel is anything with a ``name`` and a ``send`` method. Adding email means
writing a ~30-line class and registering it — no change to alert creation,
auditing, or role scoping.

**Delivery outcomes are values, not exceptions.** ``send`` returns a
``DeliveryResult`` rather than raising, because a failed push must not roll back
the alert that caused it. The alert is the important fact; the notification is
an attempt to surface it. Treating a transport failure as a transaction failure
would lose the fact because the pipe was broken.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from models.notification import NotificationChannel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationPayload:
    """What a channel is asked to deliver.

    Deliberately transport-neutral: no subject lines, no template IDs, no
    device tokens. A channel translates this into whatever its provider needs,
    which is why adding a transport does not require changing callers.
    """

    user_id: str
    title: str
    body: str
    alert_id: str | None = None
    severity: str | None = None
    #: Free-form context for a channel that can use it (deep links, priority).
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome of one delivery attempt.

    Carried back to the caller so it can be written to the ledger. A failure is
    a result, not an exception — see the module docstring.
    """

    channel: str
    delivered: bool
    provider_message_id: str | None = None
    failure_reason: str | None = None

    @classmethod
    def ok(cls, channel: str, provider_message_id: str | None = None) -> DeliveryResult:
        return cls(channel=channel, delivered=True, provider_message_id=provider_message_id)

    @classmethod
    def failed(cls, channel: str, reason: str) -> DeliveryResult:
        return cls(channel=channel, delivered=False, failure_reason=reason[:500])


class BaseChannel(ABC):
    """One transport.

    Subclasses implement ``send`` and must never raise for an ordinary delivery
    failure — return ``DeliveryResult.failed`` instead. Raising is reserved for
    programmer error (a misconfigured channel), which *should* be loud.
    """

    #: Which vocabulary entry this channel writes.
    name: NotificationChannel

    #: Whether this channel is wired up in the current deployment. A disabled
    #: channel is skipped silently rather than producing a failure row on every
    #: alert, because "email is not configured here" is not a delivery failure.
    enabled: bool = False

    @abstractmethod
    def send(self, payload: NotificationPayload) -> DeliveryResult:
        """Attempt delivery and report the outcome."""

    def describe(self) -> dict[str, Any]:
        """Self-description, surfaced on the health endpoint.

        So a reviewer can see at a glance which transports are actually live,
        rather than inferring it from the absence of emails.
        """
        return {"channel": self.name.value, "enabled": self.enabled}
