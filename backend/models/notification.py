"""In-app notifications, and the ledger of every delivery attempt.

This table is a **delivery ledger, not a queue**. Each row records one attempt
to reach one person over one channel, whether it worked or not. For the local
MVP the only implemented channel is ``IN_APP``; ``EMAIL``, ``SMS`` and ``PUSH``
exist in the vocabulary so the channel abstraction has somewhere to write, but
nothing produces them yet.

A row is written even when delivery fails, so a notification that went nowhere
is visible rather than silently lost. The schema enforces that: a ``FAILED`` row
must carry a ``failure_reason``, and a ``DELIVERED`` row must carry a
timestamp — an undiagnosable failure is worse than a visible one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import check_constraint, status_column


class NotificationChannel(StrEnum):
    """Delivery channels.

    ``IN_APP`` is implemented. The other three are declared so the channel
    abstraction is real rather than aspirational: adding email means writing a
    channel class and enabling it, not touching the alert logic that already
    decides who should hear about what.
    """

    IN_APP = "IN_APP"  # the app's own notification centre
    EMAIL = "EMAIL"  # not wired in the MVP
    SMS = "SMS"  # not wired in the MVP
    PUSH = "PUSH"  # not wired in the MVP


class NotificationStatus(StrEnum):
    PENDING = "PENDING"  # accepted, not yet handed to a channel
    DELIVERED = "DELIVERED"  # the channel confirmed it
    FAILED = "FAILED"  # the channel could not deliver it


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        check_constraint("channel", NotificationChannel),
        check_constraint("status", NotificationStatus),
        # A delivered notification with no timestamp cannot be ordered against
        # the others, and a failed one with no reason cannot be diagnosed.
        CheckConstraint(
            "status <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_notifications_delivery_timed",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_notifications_failure_explained",
        ),
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_created_at", "created_at"),
        Index("ix_notifications_status", "status"),
        Index("ix_notifications_alert_id", "alert_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # Nullable: a notification need not originate from an alert (a system
    # announcement, say), but in practice almost all of them do.
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    channel: Mapped[str] = status_column(
        NotificationChannel, NotificationChannel.IN_APP, length=16
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = status_column(NotificationStatus, NotificationStatus.PENDING, length=16)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    alert: Mapped[Alert] = relationship("Alert", back_populates="notifications")  # noqa: F821
    user: Mapped[User] = relationship("User", lazy="joined")  # noqa: F821

    @property
    def is_unread(self) -> bool:
        """Whether the recipient has acted on it.

        ``read_at`` rather than a status change, so acknowledging a notification
        does not rewrite the delivery record.
        """
        return self.read_at is None

    #: Set when the recipient opens it. Separate from ``status`` on purpose:
    #: delivery outcome and recipient attention are different facts.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["Notification", "NotificationChannel", "NotificationStatus"]
