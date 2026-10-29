"""Real GPS Tracker Device registration, vehicle linking, and source expansion.

Extends the schema with:
1. Table `gps_devices`: hardware tracker registration, provider adapter linking, connection status, and credentials.
2. `vehicles`: tracking_method selection and linked gps_device_id.
3. `gps_events`: expand ck_gps_events_source_valid to include CAR_GPS.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GPS_SOURCES_0009 = ("SIMULATED", "HARDWARE", "MANUAL", "MOBILE_GPS", "VEHICLE_GPS")
GPS_SOURCES_0010 = ("SIMULATED", "HARDWARE", "MANUAL", "MOBILE_GPS", "VEHICLE_GPS", "CAR_GPS")

CONNECTION_STATUSES = (
    "NOT_CONFIGURED",
    "PENDING_VERIFICATION",
    "CONNECTED",
    "STALE",
    "OFFLINE",
    "ERROR",
)


def _check_sql(column: str, values: tuple[str, ...]) -> str:
    escaped = ", ".join(f"'{val}'" for val in values)
    return f"{column} IN ({escaped})"


def upgrade() -> None:
    # 1. Add tracking_method and gps_device_id to vehicles
    op.add_column(
        "vehicles",
        sa.Column(
            "tracking_method",
            sa.String(32),
            nullable=False,
            server_default="SIMULATED",
            comment="REAL_GPS | MOBILE_GPS | SIMULATED | NOT_CONFIGURED",
        ),
    )
    op.add_column(
        "vehicles",
        sa.Column(
            "gps_device_id",
            sa.String(64),
            nullable=True,
            comment="Registered GPS tracker device identifier.",
        ),
    )

    # 2. Create gps_devices table
    op.create_table(
        "gps_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_name", sa.String(64), nullable=False, server_default="traccar"),
        sa.Column("integration_type", sa.String(32), nullable=False, server_default="DIRECT_TELEMETRY"),
        sa.Column("connection_status", sa.String(32), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("device_secret_hash", sa.String(128), nullable=True),
        sa.Column("last_successful_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(64), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], name="fk_gps_devices_vehicle_id_vehicles", ondelete="SET NULL"),
        sa.CheckConstraint(_check_sql("connection_status", CONNECTION_STATUSES), name="ck_gps_devices_connection_status_valid"),
    )
    op.create_index("ix_gps_devices_device_id", "gps_devices", ["device_id"], unique=True)
    op.create_index("ix_gps_devices_vehicle_id", "gps_devices", ["vehicle_id"])

    # 3. Expand ck_gps_events_source_valid
    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint("ck_gps_events_source_valid", "gps_events", _check_sql("source", GPS_SOURCES_0010))


def downgrade() -> None:
    # 3. Restore ck_gps_events_source_valid (update any CAR_GPS rows first to satisfy constraint)
    op.execute("UPDATE gps_events SET source = 'VEHICLE_GPS' WHERE source = 'CAR_GPS'")
    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint("ck_gps_events_source_valid", "gps_events", _check_sql("source", GPS_SOURCES_0009))

    # 2. Drop gps_devices table
    op.drop_index("ix_gps_devices_vehicle_id", table_name="gps_devices")
    op.drop_index("ix_gps_devices_device_id", table_name="gps_devices")
    op.drop_table("gps_devices")

    # 1. Drop columns from vehicles
    op.drop_column("vehicles", "gps_device_id")
    op.drop_column("vehicles", "tracking_method")
