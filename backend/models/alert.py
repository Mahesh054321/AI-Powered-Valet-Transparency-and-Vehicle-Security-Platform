"""The centralized alert feed and its per-recipient read state.

**Why this is not ``models.security_alert``.** That model is the geofence
engine's own record: it points at a GPS fix, a zone, and a vision evidence blob.
This model is the *feed* users actually read, spanning three domains — geofence
events, damage findings, and retrieval readiness. Each alert points back at its
origin through ``source_entity_type``/``source_id``, so the evidence chain
survives and nothing is duplicated into a form that can drift.

**Two vocabularies, on purpose.** ``AlertType`` (in ``models.enums``) is the
geofence detector's precise language — seven values including ``TAMPER_DETECTED``
and ``TELEMETRY_LOSS``. ``AlertCategory`` here is what a human triaging a feed
needs: five buckets. The mapping is lossy by design, so the original is kept in
``source_type`` and can always be recovered.

**Read state is per recipient.** ``Alert.status`` tracks only the alert's
lifecycle (OPEN / RESOLVED / DISMISSED). "Have I seen this" is a separate
question with a separate answer per person, held in ``AlertRead`` — because a
single READ flag would let a customer opening the app clear a CRITICAL breach
from the manager's queue.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import AlertSeverity, check_constraint, status_column


class AlertCategory(StrEnum):
    """The categories the centralized feed exposes."""

    GEOFENCE_BREACH = "GEOFENCE_BREACH"  # vehicle left its assigned zone
    SUSPICIOUS_MOVEMENT = "SUSPICIOUS_MOVEMENT"  # moved oddly, or was touched
    OVERSPEED = "OVERSPEED"  # faster than the zone permits
    POTENTIAL_DAMAGE = "POTENTIAL_DAMAGE"  # detector flagged damage, unverified
    RETRIEVAL_READY = "RETRIEVAL_READY"  # car is staged for handover


#: How the geofence detector's precise types fold into the feed
#: categories. Kept here rather than inline so the projection is auditable in
#: one place, and so a new detector type fails loudly instead of silently
#: falling through to a default.
CATEGORY_BY_SOURCE_TYPE: dict[str, AlertCategory] = {
    "GEOFENCE_BREACH": AlertCategory.GEOFENCE_BREACH,
    "CONFIRMED_EXIT": AlertCategory.GEOFENCE_BREACH,
    "RETURN_TO_ZONE": AlertCategory.GEOFENCE_BREACH,
    "ABNORMAL_MOVEMENT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "UNEXPECTED_PARKED_MOVEMENT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "FREQUENT_CROSSINGS": AlertCategory.SUSPICIOUS_MOVEMENT,
    "IMPLAUSIBLE_MOVEMENT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "LOCATION_UNCERTAIN": AlertCategory.SUSPICIOUS_MOVEMENT,
    "UNAUTHORIZED_ACCESS": AlertCategory.SUSPICIOUS_MOVEMENT,
    "TAMPER_DETECTED": AlertCategory.SUSPICIOUS_MOVEMENT,
    "DEVICE_TAMPER_ALARM": AlertCategory.SUSPICIOUS_MOVEMENT,
    "IGNITION_ALERT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "DOOR_OPEN_ALERT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "VIBRATION_SHOCK_ALERT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "POWER_DISCONNECT": AlertCategory.SUSPICIOUS_MOVEMENT,
    "DEVICE_REMOVED": AlertCategory.SUSPICIOUS_MOVEMENT,
    "TELEMETRY_LOSS": AlertCategory.SUSPICIOUS_MOVEMENT,
    "UNUSUAL_SPEED": AlertCategory.OVERSPEED,
    "ROUTE_DEVIATION": AlertCategory.OVERSPEED,
}


def category_for(source_type: str) -> AlertCategory:
    """Map a precise source type onto a feed category.

    Raises rather than defaulting: a new detector type that quietly became
    ``SUSPICIOUS_MOVEMENT`` would mislabel security events, and mislabelling is
    worse than failing loudly during development.
    """
    try:
        return CATEGORY_BY_SOURCE_TYPE[source_type]
    except KeyError as exc:
        raise ValueError(
            f"No alert category is mapped for source type {source_type!r}. "
            f"Add it to CATEGORY_BY_SOURCE_TYPE."
        ) from exc


class AlertStatus(StrEnum):
    """Lifecycle only — never "read".

    Read state is per recipient and lives in ``AlertRead``. Putting a READ
    status here would mean whoever opened the app first cleared the alert for
    everyone else.
    """

    OPEN = "OPEN"  # raised, not yet closed
    ACKNOWLEDGED = "ACKNOWLEDGED"  # acknowledged by operator/manager
    INVESTIGATING = "INVESTIGATING"  # actively under investigation
    RESOLVED = "RESOLVED"  # dealt with
    DISMISSED = "DISMISSED"  # judged not to need action
    REOPENED = "REOPENED"  # reopened for review


class AlertAudience(StrEnum):
    """Who an alert is addressed to.

    Stored as an array because one event legitimately concerns several
    audiences: a geofence breach is an operator problem *and* something the
    customer is entitled to know about.
    """

    CUSTOMER = "CUSTOMER"
    VALET_OPERATOR = "VALET_OPERATOR"
    VALET_MANAGER = "VALET_MANAGER"
    ADMIN = "ADMIN"


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        check_constraint("type", AlertCategory),
        check_constraint("severity", AlertSeverity),
        check_constraint("status", AlertStatus),
        # A resolution with no resolver is an unattributed decision, refused for
        # the same reason a verified damage finding needs a reviewer.
        CheckConstraint(
            "resolved_at IS NULL OR resolved_by_id IS NOT NULL",
            name="ck_alerts_resolution_attributed",
        ),
        # An alert addressed to nobody is written and then seen by nobody: a
        # silent failure rather than an empty feed.
        CheckConstraint(
            "jsonb_array_length(audiences) > 0",
            name="ck_alerts_has_audience",
        ),
        CheckConstraint(
            "status NOT IN ('RESOLVED', 'DISMISSED') OR resolved_at IS NOT NULL",
            name="ck_alerts_terminal_requires_resolution_time",
        ),
        # One alert per originating row per category. Without this, every
        # incoming GPS fix would re-raise the same breach and bury the feed.
        Index(
            "uq_alerts_source",
            "source_entity_type",
            "source_id",
            "type",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        Index("ix_alerts_created_at", "created_at"),
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_type", "type"),
        Index("ix_alerts_severity", "severity"),
        Index("ix_alerts_vehicle_id", "vehicle_id"),
        Index("ix_alerts_custody_session_id", "custody_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # Nullable: a fleet-level alert may not concern one car, and forcing a
    # custody session would invent a link that does not exist.
    custody_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custody_sessions.id", ondelete="CASCADE")
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="SET NULL")
    )

    type: Mapped[str] = status_column(AlertCategory, AlertCategory.GEOFENCE_BREACH, nullable=True)
    severity: Mapped[str] = status_column(AlertSeverity, AlertSeverity.MEDIUM, length=16)
    status: Mapped[str] = status_column(AlertStatus, AlertStatus.OPEN, length=16)
    message: Mapped[str] = mapped_column(String(500), nullable=False)

    # Provenance. The feed links back to the evidence rather than restating it,
    # because two copies of the same fact eventually disagree.
    source_entity_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_type: Mapped[str | None] = mapped_column(String(40))

    audiences: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    raised_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(String(500))

    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # -- relationships -------------------------------------------------------
    reads: Mapped[list[AlertRead]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", lazy="selectin"
    )
    notifications: Mapped[list] = relationship(
        "Notification", back_populates="alert", cascade="all, delete-orphan"
    )
    vehicle: Mapped[Vehicle] = relationship("Vehicle", lazy="joined")  # noqa: F821
    resolved_by: Mapped[User] = relationship(  # noqa: F821
        "User", foreign_keys=[resolved_by_id], lazy="joined"
    )

    @property
    def is_open(self) -> bool:
        return self.status in {
            AlertStatus.OPEN.value,
            AlertStatus.ACKNOWLEDGED.value,
            AlertStatus.INVESTIGATING.value,
            AlertStatus.REOPENED.value,
        }

    def is_read_by(self, user_id: uuid.UUID | str) -> bool:
        target = str(user_id)
        return any(str(read.user_id) == target for read in self.reads)

    def mark_read_by(self, user_id: uuid.UUID | str) -> AlertRead | None:
        """Record that ``user_id`` has seen this alert. Idempotent.

        Returns ``None`` if it was already read, so the caller can distinguish a
        first read (which may need a badge decrement) from a repeat.
        """
        if self.is_read_by(user_id):
            return None
        read = AlertRead(alert_id=self.id, user_id=user_id)
        self.reads.append(read)
        return read


class AlertRead(UUIDPrimaryKeyMixin, Base):
    """One person's acknowledgement that they saw one alert.

    Deliberately a separate table rather than a column on ``alerts``: read state
    is per recipient, and collapsing it to one flag would let the first person
    to open the app clear it for everyone.
    """

    __tablename__ = "alert_reads"
    __table_args__ = (
        Index("uq_alert_reads_alert_user", "alert_id", "user_id", unique=True),
        Index("ix_alert_reads_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The row's insertion time is the read time. There is deliberately no
    #: separate ``read_at``: it could only ever equal this, and two columns that
    #: must agree are two columns that can disagree.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    alert: Mapped[Alert] = relationship(back_populates="reads")


__all__ = [
    "Alert",
    "AlertAudience",
    "AlertCategory",
    "AlertRead",
    "AlertStatus",
    "CATEGORY_BY_SOURCE_TYPE",
    "category_for",
]
