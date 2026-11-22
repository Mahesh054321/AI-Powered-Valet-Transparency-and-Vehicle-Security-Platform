"""Parking zones and their geofence polygons.

A zone is a named area a vehicle may be parked in, described by a geofence
polygon in WGS-84 ``[longitude, latitude]`` pairs (GeoJSON order, which is what
Leaflet's ``L.polygon`` expects). The polygon is stored as JSONB rather than in
a PostGIS geometry column: this project deliberately avoids requiring the
PostGIS extension so it installs on any stock PostgreSQL, and the geofence
maths is done in Python where it can be unit-tested and explained.

``buffer_metres`` is the tolerance beyond the boundary before a fix counts as a
breach, so GPS jitter does not raise alerts on its own.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import ZoneStatus, ZoneType, check_constraint, status_column

CODE_MAX_LENGTH = 32
NAME_MAX_LENGTH = 80
DESCRIPTION_MAX_LENGTH = 300


class ParkingZone(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "parking_zones"
    __table_args__ = (
        check_constraint("status", ZoneStatus),
        CheckConstraint("zone_type IN ('POLYGON', 'CIRCLE', 'RECTANGLE')", name="ck_parking_zones_type_valid"),
        CheckConstraint("capacity > 0", name="ck_parking_zones_capacity_positive"),
        CheckConstraint("current_occupancy >= 0", name="ck_parking_zones_occupancy_non_negative"),
        CheckConstraint(
            "current_occupancy <= capacity",
            name="ck_parking_zones_occupancy_within_capacity",
        ),
        CheckConstraint("buffer_metres >= 0", name="ck_parking_zones_buffer_non_negative"),
        CheckConstraint(
            "centre_latitude IS NULL OR (centre_latitude >= -90 AND centre_latitude <= 90)",
            name="ck_parking_zones_latitude_range",
        ),
        CheckConstraint(
            "centre_longitude IS NULL OR (centre_longitude >= -180 AND centre_longitude <= 180)",
            name="ck_parking_zones_longitude_range",
        ),
        CheckConstraint(
            "orientation_degrees IS NULL OR (orientation_degrees >= 0 AND orientation_degrees <= 360)",
            name="ck_parking_zones_orientation_range",
        ),
        Index("ix_parking_zones_status", "status"),
        Index("ix_parking_zones_vehicle_id", "vehicle_id"),
        {
            "comment": (
                "Named parking areas with a geofence polygon. `geofence_polygon` "
                "is a JSONB list of [longitude, latitude] pairs (WGS-84, GeoJSON "
                "order). No image or binary data is stored here."
            )
        },
    )

    code: Mapped[str] = mapped_column(
        String(CODE_MAX_LENGTH), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX_LENGTH))
    zone_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=str(ZoneType.POLYGON),
        server_default=str(ZoneType.POLYGON),
    )

    # -- geofence -----------------------------------------------------------
    geofence_polygon: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="List of [longitude, latitude] pairs forming a closed ring.",
    )
    centre_latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    centre_longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    # The positive-radius CHECK lives in __table_args__ with the others;
    # a column-level duplicate would be created twice under the same name.
    radius_metres: Mapped[float | None] = mapped_column(Float)
    length_metres: Mapped[float | None] = mapped_column(Numeric(9, 2))
    width_metres: Mapped[float | None] = mapped_column(Numeric(9, 2))
    orientation_degrees: Mapped[float | None] = mapped_column(Numeric(5, 2))
    buffer_metres: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=25.0, server_default="25"
    )

    # -- target assignment & ownership --------------------------------------
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="SET NULL", name="fk_parking_zones_vehicle_id_vehicles"),
        index=True,
    )
    custody_session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custody_sessions.id", ondelete="SET NULL", name="fk_parking_zones_custody_session_id"),
        index=True,
    )
    rules: Mapped[dict | None] = mapped_column(JSONB)
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_parking_zones_created_by_id_users"),
        index=True,
    )

    # -- capacity -----------------------------------------------------------
    capacity: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    current_occupancy: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    status: Mapped[str] = status_column(ZoneStatus, ZoneStatus.ACTIVE)

    # -- relationships ------------------------------------------------------
    custody_sessions: Mapped[list[CustodySession]] = relationship(  # noqa: F821
        "CustodySession",
        foreign_keys="[CustodySession.zone_id]",
        back_populates="zone",
        passive_deletes=True,
    )
    alerts: Mapped[list[SecurityAlert]] = relationship(  # noqa: F821
        "SecurityAlert", back_populates="zone", passive_deletes=True
    )
    vehicle: Mapped[Vehicle | None] = relationship("Vehicle", foreign_keys=[vehicle_id])  # noqa: F821
    created_by: Mapped[User | None] = relationship("User", foreign_keys=[created_by_id])  # noqa: F821

    # -- helpers ------------------------------------------------------------
    @property
    def is_parkable(self) -> bool:
        """True when the zone may accept a vehicle."""
        return self.status in {ZoneStatus.ACTIVE.value, ZoneStatus.FULL.value}

    @property
    def available_slots(self) -> int:
        return max(0, self.capacity - self.current_occupancy)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "zone_type": self.zone_type,
            "geofence_polygon": self.geofence_polygon,
            "centre": (
                {"latitude": float(self.centre_latitude), "longitude": float(self.centre_longitude)}
                if self.centre_latitude is not None and self.centre_longitude is not None
                else None
            ),
            "radius_metres": float(self.radius_metres) if self.radius_metres is not None else None,
            "length_metres": float(self.length_metres) if self.length_metres is not None else None,
            "width_metres": float(self.width_metres) if self.width_metres is not None else None,
            "orientation_degrees": (
                float(self.orientation_degrees) if self.orientation_degrees is not None else None
            ),
            "buffer_metres": float(self.buffer_metres),
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
            "custody_session_id": str(self.custody_session_id) if self.custody_session_id else None,
            "created_by_id": str(self.created_by_id) if self.created_by_id else None,
            "rules": self.rules or {},
            "capacity": self.capacity,
            "current_occupancy": self.current_occupancy,
            "available_slots": self.available_slots,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ParkingZone {self.code} status={self.status}>"


__all__ = ["ParkingZone"]
