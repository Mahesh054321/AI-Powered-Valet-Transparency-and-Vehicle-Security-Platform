"""Add the centralized alert feed and the in-app notification ledger.

**Why a second alert table rather than reusing ``security_alerts``.** The
existing table is the geofence engine's own record: it points at a GPS fix, a
zone, and a vision evidence blob, and it is written in exactly one place. This
migration adds a *feed* that spans three domains — geofence events, damage
findings, and retrieval readiness — which is a different thing. A geofence row
that had to carry a nullable ``inspection_id`` and a nullable ``prediction_id``
would stop being a geofence record and become a grab-bag.

So ``security_alerts`` stays authoritative for what the geofence detected, and
``alerts`` is the unified view users actually read. Each alert points back at
its origin through ``source_entity_type``/``source_id``, so nothing is orphaned
and the evidence chain survives.

**Read state is per recipient, not on the alert.** ``alert_reads`` holds one row
per (alert, user). A single ``READ`` status on the alert would mean whoever
opened the app first cleared it for everyone — a manager could miss a CRITICAL
breach because a customer glanced at their phone. The alert's own ``status``
therefore tracks only its lifecycle (OPEN / RESOLVED / DISMISSED), and "have I
seen this" is a separate question with a separate answer per person.

**``notifications`` is a delivery ledger, not a queue.** Each row records one
attempt to reach one person over one channel. For the local MVP the only
implemented channel is ``IN_APP``; ``EMAIL``, ``SMS`` and ``PUSH`` exist in the
vocabulary so the channel abstraction has somewhere to write, but nothing
produces them yet. A row is written whether delivery succeeded or failed, so a
notification that silently went nowhere is visible rather than lost.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The five categories the centralized feed exposes. Deliberately coarser than
#: ``models.enums.AlertType``: a manager triaging a feed needs "something moved
#: that should not have", not the detector's internal distinction between
#: tamper and unauthorised access. The precise type is preserved on the source
#: row and mirrored into ``alerts.source_type``.
ALERT_TYPES = (
    "GEOFENCE_BREACH",
    "SUSPICIOUS_MOVEMENT",
    "OVERSPEED",
    "POTENTIAL_DAMAGE",
    "RETRIEVAL_READY",
)

ALERT_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

#: Lifecycle only. "Have I read this" lives in ``alert_reads``.
ALERT_STATUSES = ("OPEN", "RESOLVED", "DISMISSED")

#: Roles an alert can be addressed to. Stored as a JSONB array so one alert can
#: legitimately concern several audiences at once — a breach is both an operator
#: problem and something the customer is entitled to know about.
ALERT_AUDIENCES = ("CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN")

NOTIFICATION_CHANNELS = ("IN_APP", "EMAIL", "SMS", "PUSH")

NOTIFICATION_STATUSES = ("PENDING", "DELIVERED", "FAILED")

#: ``audit_logs.entity_type`` is a closed vocabulary; two tables are being added
#: to the schema, so two names are added here.
AUDIT_ENTITY_TYPES_BEFORE = (
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
NEW_ENTITY_TYPES = ("alerts", "notifications")
AUDIT_ENTITY_TYPES_AFTER = AUDIT_ENTITY_TYPES_BEFORE + NEW_ENTITY_TYPES

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _check(name: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _entity_check(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entity_type IN ({allowed})"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # alerts
    # ------------------------------------------------------------------
    op.create_table(
        "alerts",
        _uuid_pk(),
        # Nullable on purpose: a fleet-level alert may not concern one car, and
        # forcing a custody session would invent a link that does not exist.
        sa.Column("custody_session_id", UUID, sa.ForeignKey("custody_sessions.id", ondelete="CASCADE")),
        sa.Column("vehicle_id", UUID, sa.ForeignKey("vehicles.id", ondelete="SET NULL")),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("message", sa.String(500), nullable=False),
        # Where this alert came from, so the feed can link back to the evidence
        # rather than restating it and letting the two drift apart.
        sa.Column("source_entity_type", sa.String(40)),
        sa.Column("source_id", UUID),
        # The precise vocabulary of the originating subsystem, kept because the
        # five categories above are a lossy projection of it.
        sa.Column("source_type", sa.String(40)),
        sa.Column("audiences", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raised_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_by_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_notes", sa.String(500)),
        sa.Column("metadata", JSONB),
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
        _check("ck_alerts_type_valid", "type", ALERT_TYPES),
        _check("ck_alerts_severity_valid", "severity", ALERT_SEVERITIES),
        _check("ck_alerts_status_valid", "status", ALERT_STATUSES),
        # A resolution with no resolver is an unattributed decision. The schema
        # refuses it for the same reason it refuses a verified damage finding
        # with no reviewer: "someone dealt with this" has to name the someone.
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_by_id IS NOT NULL",
            name="ck_alerts_resolution_attributed",
        ),
        # An alert addressed to nobody would be written and then seen by
        # nobody, which is a silent failure rather than an empty feed.
        sa.CheckConstraint(
            "jsonb_array_length(audiences) > 0",
            name="ck_alerts_has_audience",
        ),
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_type", "alerts", ["type"])
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_vehicle_id", "alerts", ["vehicle_id"])
    op.create_index("ix_alerts_custody_session_id", "alerts", ["custody_session_id"])
    # One alert per originating row per type. Re-raising the same finding every
    # time a GPS fix arrives would bury the feed in duplicates of one event.
    op.create_index(
        "uq_alerts_source",
        "alerts",
        ["source_entity_type", "source_id", "type"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # alert_reads
    # ------------------------------------------------------------------
    op.create_table(
        "alert_reads",
        _uuid_pk(),
        sa.Column("alert_id", UUID, sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # The row's insertion time *is* the read time, so this is `created_at`
        # rather than a separate `read_at` that could only ever equal it. The
        # table is append-only: a read is never edited, only recorded.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The whole point of the table: one read marker per person per alert.
    op.create_index(
        "uq_alert_reads_alert_user", "alert_reads", ["alert_id", "user_id"], unique=True
    )
    op.create_index("ix_alert_reads_user_id", "alert_reads", ["user_id"])

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    op.create_table(
        "notifications",
        _uuid_pk(),
        sa.Column("alert_id", UUID, sa.ForeignKey("alerts.id", ondelete="CASCADE")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="IN_APP"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        # What the provider said, kept so a failed delivery can be diagnosed
        # rather than merely observed.
        sa.Column("provider_message_id", sa.String(200)),
        sa.Column("failure_reason", sa.String(500)),
        # Recipient attention, kept apart from `status`: delivery outcome and
        # whether the person has looked are different facts, and overwriting
        # one with the other would lose the record of what was delivered.
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", JSONB),
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
        _check("ck_notifications_channel_valid", "channel", NOTIFICATION_CHANNELS),
        _check("ck_notifications_status_valid", "status", NOTIFICATION_STATUSES),
        # A delivered notification with no timestamp cannot be ordered against
        # the others, and a failed one with no reason cannot be diagnosed.
        sa.CheckConstraint(
            "status <> 'DELIVERED' OR delivered_at IS NOT NULL",
            name="ck_notifications_delivery_timed",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_notifications_failure_explained",
        ),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_alert_id", "notifications", ["alert_id"])

    # ------------------------------------------------------------------
    # Widen the audit vocabulary to cover the new tables.
    # ------------------------------------------------------------------
    op.drop_constraint(
        "ck_audit_logs_entity_type_known", "audit_logs", type_="check"
    )
    op.create_check_constraint(
        "ck_audit_logs_entity_type_known",
        "audit_logs",
        _entity_check(AUDIT_ENTITY_TYPES_AFTER),
    )


def downgrade() -> None:
    # Order matters, and getting it wrong makes the downgrade fail outright.
    #
    # Narrowing `ck_audit_logs_entity_type_known` while audit rows still say
    # entity_type = 'alerts' violates the very constraint being added, so the
    # rows that name the departing tables have to go first. Dropping the tables
    # is not enough on its own: `audit_logs.entity_id` is an untyped UUID with no
    # foreign key, so those rows survive the drop and still trip the CHECK.
    op.execute(
        "DELETE FROM audit_logs WHERE entity_type IN ('alerts', 'notifications')"
    )

    op.drop_table("notifications")
    op.drop_table("alert_reads")
    op.drop_table("alerts")

    op.drop_constraint(
        "ck_audit_logs_entity_type_known", "audit_logs", type_="check"
    )
    op.create_check_constraint(
        "ck_audit_logs_entity_type_known",
        "audit_logs",
        _entity_check(AUDIT_ENTITY_TYPES_BEFORE),
    )
