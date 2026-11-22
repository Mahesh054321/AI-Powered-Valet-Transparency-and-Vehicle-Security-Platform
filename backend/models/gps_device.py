"""Physical GPS Tracker Devices registered to customer vehicles.

Stores hardware tracker identifiers, provider types, connection status,
and cryptographic tokens for authenticated device telemetry ingestion.
Passwords and API secrets are never stored in plaintext.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import DeviceConnectionStatus, check_constraint

DEVICE_ID_MAX_LENGTH = 64
PROVIDER_MAX_LENGTH = 64


class GpsDevice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gps_devices"

    __table_args__ = (
        check_constraint("connection_status", DeviceConnectionStatus),
        Index("ix_gps_devices_device_id", "device_id", unique=True),
        Index("ix_gps_devices_vehicle_id", "vehicle_id"),
        {"comment": "Physical GPS tracker hardware registered against customer vehicles."},
    )

    device_id: Mapped[str] = mapped_column(
        String(DEVICE_ID_MAX_LENGTH),
        nullable=False,
        unique=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="SET NULL", name="fk_gps_devices_vehicle_id_vehicles"),
        nullable=True,
    )
    provider_name: Mapped[str] = mapped_column(
        String(PROVIDER_MAX_LENGTH),
        nullable=False,
        default="traccar",
        comment="traccar | teltonika | geotab | freematics | generic_webhook | mock",
    )
    integration_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="DIRECT_TELEMETRY",
        comment="DIRECT_TELEMETRY | WEBHOOK | API_POLLING",
    )
    connection_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DeviceConnectionStatus.NOT_CONFIGURED.value,
    )
    device_secret_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Salted digest of device access token / secret for ingestion authentication.",
    )
    last_successful_update: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_category: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relationships
    vehicle: Mapped[Vehicle | None] = relationship(  # noqa: F821
        "Vehicle", back_populates="gps_devices"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "device_id": self.device_id,
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
            "provider_name": self.provider_name,
            "integration_type": self.integration_type,
            "connection_status": self.connection_status,
            "last_successful_update": (
                self.last_successful_update.isoformat()
                if self.last_successful_update
                else None
            ),
            "last_error_category": self.last_error_category,
            "metadata": self.metadata_ or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = ["GpsDevice"]
