"""Security alerts raised by the geofence and anomaly modules.

An alert is the auditable output of a decision: it records what was observed,
what was concluded, how confident the system was, and *why* -- so a manager can
review it without re-running the analysis.

Severity is ordered ``LOW < MEDIUM < HIGH < CRITICAL`` and status moves
``OPEN → ACKNOWLEDGED → INVESTIGATING → RESOLVED|DISMISSED``. Acknowledgement
and resolution are attributed and timestamped, because "who closed this alert,
and when" is exactly the question an audit asks.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import AlertSeverity, AlertStatus, AlertType, check_constraint, status_column


class SecurityAlert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_alerts"
    __table_args__ = (
        check_constraint("alert_type", AlertType),
        check_constraint("severity", AlertSeverity),
        check_constraint("status", AlertStatus),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_security_alerts_confidence_is_probability",
        ),
        # Explainability is a requirement: an alert must say why it fired.
        CheckConstraint(
            "reason IS NOT NULL AND length(reason) > 0",
            name="ck_security_alerts_reason_required",
        ),
        # Attribution integrity.
        CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_by_id IS NOT NULL",
            name="ck_security_alerts_ack_requires_actor",
        ),
        CheckConstraint(
            "acknowledged_by_id IS NULL OR acknowledged_at IS NOT NULL",
            name="ck_security_alerts_actor_requires_ack_time",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR acknowledged_at IS NULL OR resolved_at >= acknowledged_at",
            name="ck_security_alerts_resolve_after_ack",
        ),
        CheckConstraint(
            "occurred_at <= COALESCE(acknowledged_at, occurred_at)",
            name="ck_security_alerts_ack_after_occurrence",
        ),
        # A resolved or dismissed alert must not still be open.
        CheckConstraint(
            "status NOT IN ('RESOLVED', 'DISMISSED') OR resolved_at IS NOT NULL",
            name="ck_security_alerts_terminal_requires_resolution_time",
        ),
        Index("ix_security_alerts_status_severity", "status", "severity"),
        Index(
            "ix_security_alerts_session_occurred",
            text("custody_session_id, occurred_at DESC"),
        ),
        Index("ix_security_alerts_occurred_at", "occurred_at"),
        # The manager's open-items queue, hottest first.
        Index(
            "ix_security_alerts_open_queue",
            text("severity, occurred_at DESC"),
            postgresql_where="status IN ('OPEN', 'ACKNOWLEDGED', 'INVESTIGATING')",
        ),
        {
            "comment": (
                "Geofence and anomaly findings with severity, confidence and a "
                "human-readable reason. Acknowledgement and resolution are "
                "attributed and timestamped for audit."
            )
        },
    )

    custody_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "custody_sessions.id",
            ondelete="CASCADE",
            name="fk_security_alerts_custody_session_id_custody_sessions",
        ),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "vehicles.id", ondelete="SET NULL", name="fk_security_alerts_vehicle_id_vehicles"
        ),
        index=True,
    )
    zone_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "parking_zones.id", ondelete="SET NULL", name="fk_security_alerts_zone_id_zones"
        ),
        index=True,
    )
    gps_event_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "gps_events.id", ondelete="SET NULL", name="fk_security_alerts_gps_event_id_gps_events"
        ),
        comment="The specific fix that triggered the alert, when applicable.",
    )

    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = status_column(AlertSeverity, AlertSeverity.LOW)
    status: Mapped[str] = status_column(AlertStatus, AlertStatus.OPEN)

    # -- what was observed and concluded ------------------------------------
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    reason: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="Why the system flagged this."
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    #: The inputs that produced the verdict, so it can be re-derived.
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    #: Reference to an annotated map/evidence image. Never the image bytes.
    evidence_image_path: Mapped[str | None] = mapped_column(String(1024))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    # -- acknowledgement ----------------------------------------------------
    acknowledged_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_security_alerts_acknowledged_by_id_users"
        ),
        index=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_security_alerts_resolved_by_id_users"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(String(1000))

    # -- relationships ------------------------------------------------------
    custody_session: Mapped[CustodySession] = relationship(  # noqa: F821
        "CustodySession", back_populates="alerts"
    )
    zone: Mapped[ParkingZone | None] = relationship(  # noqa: F821
        "ParkingZone", back_populates="alerts"
    )
    gps_event: Mapped[GpsEvent | None] = relationship("GpsEvent")  # noqa: F821
    acknowledged_by: Mapped[User | None] = relationship(  # noqa: F821
        "User", foreign_keys=[acknowledged_by_id]
    )
    resolved_by: Mapped[User | None] = relationship(  # noqa: F821
        "User", foreign_keys=[resolved_by_id]
    )

    @property
    def is_open(self) -> bool:
        return self.status in {
            AlertStatus.OPEN.value,
            AlertStatus.ACKNOWLEDGED.value,
            AlertStatus.INVESTIGATING.value,
        }

    def to_dict(self) -> dict:
        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id),
            "custody_session_id": str(self.custody_session_id),
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
            "zone_id": str(self.zone_id) if self.zone_id else None,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "reason": self.reason,
            "confidence": float(self.confidence),
            "evidence": self.evidence,
            "occurred_at": _iso(self.occurred_at),
            "acknowledged_at": _iso(self.acknowledged_at),
            "resolved_at": _iso(self.resolved_at),
            "resolution_notes": self.resolution_notes,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SecurityAlert {self.alert_type} {self.severity} {self.status}>"


__all__ = ["SecurityAlert"]
