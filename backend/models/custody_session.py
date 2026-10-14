"""Custody sessions: the window between drop-off and pickup.

This is the entity the whole platform exists to make transparent. A session is
opened when a vehicle is handed to a valet against an issued ticket, and closed
when the vehicle is returned and the customer accepts its condition.

Everything time-sensitive hangs off it: GPS events, the two inspections,
security alerts and retrieval predictions all carry ``custody_session_id``.

Two partial unique indexes carry the business rules that are easy to get wrong
in application code:

* ``uq_custody_sessions_vehicle_open`` -- a vehicle cannot be in two open
  sessions at once, which is what actually enforces "one custody at a time"
  (rather than trusting ``vehicles.status``);
* ``uq_custody_sessions_ticket`` -- one custody session per ticket.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import CustodyStatus, check_constraint, status_column


class CustodySession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "custody_sessions"
    __table_args__ = (
        check_constraint("status", CustodyStatus),
        # Timeline integrity.
        CheckConstraint(
            "dropped_off_at <= COALESCE(picked_up_at, dropped_off_at)",
            name="ck_custody_sessions_pickup_after_drop_off",
        ),
        CheckConstraint(
            "retrieval_requested_at IS NULL OR dropped_off_at IS NULL "
            "OR retrieval_requested_at >= dropped_off_at",
            name="ck_custody_sessions_retrieval_after_drop_off",
        ),
        CheckConstraint(
            "expected_retrieval_minutes IS NULL OR expected_retrieval_minutes > 0",
            name="ck_custody_sessions_expected_retrieval_positive",
        ),
        CheckConstraint(
            "odometer_at_drop_off IS NULL OR odometer_at_drop_off >= 0",
            name="ck_custody_sessions_odometer_non_negative",
        ),
        CheckConstraint(
            "fuel_level_percent IS NULL OR (fuel_level_percent >= 0 AND fuel_level_percent <= 100)",
            name="ck_custody_sessions_fuel_level_range",
        ),
        CheckConstraint(
            "mileage_driven IS NULL OR mileage_driven >= 0",
            name="ck_custody_sessions_mileage_non_negative",
        ),
        # A closed session must have been picked up, and a picked-up session
        # must be closed. Written as two boolean implications because
        # `status` is a VARCHAR, so it cannot be compared to a boolean.
        CheckConstraint(
            "status <> 'CHECKED_OUT' OR picked_up_at IS NOT NULL",
            name="ck_custody_sessions_closed_requires_pickup",
        ),
        CheckConstraint(
            "picked_up_at IS NULL OR status = 'CHECKED_OUT'",
            name="ck_custody_sessions_pickup_requires_closed",
        ),        # A vehicle may not be in two open custody sessions at once.
        Index(
            "uq_custody_sessions_vehicle_open",
            "vehicle_id",
            unique=True,
            postgresql_where="status <> 'CHECKED_OUT'",
        ),
        # One custody session per ticket.
        Index("uq_custody_sessions_ticket", "ticket_id", unique=True),
        Index("ix_custody_sessions_status", "status"),
        Index("ix_custody_sessions_zone_status", "zone_id", "status"),
        Index("ix_custody_sessions_dropped_off_at", "dropped_off_at"),
        {
            "comment": (
                "The custody window between drop-off and pickup. Parent of GPS "
                "events, inspections, alerts and retrieval predictions."
            )
        },
    )

    ticket_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "valet_tickets.id", ondelete="RESTRICT", name="fk_custody_sessions_ticket_id_tickets"
        ),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "vehicles.id", ondelete="RESTRICT", name="fk_custody_sessions_vehicle_id_vehicles"
        ),
        nullable=False,
        index=True,
    )
    zone_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "parking_zones.id",
            ondelete="RESTRICT",
            name="fk_custody_sessions_zone_id_parking_zones",
        ),
        index=True,
    )
    assigned_valet_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", ondelete="RESTRICT", name="fk_custody_sessions_assigned_valet_id_users"
        ),
        index=True,
    )
    received_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", ondelete="RESTRICT", name="fk_custody_sessions_received_by_id_users"
        ),
    )

    status: Mapped[str] = status_column(CustodyStatus, CustodyStatus.CHECKED_IN)

    # -- timeline -----------------------------------------------------------
    dropped_off_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    zone_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieval_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieval_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_for_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    expected_retrieval_minutes: Mapped[int | None] = mapped_column(Integer)
    handover_point: Mapped[str | None] = mapped_column(String(120))

    # -- condition summary --------------------------------------------------
    odometer_at_drop_off: Mapped[int | None] = mapped_column(Integer)
    fuel_level_percent: Mapped[int | None] = mapped_column(Integer)
    pre_existing_damage_notes: Mapped[str | None] = mapped_column(String(1000))
    condition_accepted: Mapped[bool | None] = mapped_column(Boolean)
    condition_notes: Mapped[str | None] = mapped_column(String(1000))
    mileage_driven: Mapped[int | None] = mapped_column(
        Integer,
        comment="Set on checkout; a non-trivial value is itself suspicious."
    )

    # -- relationships ------------------------------------------------------
    ticket: Mapped[ValetTicket] = relationship(  # noqa: F821
        "ValetTicket", back_populates="custody_sessions"
    )
    vehicle: Mapped[Vehicle] = relationship(  # noqa: F821
        "Vehicle", back_populates="custody_sessions"
    )
    zone: Mapped[ParkingZone | None] = relationship(  # noqa: F821
        "ParkingZone", back_populates="custody_sessions"
    )
    assigned_valet: Mapped[User | None] = relationship(  # noqa: F821
        "User", foreign_keys=[assigned_valet_id]
    )
    received_by: Mapped[User | None] = relationship(  # noqa: F821
        "User", foreign_keys=[received_by_id]
    )

    gps_events: Mapped[list[GpsEvent]] = relationship(  # noqa: F821
        "GpsEvent",
        back_populates="custody_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    inspections: Mapped[list[VehicleInspection]] = relationship(  # noqa: F821
        "VehicleInspection",
        back_populates="custody_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    alerts: Mapped[list[SecurityAlert]] = relationship(  # noqa: F821
        "SecurityAlert",
        back_populates="custody_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    predictions: Mapped[list[RetrievalPrediction]] = relationship(  # noqa: F821
        "RetrievalPrediction",
        back_populates="custody_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # -- helpers ------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """True while the platform is responsible for the vehicle."""
        return self.status != CustodyStatus.CHECKED_OUT.value

    @property
    def drop_off_inspection(self) -> VehicleInspection | None:  # noqa: F821
        from models.enums import InspectionStage

        return next(
            (item for item in self.inspections if item.stage == InspectionStage.DROP_OFF.value),
            None,
        )

    @property
    def pickup_inspection(self) -> VehicleInspection | None:  # noqa: F821
        from models.enums import InspectionStage

        return next(
            (item for item in self.inspections if item.stage == InspectionStage.PICKUP.value),
            None,
        )

    def to_dict(self) -> dict:
        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "vehicle_id": str(self.vehicle_id),
            "zone_id": str(self.zone_id) if self.zone_id else None,
            "assigned_valet_id": str(self.assigned_valet_id) if self.assigned_valet_id else None,
            "status": self.status,
            "is_open": self.is_open,
            "dropped_off_at": _iso(self.dropped_off_at),
            "zone_assigned_at": _iso(self.zone_assigned_at),
            "retrieval_requested_at": _iso(self.retrieval_requested_at),
            "picked_up_at": _iso(self.picked_up_at),
            "condition_accepted": self.condition_accepted,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CustodySession {self.id} status={self.status}>"


__all__ = ["CustodySession"]
