"""GPS telemetry fixes.

One row per position report during a custody session. Every fix records its
**provenance** (``source``): ``SIMULATED`` for the demo generator, ``REAL_GPS``
for an authenticated registered hardware feed, ``MOBILE_GPS`` for an authorized
browser, or ``MANUAL`` for an operator entry. The project mandate is
that simulated data must never be mistakable for hardware data, so provenance
is stored per row rather than inferred from a deployment-wide setting.

Fixes are deliberately high-volume and immutable: there is no ``updated_at``
here. A telemetry row is a historical measurement, and allowing it to be edited
would undermine the audit trail the platform exists to provide.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, UUIDPrimaryKeyMixin
from models.enums import MlClassification, MovementStatus, TelemetrySource, check_constraint

DEVICE_ID_MAX_LENGTH = 64


class GpsEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "gps_events"

    #: Row-insertion time. Deliberately *not* TimestampMixin: this table is
    #: append-only, so there is no ``updated_at``. ``recorded_at`` below is the
    #: measurement time, which is what every query cares about.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        check_constraint("source", TelemetrySource),
        check_constraint("movement_status", MovementStatus),
        check_constraint("ml_classification", MlClassification),
        # The three ML columns stand or fall together. Storing a verdict without
        # the probability that produced it, or without the model version that
        # produced that, would leave a number nobody could interpret or audit.
        CheckConstraint(
            "(ml_classification IS NULL) = (ml_probability IS NULL)",
            name="ck_gps_events_ml_verdict_needs_probability",
        ),
        CheckConstraint(
            "(ml_classification IS NULL) = (ml_model_version IS NULL)",
            name="ck_gps_events_ml_verdict_needs_model_version",
        ),
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_gps_events_latitude_range"),
        CheckConstraint(
            "longitude >= -180 AND longitude <= 180", name="ck_gps_events_longitude_range"
        ),
        CheckConstraint(
            "speed_kmph IS NULL OR speed_kmph >= 0", name="ck_gps_events_speed_non_negative"
        ),
        CheckConstraint(
            "heading_degrees IS NULL OR (heading_degrees >= 0 AND heading_degrees < 360)",
            name="ck_gps_events_heading_range",
        ),
        CheckConstraint(
            "accuracy_metres IS NULL OR accuracy_metres > 0",
            name="ck_gps_events_accuracy_positive",
        ),
        # An out-of-zone fix is what makes the geofence module's job auditable.
        CheckConstraint("is_within_zone IS NOT NULL", name="ck_gps_events_zone_flag_present"),
        # A fix only has a meaningful geofence verdict once it has been
        # evaluated against some zone. Without a zone the engine cannot claim
        # the vehicle is inside anything, and it has no boundary to measure a
        # distance from -- so both must say so rather than carry a value that
        # looks measured.
        CheckConstraint(
            "zone_id IS NOT NULL "
            "OR (is_within_zone = false AND distance_from_zone_metres IS NULL)",
            name="ck_gps_events_zone_fields_consistent",
        ),
        # Hot path: the live-tracking map reads the latest fixes for one session.
        # A descending second key needs a SQL expression: a bare string would
        # be read as a column name, which does not exist.
        Index(
            "ix_gps_events_session_recorded_desc",
            text("custody_session_id, recorded_at DESC"),
        ),
        Index("ix_gps_events_source", "source"),
        Index("ix_gps_events_recorded_at", "recorded_at"),
        # Anomaly review: every fix that left its zone, newest first.
        Index(
            "ix_gps_events_out_of_zone",
            text("custody_session_id, recorded_at DESC"),
            postgresql_where="is_within_zone = false",
        ),
        # The security dashboard's alert strip reads only the fixes that
        # needed attention, newest first, without scanning the whole track.
        Index(
            "ix_gps_events_status_not_normal",
            text("custody_session_id, recorded_at DESC"),
            postgresql_where="movement_status <> 'NORMAL'",
        ),
        {
            "comment": (
                "Position fixes during custody. Immutable: a telemetry row is a "
                "historical measurement, so it has no updated_at. `source` "
                "distinguishes SIMULATED from authenticated REAL_GPS data on every row."
            )
        },
    )

    custody_session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "custody_sessions.id",
            ondelete="CASCADE",
            name="fk_gps_events_custody_session_id_custody_sessions",
        ),
        nullable=True,
        index=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "vehicles.id",
            ondelete="SET NULL",
            name="fk_gps_events_vehicle_id_vehicles",
        ),
        nullable=True,
        index=True,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # -- position -----------------------------------------------------------
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    speed_kmph: Mapped[float | None] = mapped_column(Numeric(6, 2))
    heading_degrees: Mapped[float | None] = mapped_column(Numeric(5, 2))
    accuracy_metres: Mapped[float | None] = mapped_column(Numeric(7, 2))

    # -- provenance ---------------------------------------------------------
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=str(TelemetrySource.SIMULATED),
        server_default=str(TelemetrySource.SIMULATED),
        comment=(
            "SIMULATED | REAL_GPS | MOBILE_GPS | MANUAL "
            "(legacy sources retained) - never inferred."
        ),
    )
    device_id: Mapped[str | None] = mapped_column(String(DEVICE_ID_MAX_LENGTH))
    sensor_data: Mapped[dict | None] = mapped_column(
        JSONB, comment="Hardware tracker telemetry (ignition, tamper, shock, power, door)."
    )

    # -- geofence evaluation ------------------------------------------------
    zone_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parking_zones.id", ondelete="SET NULL", name="fk_gps_events_zone_id_zones"),
        comment="The zone this fix was evaluated against.",
    )
    is_within_zone: Mapped[bool] = mapped_column(nullable=False, default=True)
    boundary_state: Mapped[str | None] = mapped_column(
        String(32), comment="INSIDE | NEAR_BOUNDARY | OUTSIDE | UNCERTAIN"
    )
    distance_from_zone_metres: Mapped[float | None] = mapped_column(
        Numeric(9, 2),
        comment="Signed distance to the zone boundary; positive means outside.",
    )
    #: The geofence engine's verdict for this fix, computed once at ingest from
    #: the distance, speed and recent movement pattern stored alongside it.
    #: Storing it keeps the dashboard's filters cheap on a table that is
    #: append-only by design, and the inputs stay on the row so the verdict
    #: can always be re-derived rather than trusted.
    movement_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=str(MovementStatus.NORMAL),
        server_default=str(MovementStatus.NORMAL),
        comment="NORMAL | WARNING | SUSPICIOUS | BREACH - rule-based, re-derivable.",
    )

    # -- prototype ML classification ----------------------------------------
    # Recorded separately from `movement_status` and never allowed to overwrite
    # it. A prototype trained on synthetic data is evidence to be weighed, not
    # a verdict; the rule engine's measurement stays authoritative. NULL when no
    # model artifact is present, which is a supported deployment state.
    ml_classification: Mapped[str | None] = mapped_column(
        String(16), comment="NORMAL | SUSPICIOUS from the prototype classifier."
    )
    ml_probability: Mapped[float | None] = mapped_column(
        Numeric(4, 3), comment="P(SUSPICIOUS) as estimated by the prototype model."
    )
    ml_model_version: Mapped[str | None] = mapped_column(
        String(64), comment="Which artifact produced the verdict, so drift is detectable."
    )

    # -- relationships ------------------------------------------------------
    custody_session: Mapped[CustodySession] = relationship(  # noqa: F821
        "CustodySession", back_populates="gps_events"
    )

    @property
    def is_simulated(self) -> bool:
        """Explicit helper so no caller has to compare provenance strings."""
        return self.source == TelemetrySource.SIMULATED.value

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "custody_session_id": str(self.custody_session_id) if self.custody_session_id else None,
            "vehicle_id": str(self.vehicle_id) if self.vehicle_id else None,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "latitude": float(self.latitude),
            "longitude": float(self.longitude),
            "speed_kmph": float(self.speed_kmph) if self.speed_kmph is not None else None,
            "heading_degrees": (
                float(self.heading_degrees) if self.heading_degrees is not None else None
            ),
            "accuracy_metres": (
                float(self.accuracy_metres) if self.accuracy_metres is not None else None
            ),
            "source": self.source,
            "is_simulated": self.is_simulated,
            "device_id": self.device_id,
            "sensor_data": self.sensor_data,
            "zone_id": str(self.zone_id) if self.zone_id else None,
            "is_within_zone": self.is_within_zone,
            "boundary_state": self.boundary_state,
            "distance_from_zone_metres": (
                float(self.distance_from_zone_metres)
                if self.distance_from_zone_metres is not None
                else None
            ),
            "movement_status": self.movement_status,
            "ml": (
                {
                    "classification": self.ml_classification,
                    "probability": float(self.ml_probability),
                    "model_version": self.ml_model_version,
                }
                if self.ml_classification is not None
                else None
            ),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<GpsEvent {self.latitude},{self.longitude} "
            f"source={self.source} within_zone={self.is_within_zone}>"
        )


__all__ = ["GpsEvent"]
