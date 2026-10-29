"""Add manual verification to damage findings, and split confidence in two.

Two problems this migration fixes:

**Findings had no review lifecycle.** The condition pipeline produces
*candidates*, and a candidate nobody has looked at is not the same thing as one
a manager has confirmed. Without a status column every finding looked equally
settled, which is exactly the impression a heuristic must not give.

**One confidence column was doing two jobs.** "There is a significant change in
this region" and "that change is a scratch" are not the same claim, and the
second is much weaker - the region comes from pixel differencing, the label from
a shape-and-colour heuristic. Collapsing them into one number would let a
well-measured region imply a well-guessed label. So ``confidence`` keeps its
original meaning (confidence that the region changed) and ``type_confidence``
carries the weaker claim about what kind of change it is.

The CHECK constraints make an unverified row impossible to mistake for a
verified one: anything past ``UNVERIFIED`` must carry who verified it and when.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

#: The review lifecycle. Kept short on purpose: a finding is unreviewed, or a
#: human has looked at it and either stood behind it or dismissed it.
VERIFICATION_STATUSES = ("UNVERIFIED", "CONFIRMED", "REJECTED")

#: ``audit_logs.action`` is a closed vocabulary, enforced by a CHECK constraint
#: so that a typo cannot silently fragment the log into near-duplicate actions.
#: Adding an action therefore means widening that constraint on purpose.
AUDIT_ACTIONS_BEFORE = (
    "CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT", "LOGIN_FAILED",
    "STATUS_CHANGE", "CHECK_IN", "CHECK_OUT", "ZONE_ASSIGN", "ALERT_RAISED",
    "ALERT_ACKNOWLEDGED", "ALERT_RESOLVED", "INSPECTION_CAPTURED",
    "DAMAGE_FLAGGED", "RETRIEVAL_REQUESTED", "PREDICTION_GENERATED",
    "PHOTO_UPLOADED", "EXPORT",
)
NEW_AUDIT_ACTIONS = ("DAMAGE_VERIFIED", "INSPECTION_DEMO_RUN")
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


def upgrade() -> None:
    op.add_column(
        "damage_detections",
        sa.Column(
            "verification_status",
            sa.String(32),
            nullable=False,
            server_default="UNVERIFIED",
            comment=(
                "Review lifecycle. UNVERIFIED means the algorithm's guess has "
                "not been looked at by a human yet - never present it as a "
                "finding of fact."
            ),
        ),
    )
    op.add_column(
        "damage_detections",
        sa.Column(
            "verified_by_id",
            UUID,
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_damage_detections_verified_by_id_users",
            ),
            nullable=True,
            comment="The reviewer who confirmed or rejected this finding.",
        ),
    )
    op.add_column(
        "damage_detections",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "damage_detections",
        sa.Column(
            "verification_notes",
            sa.String(500),
            nullable=True,
            comment="Why the reviewer agreed or disagreed, in their words.",
        ),
    )
    op.add_column(
        "damage_detections",
        sa.Column(
            "type_confidence",
            sa.Numeric(4, 3),
            nullable=True,
            comment=(
                "Confidence in the damage_type LABEL, separate from "
                "`confidence` which covers only the region. The label is a "
                "shape/colour heuristic and is normally lower."
            ),
        ),
    )

    op.create_check_constraint(
        "ck_damage_detections_verification_status_valid",
        "damage_detections",
        "verification_status IN ("
        + ", ".join(f"'{status}'" for status in VERIFICATION_STATUSES)
        + ")",
    )
    # A finding cannot claim a human reviewed it without naming who and when.
    op.create_check_constraint(
        "ck_damage_detections_verified_requires_reviewer",
        "damage_detections",
        "verification_status = 'UNVERIFIED' OR (verified_by_id IS NOT NULL "
        "AND verified_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_damage_detections_unverified_has_no_reviewer",
        "damage_detections",
        "verification_status <> 'UNVERIFIED' OR verified_by_id IS NULL",
    )
    op.create_check_constraint(
        "ck_damage_detections_type_confidence_is_probability",
        "damage_detections",
        "type_confidence IS NULL OR (type_confidence >= 0 AND type_confidence <= 1)",
    )

    # The review queue is the hot query: everything still awaiting a human.
    op.create_index(
        "ix_damage_detections_awaiting_review",
        "damage_detections",
        ["inspection_id"],
        postgresql_where=sa.text("verification_status = 'UNVERIFIED'"),
    )
    op.create_index(
        "ix_damage_detections_verification_status",
        "damage_detections",
        ["verification_status"],
    )
    op.create_index(
        "ix_damage_detections_verified_by",
        "damage_detections",
        ["verified_by_id"],
    )


    # -- widen the audit vocabulary -----------------------------------------
    # Verification is a new kind of auditable event and the most important one
    # in this module: it records which human stood behind a machine's guess.
    op.drop_constraint(
        "ck_audit_logs_action_valid", "audit_logs", type_="check"
    )
    op.create_check_constraint(
        "ck_audit_logs_action_valid",
        "audit_logs",
        _action_check(AUDIT_ACTIONS_AFTER),
    )


def downgrade() -> None:
    # Narrowing a closed vocabulary cannot succeed while rows still carry the
    # removed values, and this suite downgrades on every fixture. Those rows
    # describe a feature this downgrade deletes, so they are re-labelled rather
    # than dropped: the original action is preserved in the description, the
    # audit trail stays append-only, and the schema can still go back.
    removed = ", ".join(f"'{action}'" for action in NEW_AUDIT_ACTIONS)
    op.execute(
        "UPDATE audit_logs "
        "SET description = '[' || action || '] ' || COALESCE(description, ''), "
        "    action = 'UPDATE' "
        f"WHERE action IN ({removed})"
    )

    op.drop_constraint(
        "ck_audit_logs_action_valid", "audit_logs", type_="check"
    )
    op.create_check_constraint(
        "ck_audit_logs_action_valid",
        "audit_logs",
        _action_check(AUDIT_ACTIONS_BEFORE),
    )
    op.drop_index("ix_damage_detections_verified_by", table_name="damage_detections")
    op.drop_index("ix_damage_detections_verification_status", table_name="damage_detections")
    op.drop_index("ix_damage_detections_awaiting_review", table_name="damage_detections")
    op.drop_constraint(
        "ck_damage_detections_type_confidence_is_probability",
        "damage_detections",
        type_="check",
    )
    op.drop_constraint(
        "ck_damage_detections_unverified_has_no_reviewer",
        "damage_detections",
        type_="check",
    )
    op.drop_constraint(
        "ck_damage_detections_verified_requires_reviewer",
        "damage_detections",
        type_="check",
    )
    op.drop_constraint(
        "ck_damage_detections_verification_status_valid",
        "damage_detections",
        type_="check",
    )
    op.drop_column("damage_detections", "type_confidence")
    op.drop_column("damage_detections", "verification_notes")
    op.drop_column("damage_detections", "verified_at")
    op.drop_column("damage_detections", "verified_by_id")
    op.drop_column("damage_detections", "verification_status")
