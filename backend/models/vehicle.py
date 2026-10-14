"""Customer vehicles.

A customer may register many vehicles; a vehicle belongs to exactly one
customer. Plates are stored both as entered and normalised, because the same
plate arrives as ``UP 78 AB 1234``, ``up78ab1234`` and ``UP-78-AB-1234``.

The unique constraint is a **partial** index: it applies only while a vehicle is
not archived, so a plate can be re-registered after an old row is archived
without a constraint violation.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import VehicleStatus, check_constraint, status_column

PLATE_MAX_LENGTH = 20
MAKE_MAX_LENGTH = 60
MODEL_MAX_LENGTH = 60
COLOR_MAX_LENGTH = 30


class Vehicle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        check_constraint("status", VehicleStatus),
        CheckConstraint("year >= 1950 AND year <= 2100", name="ck_vehicles_year_range"),
        CheckConstraint(
            "normalized_plate = upper(normalized_plate)",
            name="ck_vehicles_normalized_plate_is_upper",
        ),
        # Only one non-archived vehicle may hold a given plate.
        Index(
            "uq_vehicles_normalized_plate_active",
            "normalized_plate",
            unique=True,
            postgresql_where="status <> 'ARCHIVED'",
        ),
        Index("ix_vehicles_owner_status", "owner_id", "status"),
        Index("ix_vehicles_plate_number", "plate_number"),
        {
            "comment": (
                "Customer vehicles. `normalized_plate` is the uppercase, "
                "separator-free form used for lookups and uniqueness."
            )
        },
    )

    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_vehicles_owner_id_users"),
        nullable=False,
        index=True,
    )

    # -- identification -----------------------------------------------------
    plate_number: Mapped[str] = mapped_column(String(PLATE_MAX_LENGTH), nullable=False)
    normalized_plate: Mapped[str] = mapped_column(String(PLATE_MAX_LENGTH), nullable=False)
    make: Mapped[str] = mapped_column(String(MAKE_MAX_LENGTH), nullable=False)
    model: Mapped[str | None] = mapped_column(String(MODEL_MAX_LENGTH))
    year: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(String(COLOR_MAX_LENGTH))
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)

    status: Mapped[str] = status_column(VehicleStatus, VehicleStatus.REGISTERED)
    notes: Mapped[str | None] = mapped_column(String(500))

    # -- relationships ------------------------------------------------------
    owner: Mapped[User] = relationship("User", back_populates="vehicles")  # noqa: F821
    custody_sessions: Mapped[list[CustodySession]] = relationship(  # noqa: F821
        "CustodySession", back_populates="vehicle", passive_deletes=True
    )
    tickets: Mapped[list[ValetTicket]] = relationship(  # noqa: F821
        "ValetTicket", back_populates="vehicle", passive_deletes=True
    )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def normalize_plate(plate: str) -> str:
        """Uppercase and strip separators so equivalent plates compare equal."""
        return "".join(
            character for character in plate.upper() if character.isalnum()
        )

    @property
    def is_in_custody(self) -> bool:
        return self.status == VehicleStatus.IN_CUSTODY.value

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "owner_id": str(self.owner_id),
            "plate_number": self.plate_number,
            "normalized_plate": self.normalized_plate,
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "color": self.color,
            "vin": self.vin,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Vehicle {self.plate_number} status={self.status}>"


__all__ = ["Vehicle"]
