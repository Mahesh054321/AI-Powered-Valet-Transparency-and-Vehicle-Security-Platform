"""Notification delivery: channels, routing, and the in-app transport.

Public surface is deliberately tiny. Business logic calls
:func:`dispatch` and nothing else — it never names a channel, because the
requirement is that email, SMS and push can be added later without touching the
code that decides who should hear about an alert.

To add a transport:

1. Write a ``BaseChannel`` subclass implementing ``send``.
2. Add an instance to ``CHANNELS`` in :mod:`services.notifications.dispatcher`.
3. Set ``enabled = True``.

No change to ``services.alerts``, the publishers, the routes, or the auditing.
"""

from __future__ import annotations

from services.notifications.base import (
    BaseChannel,
    DeliveryResult,
    NotificationPayload,
)
from services.notifications.dispatcher import (
    CHANNELS,
    DispatchSummary,
    describe_channels,
    dispatch,
    resolve_recipients,
)
from services.notifications.in_app import InAppChannel
from services.notifications.stubs import EmailChannel, PushChannel, SmsChannel

__all__ = [
    "CHANNELS",
    "BaseChannel",
    "DeliveryResult",
    "DispatchSummary",
    "EmailChannel",
    "InAppChannel",
    "NotificationPayload",
    "PushChannel",
    "SmsChannel",
    "describe_channels",
    "dispatch",
    "resolve_recipients",
]
