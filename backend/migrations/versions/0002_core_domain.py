"""Core domain schema: the eleven tables behind custody transparency.

Creates ``users``, ``vehicles``, ``parking_zones``, ``valet_tickets``,
``custody_sessions``, ``gps_events``, ``vehicle_inspections``,
``damage_detections``, ``security_alerts``, ``retrieval_predictions`` and
``audit_logs``, with their foreign keys, indexes and CHECK constraints.

Two deliberate conventions:

**Status columns are ``VARCHAR`` + ``CHECK``, not native enums.** The permitted
values are written here as literals rather than read from ``models/enums.py``.
A migration is a historical record and must stay deterministic: if a status is
added to the Python enum next year, this file must not silently change meaning.
Drift between the two is caught by ``tests/test_database_models.py``, which
asserts the live database CHECK matches the model.

**No table stores image bytes.** Photographs, QR codes and annotated evidence
live in ``storage/media/`` (or object storage); the schema stores validated
paths plus size, dimensions, MIME type and a SHA-256 digest.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Permitted status values. Literals, on purpose - see the module docstring.
# ---------------------------------------------------------------------------
ROLES = ("CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN")
USER_STATUSES = ("PENDING", "ACTIVE", "SUSPENDED", "DEACTIVATED")
VEHICLE_STATUSES = ("REGISTERED", "IN_CUSTODY", "FLAGGED", "ARCHIVED")
ZONE_STATUSES = ("ACTIVE", "MAINTENANCE", "FULL", "RETIRED")
TICKET_STATUSES = ("ISSUED", "ACTIVE", "COMPLETED", "CANCELLED", "EXPIRED")
CUSTODY_STATUSES = (
    "CHECKED_IN",
    "PARKED",
    "IN_TRANSIT",
    "RETRIEVAL_REQUESTED",
    "RETRIEVAL_IN_PROGRESS",
    "READY_FOR_PICKUP",
    "CHECKED_OUT",
    "DISPUTED",
)
INSPECTION_STAGES = ("DROP_OFF", "PICKUP")
INSPECTION_OUTCOMES = ("PENDING_REVIEW", "CLEAN", "DAMAGE_FOUND", "REVIEWED")
DAMAGE_SEVERITIES = ("COSMETIC", "MINOR", "MODERATE", "SEVERE")
DAMAGE_TYPES = (
    "SCRATCH",
    "DENT",
    "CRACK",
    "PAINT_CHIP",
    "RUST",
    "GLASS_DAMAGE",
    "TYRE_DAMAGE",
    "MISSING_PART",
    "DIRT_STAIN",
    "OTHER",
)
VEHICLE_REGIONS = (
    "FRONT_BUMPER",
    "REAR_BUMPER",
    "HOOD",
    "ROOF",
    "BOOT",
    "LEFT_FRONT_DOOR",
    "LEFT_REAR_DOOR",
    "RIGHT_FRONT_DOOR",
    "RIGHT_REAR_DOOR",
    "LEFT_FENDER",
    "RIGHT_FENDER",
    "WINDSHIELD",
    "REAR_WINDSHIELD",
    "INTERIOR",
    "WHEELS",
    "UNDERCARRIAGE",
    "OTHER",
)
DETECTION_METHODS = ("CLASSICAL_CV", "RULE_BASED", "MANUAL", "ML_MODEL")
ALERT_TYPES = (
    "GEOFENCE_BREACH",
    "ROUTE_DEVIATION",
    "UNUSUAL_SPEED",
    "ABNORMAL_MOVEMENT",
    "UNAUTHORIZED_ACCESS",
    "TAMPER_DETECTED",
    "TELEMETRY_LOSS",
)
ALERT_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED")
TELEMETRY_SOURCES = ("SIMULATED", "HARDWARE", "MANUAL")
PREDICTION_STATUSES = ("PENDING", "DISPATCHED", "FULFILLED", "SUPERSEDED", "EXPIRED")
AUDIT_ACTIONS = (
    "CREATE",
    "UPDATE",
    "DELETE",
    "LOGIN",
    "LOGOUT",
    "LOGIN_FAILED",
    "STATUS_CHANGE",
    "CHECK_IN",
    "CHECK_OUT",
    "ZONE_ASSIGN",
    "ALERT_RAISED",
    "ALERT_ACKNOWLEDGED",
    "ALERT_RESOLVED",
    "INSPECTION_CAPTURED",
    "DAMAGE_FLAGGED",
    "RETRIEVAL_REQUESTED",
    "PREDICTION_GENERATED",
    "PHOTO_UPLOADED",
    "EXPORT",
)
AUDIT_OUTCOMES = ("SUCCESS", "FAILURE", "DENIED")
AUDIT_ENTITY_TYPES = (
    "users",
    "vehicles",
    "parking_zones",
    "valet_tickets",
    "custody_sessions",
    "vehicle_inspections",
    "damage_detections",
    "gps_events",
    "security_alerts",
    "retrieval_predictions",
)

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _check(name: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    # ---------------------------------------------------------------- users
    op.create_table(
        "users",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.String(32),
            nullable=False,
            server_default="CUSTOMER",
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(24)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("deactivated_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.String(500)),
        sa.UniqueConstraint("email", name="uq_users_email"),
        _check("ck_users_role_valid", "role", ROLES),
        _check("ck_users_status_valid", "status", USER_STATUSES),
        sa.CheckConstraint(
            "NOT (status = 'ACTIVE' AND deactivated_at IS NOT NULL)",
            name="ck_users_active_consistency",
        ),
        comment=(
            "Platform accounts for all four roles. Passwords are stored only "
            "as PBKDF2-SHA256 hashes; plaintext is never persisted."
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_role_status", "users", ["role", "status"])
    op.create_index("ix_users_full_name", "users", ["full_name"])

    # ------------------------------------------------------------- vehicles
    op.create_table(
        "vehicles",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("plate_number", sa.String(20), nullable=False),
        sa.Column("normalized_plate", sa.String(20), nullable=False),
        sa.Column("make", sa.String(60), nullable=False),
        sa.Column("model", sa.String(60)),
        sa.Column("year", sa.Integer()),
        sa.Column("color", sa.String(30)),
        sa.Column("vin", sa.String(17)),
        sa.Column("status", sa.String(32), nullable=False, server_default="REGISTERED"),
        sa.Column("notes", sa.String(500)),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_vehicles_owner_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("vin", name="uq_vehicles_vin"),
        _check("ck_vehicles_status_valid", "status", VEHICLE_STATUSES),
        sa.CheckConstraint("year >= 1950 AND year <= 2100", name="ck_vehicles_year_range"),
        sa.CheckConstraint(
            "normalized_plate = upper(normalized_plate)",
            name="ck_vehicles_normalized_plate_is_upper",
        ),
        comment=(
            "Customer vehicles. `normalized_plate` is the uppercase, "
            "separator-free form used for lookups and uniqueness."
        ),
    )
    op.create_index("ix_vehicles_owner_id", "vehicles", ["owner_id"])
    op.create_index("ix_vehicles_created_at", "vehicles", ["created_at"])
    op.create_index("ix_vehicles_plate_number", "vehicles", ["plate_number"])
    op.create_index("ix_vehicles_owner_status", "vehicles", ["owner_id", "status"])
    # Only one non-archived vehicle may hold a given plate.
    op.create_index(
        "uq_vehicles_normalized_plate_active",
        "vehicles",
        ["normalized_plate"],
        unique=True,
        postgresql_where=sa.text("status <> 'ARCHIVED'"),
    )

    # -------------------------------------------------------- parking_zones
    op.create_table(
        "parking_zones",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("geofence_polygon", JSONB, nullable=False),
        sa.Column("centre_latitude", sa.Numeric(9, 6)),
        sa.Column("centre_longitude", sa.Numeric(9, 6)),
        sa.Column("radius_metres", sa.Float()),
        sa.Column("buffer_metres", sa.Numeric(6, 2), nullable=False, server_default="25"),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_occupancy", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("code", name="uq_parking_zones_code"),
        _check("ck_parking_zones_status_valid", "status", ZONE_STATUSES),
        sa.CheckConstraint("capacity > 0", name="ck_parking_zones_capacity_positive"),
        sa.CheckConstraint(
            "current_occupancy >= 0", name="ck_parking_zones_occupancy_non_negative"
        ),
        sa.CheckConstraint(
            "current_occupancy <= capacity",
            name="ck_parking_zones_occupancy_within_capacity",
        ),
        sa.CheckConstraint("buffer_metres >= 0", name="ck_parking_zones_buffer_non_negative"),
        sa.CheckConstraint(
            "radius_metres IS NULL OR radius_metres > 0",
            name="ck_parking_zones_radius_positive",
        ),
        sa.CheckConstraint(
            "centre_latitude IS NULL OR (centre_latitude >= -90 AND centre_latitude <= 90)",
            name="ck_parking_zones_latitude_range",
        ),
        sa.CheckConstraint(
            "centre_longitude IS NULL OR (centre_longitude >= -180 AND centre_longitude <= 180)",
            name="ck_parking_zones_longitude_range",
        ),
        comment=(
            "Named parking areas with a geofence polygon. `geofence_polygon` is "
            "a JSONB list of [longitude, latitude] pairs (WGS-84, GeoJSON "
            "order). No image or binary data is stored here."
        ),
    )
    op.create_index("ix_parking_zones_code", "parking_zones", ["code"])
    op.create_index("ix_parking_zones_created_at", "parking_zones", ["created_at"])
    op.create_index("ix_parking_zones_status", "parking_zones", ["status"])

    # ------------------------------------------------------- valet_tickets
    op.create_table(
        "valet_tickets",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("ticket_code", sa.String(24), nullable=False),
        sa.Column("customer_id", UUID, nullable=False),
        sa.Column("vehicle_id", UUID, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ISSUED"),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expected_return_at", sa.DateTime(timezone=True)),
        sa.Column("actual_drop_off_at", sa.DateTime(timezone=True)),
        sa.Column("actual_pickup_at", sa.DateTime(timezone=True)),
        sa.Column("qr_payload", sa.String(512)),
        sa.Column("qr_code_path", sa.String(1024)),
        sa.Column("special_instructions", sa.String(500)),
        sa.Column("metadata", JSONB),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["users.id"], name="fk_valet_tickets_customer_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name="fk_valet_tickets_vehicle_id_vehicles",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("ticket_code", name="uq_valet_tickets_ticket_code"),
        _check("ck_valet_tickets_status_valid", "status", TICKET_STATUSES),
        sa.CheckConstraint(
            "expected_return_at IS NULL OR issued_at IS NULL OR expected_return_at > issued_at",
            name="ck_valet_tickets_return_after_issue",
        ),
        sa.CheckConstraint(
            "actual_drop_off_at IS NULL OR actual_drop_off_at >= issued_at",
            name="ck_valet_tickets_drop_off_after_issue",
        ),
        sa.CheckConstraint(
            "actual_pickup_at IS NULL OR actual_drop_off_at IS NULL "
            "OR actual_pickup_at >= actual_drop_off_at",
            name="ck_valet_tickets_pickup_after_drop_off",
        ),
        comment=(
            "Digital valet tickets. `qr_code_path` references the rendered QR "
            "image in storage; the image bytes are never stored here."
        ),
    )
    op.create_index("ix_valet_tickets_ticket_code", "valet_tickets", ["ticket_code"])
    op.create_index("ix_valet_tickets_customer_id", "valet_tickets", ["customer_id"])
    op.create_index("ix_valet_tickets_vehicle_id", "valet_tickets", ["vehicle_id"])
    op.create_index("ix_valet_tickets_created_at", "valet_tickets", ["created_at"])
    op.create_index(
        "ix_valet_tickets_customer_status", "valet_tickets", ["customer_id", "status"]
    )

    # ----------------------------------------------------- custody_sessions
    op.create_table(
        "custody_sessions",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("ticket_id", UUID, nullable=False),
        sa.Column("vehicle_id", UUID, nullable=False),
        sa.Column("zone_id", UUID),
        sa.Column("assigned_valet_id", UUID),
        sa.Column("received_by_id", UUID),
        sa.Column("status", sa.String(32), nullable=False, server_default="CHECKED_IN"),
        sa.Column(
            "dropped_off_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("zone_assigned_at", sa.DateTime(timezone=True)),
        sa.Column("retrieval_requested_at", sa.DateTime(timezone=True)),
        sa.Column("retrieval_started_at", sa.DateTime(timezone=True)),
        sa.Column("ready_for_pickup_at", sa.DateTime(timezone=True)),
        sa.Column("picked_up_at", sa.DateTime(timezone=True)),
        sa.Column("expected_retrieval_minutes", sa.Integer()),
        sa.Column("handover_point", sa.String(120)),
        sa.Column("odometer_at_drop_off", sa.Integer()),
        sa.Column("fuel_level_percent", sa.Integer()),
        sa.Column("pre_existing_damage_notes", sa.String(1000)),
        sa.Column("condition_accepted", sa.Boolean()),
        sa.Column("condition_notes", sa.String(1000)),
        sa.Column("mileage_driven", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["valet_tickets.id"],
            name="fk_custody_sessions_ticket_id_tickets", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"],
            name="fk_custody_sessions_vehicle_id_vehicles", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"],
            name="fk_custody_sessions_zone_id_parking_zones", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_valet_id"], ["users.id"],
            name="fk_custody_sessions_assigned_valet_id_users", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["received_by_id"], ["users.id"],
            name="fk_custody_sessions_received_by_id_users", ondelete="RESTRICT",
        ),
        _check("ck_custody_sessions_status_valid", "status", CUSTODY_STATUSES),
        sa.CheckConstraint(
            "dropped_off_at <= COALESCE(picked_up_at, dropped_off_at)",
            name="ck_custody_sessions_pickup_after_drop_off",
        ),
        sa.CheckConstraint(
            "retrieval_requested_at IS NULL OR dropped_off_at IS NULL "
            "OR retrieval_requested_at >= dropped_off_at",
            name="ck_custody_sessions_retrieval_after_drop_off",
        ),
        sa.CheckConstraint(
            "expected_retrieval_minutes IS NULL OR expected_retrieval_minutes > 0",
            name="ck_custody_sessions_expected_retrieval_positive",
        ),
        sa.CheckConstraint(
            "odometer_at_drop_off IS NULL OR odometer_at_drop_off >= 0",
            name="ck_custody_sessions_odometer_non_negative",
        ),
        sa.CheckConstraint(
            "fuel_level_percent IS NULL OR (fuel_level_percent >= 0 AND fuel_level_percent <= 100)",
            name="ck_custody_sessions_fuel_level_range",
        ),
        sa.CheckConstraint(
            "mileage_driven IS NULL OR mileage_driven >= 0",
            name="ck_custody_sessions_mileage_non_negative",
        ),
        # Written as two boolean implications because `status` is a VARCHAR and
        # therefore cannot be compared to a boolean.
        sa.CheckConstraint(
            "status <> 'CHECKED_OUT' OR picked_up_at IS NOT NULL",
            name="ck_custody_sessions_closed_requires_pickup",
        ),
        sa.CheckConstraint(
            "picked_up_at IS NULL OR status = 'CHECKED_OUT'",
            name="ck_custody_sessions_pickup_requires_closed",
        ),
        comment=(
            "The custody window between drop-off and pickup. Parent of GPS "
            "events, inspections, alerts and retrieval predictions."
        ),
    )
    op.create_index("ix_custody_sessions_ticket_id", "custody_sessions", ["ticket_id"])
    op.create_index("ix_custody_sessions_vehicle_id", "custody_sessions", ["vehicle_id"])
    op.create_index("ix_custody_sessions_zone_id", "custody_sessions", ["zone_id"])
    op.create_index("ix_custody_sessions_assigned_valet_id", "custody_sessions", ["assigned_valet_id"])
    op.create_index("ix_custody_sessions_created_at", "custody_sessions", ["created_at"])
    op.create_index("ix_custody_sessions_status", "custody_sessions", ["status"])
    op.create_index(
        "ix_custody_sessions_zone_status", "custody_sessions", ["zone_id", "status"]
    )
    op.create_index(
        "ix_custody_sessions_dropped_off_at", "custody_sessions", ["dropped_off_at"]
    )
    # A vehicle may not be in two open custody sessions at once.
    op.create_index(
        "uq_custody_sessions_vehicle_open",
        "custody_sessions",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'CHECKED_OUT'"),
    )
    # One custody session per ticket.
    op.create_index("uq_custody_sessions_ticket", "custody_sessions", ["ticket_id"], unique=True)

    # ----------------------------------------------------------- gps_events
    op.create_table(
        "gps_events",
        _uuid_pk(),
        # Immutable: a telemetry row is a historical measurement, so there is
        # deliberately no updated_at.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("custody_session_id", UUID, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("speed_kmph", sa.Numeric(6, 2)),
        sa.Column("heading_degrees", sa.Numeric(5, 2)),
        sa.Column("accuracy_metres", sa.Numeric(7, 2)),
        sa.Column("source", sa.String(32), nullable=False, server_default="SIMULATED"),
        sa.Column("device_id", sa.String(64)),
        sa.Column("zone_id", UUID),
        sa.Column("is_within_zone", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("distance_from_zone_metres", sa.Numeric(9, 2)),
        sa.ForeignKeyConstraint(
            ["custody_session_id"], ["custody_sessions.id"],
            name="fk_gps_events_custody_session_id_custody_sessions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"], name="fk_gps_events_zone_id_zones",
            ondelete="SET NULL",
        ),
        _check("ck_gps_events_source_valid", "source", TELEMETRY_SOURCES),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_gps_events_latitude_range"),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180", name="ck_gps_events_longitude_range"
        ),
        sa.CheckConstraint(
            "speed_kmph IS NULL OR speed_kmph >= 0", name="ck_gps_events_speed_non_negative"
        ),
        sa.CheckConstraint(
            "heading_degrees IS NULL OR (heading_degrees >= 0 AND heading_degrees < 360)",
            name="ck_gps_events_heading_range",
        ),
        sa.CheckConstraint(
            "accuracy_metres IS NULL OR accuracy_metres > 0",
            name="ck_gps_events_accuracy_positive",
        ),
        sa.CheckConstraint("is_within_zone IS NOT NULL", name="ck_gps_events_zone_flag_present"),
        comment=(
            "Position fixes during custody. Immutable: a telemetry row is a "
            "historical measurement, so it has no updated_at. `source` "
            "distinguishes SIMULATED from HARDWARE data on every row."
        ),
    )
    op.create_index("ix_gps_events_custody_session_id", "gps_events", ["custody_session_id"])
    op.create_index("ix_gps_events_recorded_at", "gps_events", ["recorded_at"])
    op.create_index("ix_gps_events_source", "gps_events", ["source"])
    op.create_index(
        "ix_gps_events_session_recorded_desc",
        "gps_events",
        [sa.text("custody_session_id"), sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_gps_events_out_of_zone",
        "gps_events",
        [sa.text("custody_session_id"), sa.text("recorded_at DESC")],
        postgresql_where=sa.text("is_within_zone = false"),
    )

    # ------------------------------------------------- vehicle_inspections
    op.create_table(
        "vehicle_inspections",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("custody_session_id", UUID, nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("inspected_by_id", UUID),
        sa.Column("reviewed_by_id", UUID),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("location", sa.String(120)),
        sa.Column("photo_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overall_condition_score", sa.Numeric(5, 2)),
        sa.Column("odometer_reading", sa.Integer()),
        sa.Column("inspector_notes", sa.String(1000)),
        sa.Column(
            "is_new_damage", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.ForeignKeyConstraint(
            ["custody_session_id"], ["custody_sessions.id"],
            name="fk_vehicle_inspections_custody_session_id_custody_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inspected_by_id"], ["users.id"],
            name="fk_vehicle_inspections_inspected_by_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"], ["users.id"],
            name="fk_vehicle_inspections_reviewed_by_id_users", ondelete="SET NULL",
        ),
        _check("ck_vehicle_inspections_stage_valid", "stage", INSPECTION_STAGES),
        _check("ck_vehicle_inspections_outcome_valid", "outcome", INSPECTION_OUTCOMES),
        sa.CheckConstraint(
            "photo_count >= 0", name="ck_vehicle_inspections_photo_count_non_negative"
        ),
        sa.CheckConstraint(
            "damage_count >= 0", name="ck_vehicle_inspections_damage_count_non_negative"
        ),
        sa.CheckConstraint(
            "outcome <> 'DAMAGE_FOUND' OR damage_count > 0",
            name="ck_vehicle_inspections_damage_found_requires_count",
        ),
        sa.CheckConstraint(
            "overall_condition_score IS NULL "
            "OR (overall_condition_score >= 0 AND overall_condition_score <= 100)",
            name="ck_vehicle_inspections_condition_score_range",
        ),
        sa.CheckConstraint(
            "captured_at <= COALESCE(reviewed_at, captured_at)",
            name="ck_vehicle_inspections_review_after_capture",
        ),
        comment=(
            "Condition capture at drop-off and pickup. Stores JSONB references "
            "to images in storage (path, size, dimensions, MIME type, SHA-256) "
            "- never the image bytes themselves."
        ),
    )
    op.create_index(
        "ix_vehicle_inspections_custody_session_id",
        "vehicle_inspections",
        ["custody_session_id"],
    )
    op.create_index("ix_vehicle_inspections_inspected_by_id", "vehicle_inspections", ["inspected_by_id"])
    op.create_index("ix_vehicle_inspections_created_at", "vehicle_inspections", ["created_at"])
    op.create_index("ix_vehicle_inspections_outcome", "vehicle_inspections", ["outcome"])
    op.create_index(
        "ix_vehicle_inspections_session_stage",
        "vehicle_inspections",
        ["custody_session_id", "stage"],
    )
    # Exactly one drop-off and one pickup inspection per custody session.
    op.create_index(
        "uq_vehicle_inspections_session_stage",
        "vehicle_inspections",
        ["custody_session_id", "stage"],
        unique=True,
    )

    # ---------------------------------------------------- damage_detections
    op.create_table(
        "damage_detections",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("inspection_id", UUID, nullable=False),
        sa.Column("damage_type", sa.String(32), nullable=False),
        sa.Column("region", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="COSMETIC"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column(
            "is_pre_existing", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "requires_customer_attention",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("bounding_box", JSONB, nullable=False),
        sa.Column("detection_reasoning", sa.String(1000), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="CLASSICAL_CV"),
        sa.Column("model_version", sa.String(64)),
        sa.Column("algorithm_params", JSONB),
        sa.Column("source_image_path", sa.String(1024)),
        sa.Column("evidence_image_path", sa.String(1024)),
        sa.Column("comparison_image_path", sa.String(1024)),
        sa.Column("pixel_area", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["inspection_id"], ["vehicle_inspections.id"],
            name="fk_damage_detections_inspection_id_inspections", ondelete="CASCADE",
        ),
        _check("ck_damage_detections_severity_valid", "severity", DAMAGE_SEVERITIES),
        _check("ck_damage_detections_damage_type_valid", "damage_type", DAMAGE_TYPES),
        _check("ck_damage_detections_region_valid", "region", VEHICLE_REGIONS),
        _check("ck_damage_detections_method_valid", "method", DETECTION_METHODS),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_damage_detections_confidence_is_probability",
        ),
        sa.CheckConstraint(
            "detection_reasoning IS NOT NULL AND length(detection_reasoning) > 0",
            name="ck_damage_detections_reasoning_required",
        ),
        sa.CheckConstraint(
            "method <> 'ML_MODEL' OR model_version IS NOT NULL",
            name="ck_damage_detections_ml_requires_model_version",
        ),
        sa.CheckConstraint(
            "is_pre_existing IS NOT NULL", name="ck_damage_detections_pre_existing_present"
        ),
        sa.CheckConstraint(
            "pixel_area IS NULL OR pixel_area > 0",
            name="ck_damage_detections_pixel_area_positive",
        ),
        comment=(
            "Individual damage findings with region, confidence, reasoning and "
            "an evidence-image reference. Stores no image bytes; `method` "
            "records honestly how the finding was produced."
        ),
    )
    op.create_index("ix_damage_detections_inspection_id", "damage_detections", ["inspection_id"])
    op.create_index("ix_damage_detections_created_at", "damage_detections", ["created_at"])
    op.create_index(
        "ix_damage_detections_inspection_severity",
        "damage_detections",
        ["inspection_id", "severity"],
    )
    op.create_index("ix_damage_detections_damage_type", "damage_detections", ["damage_type"])
    op.create_index(
        "ix_damage_detections_new_only",
        "damage_detections",
        ["inspection_id"],
        postgresql_where=sa.text("is_pre_existing = false"),
    )

    # ------------------------------------------------------ security_alerts
    op.create_table(
        "security_alerts",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("custody_session_id", UUID, nullable=False),
        sa.Column("vehicle_id", UUID),
        sa.Column("zone_id", UUID),
        sa.Column("gps_event_id", UUID),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="LOW"),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("evidence", JSONB),
        sa.Column("evidence_image_path", sa.String(1024)),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("acknowledged_by_id", UUID),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_id", UUID),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_notes", sa.String(1000)),
        sa.ForeignKeyConstraint(
            ["custody_session_id"], ["custody_sessions.id"],
            name="fk_security_alerts_custody_session_id_custody_sessions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"],
            name="fk_security_alerts_vehicle_id_vehicles", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["parking_zones.id"],
            name="fk_security_alerts_zone_id_zones", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["gps_event_id"], ["gps_events.id"],
            name="fk_security_alerts_gps_event_id_gps_events", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_id"], ["users.id"],
            name="fk_security_alerts_acknowledged_by_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"], ["users.id"],
            name="fk_security_alerts_resolved_by_id_users", ondelete="SET NULL",
        ),
        _check("ck_security_alerts_alert_type_valid", "alert_type", ALERT_TYPES),
        _check("ck_security_alerts_severity_valid", "severity", ALERT_SEVERITIES),
        _check("ck_security_alerts_status_valid", "status", ALERT_STATUSES),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_security_alerts_confidence_is_probability",
        ),
        sa.CheckConstraint(
            "reason IS NOT NULL AND length(reason) > 0",
            name="ck_security_alerts_reason_required",
        ),
        sa.CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_by_id IS NOT NULL",
            name="ck_security_alerts_ack_requires_actor",
        ),
        sa.CheckConstraint(
            "acknowledged_by_id IS NULL OR acknowledged_at IS NOT NULL",
            name="ck_security_alerts_actor_requires_ack_time",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR acknowledged_at IS NULL OR resolved_at >= acknowledged_at",
            name="ck_security_alerts_resolve_after_ack",
        ),
        sa.CheckConstraint(
            "occurred_at <= COALESCE(acknowledged_at, occurred_at)",
            name="ck_security_alerts_ack_after_occurrence",
        ),
        sa.CheckConstraint(
            "status NOT IN ('RESOLVED', 'DISMISSED') OR resolved_at IS NOT NULL",
            name="ck_security_alerts_terminal_requires_resolution_time",
        ),
        comment=(
            "Geofence and anomaly findings with severity, confidence and a "
            "human-readable reason. Acknowledgement and resolution are "
            "attributed and timestamped for audit."
        ),
    )
    op.create_index(
        "ix_security_alerts_custody_session_id", "security_alerts", ["custody_session_id"]
    )
    op.create_index("ix_security_alerts_vehicle_id", "security_alerts", ["vehicle_id"])
    op.create_index("ix_security_alerts_zone_id", "security_alerts", ["zone_id"])
    op.create_index("ix_security_alerts_acknowledged_by_id", "security_alerts", ["acknowledged_by_id"])
    op.create_index("ix_security_alerts_created_at", "security_alerts", ["created_at"])
    op.create_index("ix_security_alerts_occurred_at", "security_alerts", ["occurred_at"])
    op.create_index(
        "ix_security_alerts_status_severity", "security_alerts", ["status", "severity"]
    )
    op.create_index(
        "ix_security_alerts_session_occurred",
        "security_alerts",
        [sa.text("custody_session_id"), sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_security_alerts_open_queue",
        "security_alerts",
        [sa.text("severity"), sa.text("occurred_at DESC")],
        postgresql_where=sa.text("status IN ('OPEN', 'ACKNOWLEDGED', 'INVESTIGATING')"),
    )

    # ------------------------------------------------- retrieval_predictions
    op.create_table(
        "retrieval_predictions",
        _uuid_pk(),
        *_timestamps(),
        sa.Column("custody_session_id", UUID, nullable=False),
        sa.Column("requested_by_id", UUID),
        sa.Column("assigned_valet_id", UUID),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("predicted_ready_at", sa.DateTime(timezone=True)),
        sa.Column("actual_ready_at", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_after", sa.DateTime(timezone=True)),
        sa.Column("predicted_wait_minutes", sa.Integer(), nullable=False),
        sa.Column("min_wait_minutes", sa.Integer()),
        sa.Column("max_wait_minutes", sa.Integer()),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("queue_position", sa.Integer()),
        sa.Column(
            "method", sa.String(64), nullable=False, server_default="heuristic-queue-v1"
        ),
        sa.Column("model_version", sa.String(64)),
        sa.Column("inputs_snapshot", JSONB),
        sa.Column("explanation", sa.String(1000)),
        sa.ForeignKeyConstraint(
            ["custody_session_id"], ["custody_sessions.id"],
            name="fk_retrieval_predictions_custody_session_id_custody_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"],
            name="fk_retrieval_predictions_requested_by_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_valet_id"], ["users.id"],
            name="fk_retrieval_predictions_assigned_valet_id_users", ondelete="SET NULL",
        ),
        _check("ck_retrieval_predictions_status_valid", "status", PREDICTION_STATUSES),
        sa.CheckConstraint(
            "predicted_wait_minutes >= 0",
            name="ck_retrieval_predictions_wait_non_negative",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_retrieval_predictions_confidence_is_probability",
        ),
        sa.CheckConstraint(
            "queue_position IS NULL OR queue_position >= 1",
            name="ck_retrieval_predictions_queue_position_positive",
        ),
        sa.CheckConstraint(
            "min_wait_minutes IS NULL OR max_wait_minutes IS NULL "
            "OR min_wait_minutes <= max_wait_minutes",
            name="ck_retrieval_predictions_wait_interval_ordered",
        ),
        sa.CheckConstraint(
            "predicted_wait_minutes IS NULL OR min_wait_minutes IS NULL "
            "OR predicted_wait_minutes >= min_wait_minutes",
            name="ck_retrieval_predictions_wait_within_interval_low",
        ),
        sa.CheckConstraint(
            "predicted_wait_minutes IS NULL OR max_wait_minutes IS NULL "
            "OR predicted_wait_minutes <= max_wait_minutes",
            name="ck_retrieval_predictions_wait_within_interval_high",
        ),
        sa.CheckConstraint(
            "dispatch_after IS NULL OR requested_at IS NULL OR dispatch_after >= requested_at",
            name="ck_retrieval_predictions_dispatch_after_request",
        ),
        sa.CheckConstraint(
            "actual_ready_at IS NULL OR requested_at IS NULL OR actual_ready_at >= requested_at",
            name="ck_retrieval_predictions_ready_after_request",
        ),
        comment=(
            "Predicted retrieval times with confidence, queue position and the "
            "inputs that produced the estimate, so the reasoning is reviewable. "
            "Historical inputs are synthetic in this MVP."
        ),
    )
    op.create_index(
        "ix_retrieval_predictions_custody_session_id",
        "retrieval_predictions",
        ["custody_session_id"],
    )
    op.create_index(
        "ix_retrieval_predictions_assigned_valet_id",
        "retrieval_predictions",
        ["assigned_valet_id"],
    )
    op.create_index("ix_retrieval_predictions_created_at", "retrieval_predictions", ["created_at"])
    op.create_index("ix_retrieval_predictions_status", "retrieval_predictions", ["status"])
    op.create_index(
        "ix_retrieval_predictions_session_requested",
        "retrieval_predictions",
        [sa.text("custody_session_id"), sa.text("requested_at DESC")],
    )

    # ---------------------------------------------------------- audit_logs
    op.create_table(
        "audit_logs",
        _uuid_pk(),
        # Append-only: no updated_at, and no cascading delete from any table
        # this one references.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("actor_id", UUID),
        sa.Column("actor_role", sa.String(32)),
        sa.Column("actor_email", sa.String(254)),
        sa.Column("actor_deleted_at", sa.DateTime(timezone=True)),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="SUCCESS"),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", UUID),
        sa.Column("old_values", JSONB),
        sa.Column("new_values", JSONB),
        sa.Column("description", sa.String(500)),
        sa.Column("failure_reason", sa.String(500)),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_id", sa.String(64)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("custody_session_id", UUID),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_audit_logs_actor_id_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["custody_session_id"], ["custody_sessions.id"],
            name="fk_audit_logs_custody_session_id_custody_sessions", ondelete="SET NULL",
        ),
        _check("ck_audit_logs_action_valid", "action", AUDIT_ACTIONS),
        _check("ck_audit_logs_outcome_valid", "outcome", AUDIT_OUTCOMES),
        _check("ck_audit_logs_actor_role_valid", "actor_role", ROLES),
        _check("ck_audit_logs_entity_type_known", "entity_type", AUDIT_ENTITY_TYPES),
        # An actor snapshot may only be attributed to a real account, so a
        # role can never be written without an actor_id. The reverse is *not*
        # constrained: when a user is deleted, ON DELETE SET NULL clears
        # actor_id while the snapshot columns deliberately survive, so the
        # trail still records who acted. Constraining both directions here
        # would make deleting any audited user impossible.
        sa.CheckConstraint(
            "actor_role IS NULL OR actor_id IS NOT NULL OR actor_deleted_at IS NOT NULL",
            name="ck_audit_logs_snapshot_attributed_or_tombstoned",
        ),
        sa.CheckConstraint(
            "outcome = 'SUCCESS' OR (failure_reason IS NOT NULL AND length(failure_reason) > 0)",
            name="ck_audit_logs_failure_requires_reason",
        ),
        comment=(
            "Append-only audit trail. No updated_at and no cascading deletes: "
            "actor details are snapshotted so the trail survives the deletion "
            "of the rows it describes."
        ),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_custody_session_id", "audit_logs", ["custody_session_id"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index(
        "ix_audit_logs_custody_occurred",
        "audit_logs",
        [sa.text("custody_session_id"), sa.text("occurred_at DESC")],
    )

    # ------------------------------------------------------------------
    # Tombstone the audit trail when an actor is deleted.
    #
    # audit_logs.actor_id is ON DELETE SET NULL so the trail survives the
    # account it describes, but that alone leaves a row indistinguishable from
    # one that was never attributed to anybody. This trigger records *when*
    # the actor went away, which is what makes the retained snapshot provably
    # deliberate and satisfies ck_audit_logs_snapshot_attributed_or_tombstoned.
    #
    # It must be BEFORE DELETE, not AFTER: the referential SET NULL is an
    # immediate foreign-key action that runs before any AFTER trigger, so an
    # AFTER trigger would only tombstone the row after the CHECK had already
    # rejected it. BEFORE runs first, and both changes are then validated
    # together at the end of the statement.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION set_actor_tombstone_on_user_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE audit_logs
               SET actor_deleted_at = now()
             WHERE actor_id = OLD.id
               AND actor_deleted_at IS NULL;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_tombstone_actor
        BEFORE DELETE ON users
        FOR EACH ROW
        EXECUTE FUNCTION set_actor_tombstone_on_user_delete()
        """
    )

    # The audit-log timestamp columns are `created_at` (row insertion) and
    # `occurred_at` (the action); the TimestampMixin's `updated_at` is
    # deliberately absent because the table is append-only.


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_tombstone_actor ON users")
    op.execute("DROP FUNCTION IF EXISTS set_actor_tombstone_on_user_delete()")

    # Reverse dependency order so no foreign key is dropped while referenced.
    for table in (
        "audit_logs",
        "retrieval_predictions",
        "security_alerts",
        "damage_detections",
        "vehicle_inspections",
        "gps_events",
        "custody_sessions",
        "valet_tickets",
        "parking_zones",
        "vehicles",
        "users",
    ):
        op.drop_table(table)
