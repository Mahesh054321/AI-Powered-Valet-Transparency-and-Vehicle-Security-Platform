"""Smart geofencing, real GPS sources, tamper detection, and persistent alert workflows.

Extends the schema with:
1. ParkingZone: user-defined safe zone parameters (zone_type, dimensions, vehicle/session links, rules, created_by).
2. GpsEvent: support for MOBILE_GPS and VEHICLE_GPS sources, sensor_data for hardware telemetry, boundary_state, and direct vehicle_id.
3. SecurityAlert & Alert: support for REOPENED status, expanded AlertType vocabulary (confirmed exit, return-to-zone, parked movement, tamper events), and tamper sensor fields.
4. User: notification_preferences for email alert configuration.
5. AuditLog: new lifecycle and security audit actions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ZONE_STATUSES = ("ACTIVE", "MAINTENANCE", "FULL", "RETIRED")
NEW_ZONE_STATUSES = ("ACTIVE", "MAINTENANCE", "FULL", "RETIRED", "DEACTIVATED")

OLD_GPS_SOURCES = ("SIMULATED", "HARDWARE", "MANUAL")
NEW_GPS_SOURCES = ("SIMULATED", "HARDWARE", "MANUAL", "MOBILE_GPS", "VEHICLE_GPS")

OLD_SECURITY_ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED")
NEW_SECURITY_ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED", "REOPENED")

OLD_ALERT_TYPES = (
    "GEOFENCE_BREACH",
    "ROUTE_DEVIATION",
    "UNUSUAL_SPEED",
    "ABNORMAL_MOVEMENT",
    "UNAUTHORIZED_ACCESS",
    "TAMPER_DETECTED",
    "TELEMETRY_LOSS",
)
NEW_ALERT_TYPES = OLD_ALERT_TYPES + (
    "CONFIRMED_EXIT",
    "RETURN_TO_ZONE",
    "UNEXPECTED_PARKED_MOVEMENT",
    "FREQUENT_CROSSINGS",
    "IMPLAUSIBLE_MOVEMENT",
    "LOCATION_UNCERTAIN",
    "DEVICE_TAMPER_ALARM",
    "IGNITION_ALERT",
    "DOOR_OPEN_ALERT",
    "VIBRATION_SHOCK_ALERT",
    "POWER_DISCONNECT",
    "DEVICE_REMOVED",
)

OLD_FEED_ALERT_TYPES = (
    "GEOFENCE_BREACH",
    "SUSPICIOUS_MOVEMENT",
    "OVERSPEED",
    "POTENTIAL_DAMAGE",
    "RETRIEVAL_READY",
)
NEW_FEED_ALERT_TYPES = OLD_FEED_ALERT_TYPES + (
    "TAMPER_ALERT",
    "SECURITY_ALERT",
)

OLD_FEED_ALERT_STATUSES = ("OPEN", "RESOLVED", "DISMISSED")
NEW_FEED_ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED", "REOPENED")

OLD_AUDIT_ACTIONS = (
    "USER_LOGIN",
    "VEHICLE_CREATED",
    "TICKET_CREATED",
    "VEHICLE_CHECKED_IN",
    "IMAGE_UPLOADED",
    "CUSTODY_STARTED",
    "GPS_BREACH",
    "SECURITY_ALERT_CREATED",
    "PICKUP_REQUESTED",
    "RETRIEVAL_PREDICTED",
    "PICKUP_IMAGE_CAPTURED",
    "DAMAGE_DETECTED",
    "VEHICLE_PICKED_UP",
    "CUSTODY_COMPLETED",
    "CREATE",
    "UPDATE",
    "DELETE",
    "LOGOUT",
    "LOGIN_FAILED",
    "STATUS_CHANGE",
    "ZONE_ASSIGN",
    "ALERT_ACKNOWLEDGED",
    "ALERT_RESOLVED",
    "INSPECTION_CAPTURED",
    "DAMAGE_VERIFIED",
    "INSPECTION_DEMO_RUN",
    "GEOFENCE_CONFIGURED",
    "GPS_TRACK_SIMULATED",
    "EXPORT",
)
NEW_AUDIT_ACTIONS = OLD_AUDIT_ACTIONS + (
    "SECURITY_ALERT_INVESTIGATING",
    "SECURITY_ALERT_REOPENED",
    "SAFE_ZONE_CREATED",
    "SAFE_ZONE_UPDATED",
    "SAFE_ZONE_DELETED",
    "TAMPER_EVENT_RECORDED",
    "EMAIL_DISPATCHED",
    "EMAIL_DISPATCH_FAILED",
)


def _check_sql(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(val) for val in values)})"


def upgrade() -> None:
    # ----------------------------------------------------------------------
    # 1. parking_zones extensions
    # ----------------------------------------------------------------------
    op.add_column("parking_zones", sa.Column("zone_type", sa.String(32), server_default="POLYGON", nullable=False))
    op.add_column("parking_zones", sa.Column("length_metres", sa.Numeric(9, 2), nullable=True))
    op.add_column("parking_zones", sa.Column("width_metres", sa.Numeric(9, 2), nullable=True))
    op.add_column("parking_zones", sa.Column("orientation_degrees", sa.Numeric(5, 2), nullable=True))
    op.add_column(
        "parking_zones",
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicles.id", ondelete="SET NULL", name="fk_parking_zones_vehicle_id_vehicles"),
            nullable=True,
            index=True,
        ),
    )
    op.add_column(
        "parking_zones",
        sa.Column(
            "custody_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custody_sessions.id", ondelete="SET NULL", name="fk_parking_zones_custody_session_id"),
            nullable=True,
            index=True,
        ),
    )
    op.add_column("parking_zones", sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "parking_zones",
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_parking_zones_created_by_id_users"),
            nullable=True,
            index=True,
        ),
    )

    op.drop_constraint("ck_parking_zones_status_valid", "parking_zones", type_="check")
    op.create_check_constraint("ck_parking_zones_status_valid", "parking_zones", _check_sql("status", NEW_ZONE_STATUSES))
    op.create_check_constraint(
        "ck_parking_zones_type_valid",
        "parking_zones",
        "zone_type IN ('POLYGON', 'CIRCLE', 'RECTANGLE')",
    )
    op.create_check_constraint(
        "ck_parking_zones_orientation_range",
        "parking_zones",
        "orientation_degrees IS NULL OR (orientation_degrees >= 0 AND orientation_degrees <= 360)",
    )

    # ----------------------------------------------------------------------
    # 2. gps_events extensions
    # ----------------------------------------------------------------------
    op.add_column("gps_events", sa.Column("sensor_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("gps_events", sa.Column("boundary_state", sa.String(32), nullable=True))
    op.add_column(
        "gps_events",
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicles.id", ondelete="SET NULL", name="fk_gps_events_vehicle_id_vehicles"),
            nullable=True,
            index=True,
        ),
    )
    # Allow custody_session_id to be nullable for direct vehicle telemetry
    op.alter_column("gps_events", "custody_session_id", nullable=True)

    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint("ck_gps_events_source_valid", "gps_events", _check_sql("source", NEW_GPS_SOURCES))

    # ----------------------------------------------------------------------
    # 3. security_alerts extensions
    # ----------------------------------------------------------------------
    op.add_column("security_alerts", sa.Column("tamper_sensor_type", sa.String(64), nullable=True))
    op.add_column("security_alerts", sa.Column("consecutive_count", sa.Integer(), server_default="1", nullable=False))

    op.drop_constraint("ck_security_alerts_status_valid", "security_alerts", type_="check")
    op.create_check_constraint("ck_security_alerts_status_valid", "security_alerts", _check_sql("status", NEW_SECURITY_ALERT_STATUSES))

    op.drop_constraint("ck_security_alerts_alert_type_valid", "security_alerts", type_="check")
    op.create_check_constraint("ck_security_alerts_alert_type_valid", "security_alerts", _check_sql("alert_type", NEW_ALERT_TYPES))

    # ----------------------------------------------------------------------
    # 4. alerts (feed) extensions
    # ----------------------------------------------------------------------
    op.drop_constraint("ck_alerts_status_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_status_valid", "alerts", _check_sql("status", NEW_FEED_ALERT_STATUSES))

    op.drop_constraint("ck_alerts_type_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_type_valid", "alerts", _check_sql("type", NEW_FEED_ALERT_TYPES))

    # ----------------------------------------------------------------------
    # 5. users notification preferences
    # ----------------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "notification_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{"email_enabled": true, "sms_enabled": false, "min_severity": "LOW"}',
            nullable=True,
        ),
    )

    # ----------------------------------------------------------------------
    # 6. audit_logs vocabulary update
    # ----------------------------------------------------------------------
    op.drop_constraint("ck_audit_logs_action_valid", "audit_logs", type_="check")
    op.create_check_constraint("ck_audit_logs_action_valid", "audit_logs", _check_sql("action", NEW_AUDIT_ACTIONS))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"UPDATE alerts SET type = 'GEOFENCE_BREACH' WHERE type NOT IN {tuple(OLD_FEED_ALERT_TYPES)}"))
    conn.execute(sa.text(f"UPDATE alerts SET status = 'OPEN' WHERE status NOT IN {tuple(OLD_FEED_ALERT_STATUSES)}"))
    conn.execute(sa.text(f"UPDATE security_alerts SET alert_type = 'GEOFENCE_BREACH' WHERE alert_type NOT IN {tuple(OLD_ALERT_TYPES)}"))
    conn.execute(sa.text(f"UPDATE security_alerts SET status = 'OPEN' WHERE status NOT IN {tuple(OLD_SECURITY_ALERT_STATUSES)}"))
    conn.execute(sa.text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_append_only"))
    conn.execute(sa.text(f"UPDATE audit_logs SET action = 'UPDATE' WHERE action NOT IN {tuple(OLD_AUDIT_ACTIONS)}"))
    conn.execute(sa.text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_append_only"))
    conn.execute(sa.text(f"UPDATE gps_events SET source = 'MANUAL' WHERE source NOT IN {tuple(OLD_GPS_SOURCES)}"))
    conn.execute(sa.text(f"UPDATE parking_zones SET status = 'ACTIVE' WHERE status NOT IN {tuple(OLD_ZONE_STATUSES)}"))

    # 6. audit_logs
    op.drop_constraint("ck_audit_logs_action_valid", "audit_logs", type_="check")
    op.create_check_constraint("ck_audit_logs_action_valid", "audit_logs", _check_sql("action", OLD_AUDIT_ACTIONS))

    # 5. users
    op.drop_column("users", "notification_preferences")

    # 4. alerts
    op.drop_constraint("ck_alerts_type_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_type_valid", "alerts", _check_sql("type", OLD_FEED_ALERT_TYPES))
    op.drop_constraint("ck_alerts_status_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_status_valid", "alerts", _check_sql("status", OLD_FEED_ALERT_STATUSES))

    # 3. security_alerts
    op.drop_constraint("ck_security_alerts_alert_type_valid", "security_alerts", type_="check")
    op.create_check_constraint("ck_security_alerts_alert_type_valid", "security_alerts", _check_sql("alert_type", OLD_ALERT_TYPES))
    op.drop_constraint("ck_security_alerts_status_valid", "security_alerts", type_="check")
    op.create_check_constraint("ck_security_alerts_status_valid", "security_alerts", _check_sql("status", OLD_SECURITY_ALERT_STATUSES))
    op.drop_column("security_alerts", "consecutive_count")
    op.drop_column("security_alerts", "tamper_sensor_type")

    # 2. gps_events
    op.execute("DELETE FROM gps_events WHERE custody_session_id IS NULL")
    op.drop_constraint("ck_gps_events_source_valid", "gps_events", type_="check")
    op.create_check_constraint("ck_gps_events_source_valid", "gps_events", _check_sql("source", OLD_GPS_SOURCES))
    op.alter_column("gps_events", "custody_session_id", nullable=False)
    op.drop_column("gps_events", "vehicle_id")
    op.drop_column("gps_events", "boundary_state")
    op.drop_column("gps_events", "sensor_data")

    # 1. parking_zones
    op.drop_constraint("ck_parking_zones_orientation_range", "parking_zones", type_="check")
    op.drop_constraint("ck_parking_zones_type_valid", "parking_zones", type_="check")
    op.drop_constraint("ck_parking_zones_status_valid", "parking_zones", type_="check")
    op.create_check_constraint("ck_parking_zones_status_valid", "parking_zones", _check_sql("status", OLD_ZONE_STATUSES))
    op.drop_column("parking_zones", "created_by_id")
    op.drop_column("parking_zones", "rules")
    op.drop_column("parking_zones", "custody_session_id")
    op.drop_column("parking_zones", "vehicle_id")
    op.drop_column("parking_zones", "orientation_degrees")
    op.drop_column("parking_zones", "width_metres")
    op.drop_column("parking_zones", "length_metres")
    op.drop_column("parking_zones", "zone_type")
