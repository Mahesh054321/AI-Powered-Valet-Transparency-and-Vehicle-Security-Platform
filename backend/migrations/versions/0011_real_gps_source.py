"""Add an explicit provenance value for authenticated hardware telemetry.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GPS_SOURCES_0010 = (
    "SIMULATED",
    "HARDWARE",
    "MANUAL",
    "MOBILE_GPS",
    "VEHICLE_GPS",
    "CAR_GPS",
)
GPS_SOURCES_0011 = (*GPS_SOURCES_0010, "REAL_GPS")


def _check_sql(values: tuple[str, ...]) -> str:
    escaped = ", ".join(f"'{value}'" for value in values)
    return f"source IN ({escaped})"


def upgrade() -> None:
    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint(
        "ck_gps_events_source_valid",
        "gps_events",
        _check_sql(GPS_SOURCES_0011),
    )


def downgrade() -> None:
    op.execute("UPDATE gps_events SET source = 'CAR_GPS' WHERE source = 'REAL_GPS'")
    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint(
        "ck_gps_events_source_valid",
        "gps_events",
        _check_sql(GPS_SOURCES_0010),
    )
