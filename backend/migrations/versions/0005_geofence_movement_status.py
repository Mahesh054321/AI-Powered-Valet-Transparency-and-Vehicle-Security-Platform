"""Store the geofence verdict on each GPS fix, and audit geofence edits.

The geofence module classifies every position fix as ``NORMAL``, ``WARNING``,
``SUSPICIOUS`` or ``BREACH`` from three inputs: how far the fix is from its
zone boundary, how fast the vehicle is going, and how the recent fixes relate
to each other. Before this migration the verdict could only be recomputed on
every read, which meant the security dashboard had to re-run the maths over the
whole track to answer "show me the fixes that needed attention" -- and an
append-only telemetry table is exactly the table that grows without bound.

Storing it is safe precisely *because* the inputs stay on the row:
``zone_id``, ``is_within_zone``, ``distance_from_zone_metres`` and
``speed_kmph`` are all persisted next to the verdict, so the classification can
always be re-derived and compared rather than taken on trust. The
``ck_gps_events_zone_fields_consistent`` CHECK keeps the geofence columns
mutually honest: without a zone to evaluate against, a fix cannot claim to be
inside anything and has no boundary to measure a distance from.

Two audit actions are added on purpose. A geofence edit changes the meaning of
every future fix, so it is recorded as ``GEOFENCE_CONFIGURED`` rather than a
generic ``UPDATE``. A simulated track is recorded as ``GPS_TRACK_SIMULATED`` so
demo telemetry can never be mistaken for a real journey afterwards.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The four verdicts the geofence engine can reach. Mirrors
#: ``models.enums.MovementStatus``.
MOVEMENT_STATUSES = ("NORMAL", "WARNING", "SUSPICIOUS", "BREACH")

#: ``audit_logs.action`` is a closed vocabulary, so widening it is a deliberate
#: act rather than an accident. ``0004`` left it at 22 values.
AUDIT_ACTIONS_BEFORE = (
    "CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT", "LOGIN_FAILED",
    "STATUS_CHANGE", "CHECK_IN", "CHECK_OUT", "ZONE_ASSIGN", "ALERT_RAISED",
    "ALERT_ACKNOWLEDGED", "ALERT_RESOLVED", "INSPECTION_CAPTURED",
    "DAMAGE_FLAGGED", "DAMAGE_VERIFIED", "INSPECTION_DEMO_RUN",
    "RETRIEVAL_REQUESTED", "PREDICTION_GENERATED", "PHOTO_UPLOADED", "EXPORT",
)
NEW_AUDIT_ACTIONS = ("GEOFENCE_CONFIGURED", "GPS_TRACK_SIMULATED")
AUDIT_ACTIONS_AFTER = AUDIT_ACTIONS_BEFORE + NEW_AUDIT_ACTIONS


def _action_check(actions: tuple[str, ...]) -> str:
    """Render the action predicate exactly as migration 0002 did.

    ``0002`` builds this constraint with its ``_check`` helper as
    ``action IN ('A', 'B', ...)``, which PostgreSQL normalises to an
    ``ANY (ARRAY[...]::character varying[])`` form. Spelling it the same way
    matters: ``tests/test_database_models.py`` is a drift guard that parses the
    stored constraint definition to prove the database vocabulary still matches
    the Python enum, and it reads the values out of the ``::character
    varying`` casts. Emitting ``::text[]`` instead renders identically to a
    human but breaks that guard.
    """
    allowed = ", ".join(f"'{action}'" for action in actions)
    return f"action IN ({allowed})"


def _statuses(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.add_column(
        "gps_events",
        sa.Column(
            "movement_status",
            sa.String(16),
            nullable=False,
            # Every fix that predates the geofence engine was never classified,
            # so it is back-filled as benign rather than guessed at. Existing
            # demo telemetry is simulated data anyway, and labelling it
            # ``SUSPICIOUS`` retroactively would be a fabrication.
            server_default=MOVEMENT_STATUSES[0],
            comment="NORMAL | WARNING | SUSPICIOUS | BREACH - rule-based, re-derivable.",
        ),
    )
    op.create_check_constraint(
        "ck_gps_events_movement_status_valid",
        "gps_events",
        _statuses("movement_status", MOVEMENT_STATUSES),
    )
    op.create_check_constraint(
        "ck_gps_events_zone_fields_consistent",
        "gps_events",
        "zone_id IS NOT NULL "
        "OR (is_within_zone = false AND distance_from_zone_metres IS NULL)",
    )
    op.create_index(
        "ix_gps_events_status_not_normal",
        "gps_events",
        [
            sa.text("custody_session_id"),
            sa.text("recorded_at DESC"),
        ],
        postgresql_where=sa.text("movement_status <> 'NORMAL'"),
    )

    op.drop_constraint("ck_audit_logs_action_valid", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action_valid",
        "audit_logs",
        _action_check(AUDIT_ACTIONS_AFTER),
    )


def downgrade() -> None:
    # Narrowing a closed vocabulary fails while rows still use the removed
    # values, so re-label them instead of deleting audit history. The original
    # action is preserved in the description, which is the audit log's own
    # escape hatch: a row is never rewritten into a lie, only re-tagged into a
    # value the older schema understands.
    relabelled = ", ".join(f"'{action}'" for action in NEW_AUDIT_ACTIONS)
    op.execute(
        "UPDATE audit_logs "
        "SET action = 'UPDATE', "
        "    description = COALESCE(description || ' | ', '') || "
        "                  '[downgraded from ' || action || ']' "
        f"WHERE action IN ({relabelled})"
    )

    op.drop_constraint("ck_audit_logs_action_valid", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action_valid",
        "audit_logs",
        _action_check(AUDIT_ACTIONS_BEFORE),
    )

    # Relabel any fix carrying a non-benign verdict before the CHECK that
    # permits it disappears, otherwise the column drop loses that history
    # silently rather than visibly.
    op.execute("UPDATE gps_events SET movement_status = 'NORMAL' "
               "WHERE movement_status <> 'NORMAL'")

    op.drop_index("ix_gps_events_status_not_normal", table_name="gps_events")
    op.drop_constraint("ck_gps_events_zone_fields_consistent", "gps_events", type_="check")
    op.drop_constraint("ck_gps_events_movement_status_valid", "gps_events", type_="check")
    op.drop_column("gps_events", "movement_status")
