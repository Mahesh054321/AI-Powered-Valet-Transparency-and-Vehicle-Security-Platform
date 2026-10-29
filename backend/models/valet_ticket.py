"""The digital valet ticket.

Issued when a customer registers a vehicle for valet, before the handover. It
carries the human-readable ``ticket_code`` shown on screen and the QR payload
the gate scanner reads. The QR code itself is rendered on demand and written to
``storage/media/`` -- only its path is stored here, never the image bytes.

Exactly one custody session is created per ticket when the vehicle is handed
over; the relationship is one-to-many at the schema level so a re-issued
custody (for example after a dispute) can be represented without a migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import TicketStatus, check_constraint, status_column

TICKET_CODE_MAX_LENGTH = 24
QR_PAYLOAD_MAX_LENGTH = 512


class ValetTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "valet_tickets"
    __table_args__ = (
        check_constraint("status", TicketStatus),
        CheckConstraint(
            "expected_return_at IS NULL OR issued_at IS NULL OR expected_return_at > issued_at",
            name="ck_valet_tickets_return_after_issue",
        ),
        CheckConstraint(
            "actual_drop_off_at IS NULL OR actual_drop_off_at >= issued_at",
            name="ck_valet_tickets_drop_off_after_issue",
        ),
        CheckConstraint(
            "actual_pickup_at IS NULL OR actual_drop_off_at IS NULL "
            "OR actual_pickup_at >= actual_drop_off_at",
            name="ck_valet_tickets_pickup_after_drop_off",
        ),
        Index("ix_valet_tickets_customer_status", "customer_id", "status"),
        Index("ix_valet_tickets_vehicle_id", "vehicle_id"),
        {
            "comment": (
                "Digital valet tickets. `qr_code_path` references the rendered "
                "QR image in storage; the image bytes are never stored here."
            )
        },
    )

    ticket_code: Mapped[str] = mapped_column(
        String(TICKET_CODE_MAX_LENGTH), nullable=False, unique=True, index=True
    )

    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_valet_tickets_customer_id_users"),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="RESTRICT", name="fk_valet_tickets_vehicle_id_vehicles"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = status_column(TicketStatus, TicketStatus.ISSUED)

    # -- timeline -----------------------------------------------------------
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expected_return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_drop_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- QR -----------------------------------------------------------------
    qr_payload: Mapped[str | None] = mapped_column(
        String(QR_PAYLOAD_MAX_LENGTH),
        comment="The string encoded into the QR code; not the image itself.",
    )
    qr_code_path: Mapped[str | None] = mapped_column(
        String(1024),
        comment="Relative path or object-storage key of the rendered QR image.",
    )

    special_instructions: Mapped[str | None] = mapped_column(String(500))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    # -- relationships ------------------------------------------------------
    customer: Mapped[User] = relationship("User", back_populates="tickets")  # noqa: F821
    vehicle: Mapped[Vehicle] = relationship("Vehicle", back_populates="tickets")  # noqa: F821
    custody_sessions: Mapped[list[CustodySession]] = relationship(  # noqa: F821
        "CustodySession", back_populates="ticket", passive_deletes=True
    )

    @property
    def is_open(self) -> bool:
        return self.status in {TicketStatus.ISSUED.value, TicketStatus.ACTIVE.value}

    def to_dict(self) -> dict:
        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id),
            "ticket_code": self.ticket_code,
            "customer_id": str(self.customer_id),
            "vehicle_id": str(self.vehicle_id),
            "status": self.status,
            "issued_at": _iso(self.issued_at),
            "expected_return_at": _iso(self.expected_return_at),
            "actual_drop_off_at": _iso(self.actual_drop_off_at),
            "actual_pickup_at": _iso(self.actual_pickup_at),
            "qr_payload": self.qr_payload,
            "qr_code_path": self.qr_code_path,
            "special_instructions": self.special_instructions,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ValetTicket {self.ticket_code} status={self.status}>"


__all__ = ["ValetTicket"]
