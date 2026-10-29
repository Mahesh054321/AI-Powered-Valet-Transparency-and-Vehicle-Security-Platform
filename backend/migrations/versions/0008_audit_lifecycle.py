"""Audit trail: lifecycle vocabulary, event metadata, and an append-only guard.

Three changes, all in service of one requirement -- that the audit log be
something an evaluator can actually read as a record of what happened to a
vehicle.

**1. The vocabulary is renamed to the custody lifecycle.** The log previously
recorded ``CHECK_IN``, ``PHOTO_UPLOADED``, ``ALERT_RAISED`` and so on: names
that describe which code path ran. This migration renames them to names that
describe what happened to the car -- ``VEHICLE_CHECKED_IN``,
``IMAGE_UPLOADED``, ``SECURITY_ALERT_CREATED`` -- and adds the events that had
no name at all (``CUSTODY_STARTED``, ``GPS_BREACH``, ``CUSTODY_COMPLETED``,
``PICKUP_IMAGE_CAPTURED``, ``VEHICLE_CREATED``, ``TICKET_CREATED``).

Existing rows are **renamed, not deleted**. An audit trail that discarded its
history to adopt a nicer vocabulary would defeat its own purpose, so the
mapping is applied with ``UPDATE`` and the downgrade reverses it exactly.

The rename happens *before* the CHECK constraint is narrowed. Doing it the
other way round would have the new constraint reject the very rows the rename
has not reached yet.

**2. ``metadata`` gives an event somewhere to put its context.** ``new_values``
answers "what changed on the row?". It cannot answer "what was the model
confidence?", "which synthetic scenario produced this track?", "how far outside
the fence was the fix?" -- those are properties of the moment, not diffs. They
now have a column.

**3. A trigger makes the log append-only at the database level.** The table
already had no ``updated_at`` and no cascading deletes, which is append-only by
convention. A reviewer is entitled to something stronger than convention: any
``UPDATE`` or ``DELETE`` against ``audit_logs`` now raises, whatever issued it.
The application has no code path that needs either, so nothing legitimate
breaks -- but a stray admin console session, a future careless migration, or a
SQL-injection payload cannot quietly rewrite history.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Old name -> new name. Applied to existing rows on upgrade and reversed on
#: downgrade, so no history is orphaned by the rename.
RENAMED_ACTIONS: dict[str, str] = {
    "LOGIN": "USER_LOGIN",
    "CHECK_IN": "VEHICLE_CHECKED_IN",
    "CHECK_OUT": "VEHICLE_PICKED_UP",
    "PHOTO_UPLOADED": "IMAGE_UPLOADED",
    "ALERT_RAISED": "SECURITY_ALERT_CREATED",
    "DAMAGE_FLAGGED": "DAMAGE_DETECTED",
    "RETRIEVAL_REQUESTED": "PICKUP_REQUESTED",
    "PREDICTION_GENERATED": "RETRIEVAL_PREDICTED",
}

#: The fourteen lifecycle events.
LIFECYCLE_ACTIONS = (
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
)

#: Supporting events that survive unchanged.
SUPPORTING_ACTIONS = (
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

NEW_ACTIONS = LIFECYCLE_ACTIONS + SUPPORTING_ACTIONS

#: Reverse of ``RENAMED_ACTIONS``, used to rebuild the pre-migration vocabulary
#: on downgrade. Built once rather than per iteration.
_UNRENAMED: dict[str, str] = {new: old for old, new in RENAMED_ACTIONS.items()}

#: Lifecycle names that had no predecessor. They did not exist before this
#: migration, so they drop out of the downgraded vocabulary.
_ACTIONS_WITHOUT_PREDECESSOR = frozenset(
    {
        "CUSTODY_STARTED",
        "GPS_BREACH",
        "CUSTODY_COMPLETED",
        "PICKUP_IMAGE_CAPTURED",
        "VEHICLE_CREATED",
        "TICKET_CREATED",
    }
)

#: What the vocabulary was before this migration. Needed to rebuild the old
#: constraint on downgrade.
OLD_ACTIONS = tuple(
    _UNRENAMED.get(name, name)
    for name in NEW_ACTIONS
    if name not in _ACTIONS_WITHOUT_PREDECESSOR
)

CONSTRAINT_NAME = "ck_audit_logs_action_valid"
TRIGGER_NAME = "trg_audit_logs_append_only"
DELETE_TRIGGER_NAME = "trg_audit_logs_no_delete"
FUNCTION_NAME = "fn_audit_logs_reject_mutation"


def _action_check(values: Sequence[str]) -> str:
    """SQL predicate listing the permitted action names."""
    return "action IN ({})".format(", ".join(repr(value) for value in values))


def upgrade() -> None:
    # -- 1. metadata and a monotonic sequence ------------------------------ #
    op.add_column(
        "audit_logs",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Context for the event rather than a diff of the row: model "
                "confidence, simulation scenario, breach distance. NULL for "
                "events with nothing to add."
            ),
        ),
    )
    # ``occurred_at`` is not enough to order the log. A single request writes
    # several entries inside one transaction and they all receive the same
    # timestamp, so ordering by (occurred_at, id) falls back to a random UUID
    # and renders "custody started" before "vehicle checked in" -- a narrative
    # that reads backwards. An identity column gives the true insertion order,
    # which is the only ordering an append-only log can promise.
    op.add_column(
        "audit_logs",
        sa.Column(
            "seq",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
            comment=(
                "Monotonic insertion order. Timestamps collide within a "
                "transaction; this does not, so a timeline can be rendered in "
                "the order events were actually written."
            ),
        ),
    )
    op.create_index("ix_audit_logs_seq", "audit_logs", ["seq"], unique=True)

    # -- 2. rename history, then narrow the vocabulary --------------------- #
    # The constraint is dropped first: while it is in place it would reject the
    # new names the UPDATE is about to write.
    op.drop_constraint(CONSTRAINT_NAME, "audit_logs", type_="check")

    audit = sa.table("audit_logs", sa.column("action", sa.String))
    for old, new in RENAMED_ACTIONS.items():
        op.execute(sa.update(audit).where(audit.c.action == old).values(action=new))

    op.create_check_constraint(
        CONSTRAINT_NAME, "audit_logs", _action_check(NEW_ACTIONS)
    )

    # The lifecycle events describe one custody, so the timeline query filters
    # on them constantly. A partial index keeps that read cheap without paying
    # for the supporting events nobody pages through.
    op.create_index(
        "ix_audit_logs_lifecycle_occurred",
        "audit_logs",
        ["occurred_at"],
        postgresql_where=sa.text(_action_check(LIFECYCLE_ACTIONS)),
    )

    # -- 3. append-only at the database level ------------------------------ #
    # Two guards, because "append-only" has to mean something precise here.
    #
    # Rows may never be removed, and the *content* of a row may never change.
    # But three columns are legitimately written after insertion, and blocking
    # them would break the design this table depends on:
    #
    #   * ``actor_deleted_at`` is set by the tombstone trigger when an actor's
    #     account is deleted. That is how the trail outlives the account.
    #   * ``actor_id`` and ``custody_session_id`` go to NULL via
    #     ``ON DELETE SET NULL``, which PostgreSQL implements as an UPDATE.
    #
    # So the guard forbids *rewriting history* rather than forbidding the word
    # UPDATE. Reattributing an entry to a different user, changing what it
    # says, or backdating it all raise; de-referencing a deleted row does not.
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION_NAME}() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION
                    'audit_logs is append-only: % is not permitted. '
                    'Write a new row that supersedes the old one instead.',
                    TG_OP;
            END IF;

            IF NOT (
                NEW.action              IS NOT DISTINCT FROM OLD.action
                AND NEW.outcome         IS NOT DISTINCT FROM OLD.outcome
                AND NEW.entity_type     IS NOT DISTINCT FROM OLD.entity_type
                AND NEW.entity_id       IS NOT DISTINCT FROM OLD.entity_id
                AND NEW.description     IS NOT DISTINCT FROM OLD.description
                AND NEW.failure_reason  IS NOT DISTINCT FROM OLD.failure_reason
                AND NEW.old_values      IS NOT DISTINCT FROM OLD.old_values
                AND NEW.new_values      IS NOT DISTINCT FROM OLD.new_values
                AND NEW.metadata        IS NOT DISTINCT FROM OLD.metadata
                AND NEW.occurred_at     IS NOT DISTINCT FROM OLD.occurred_at
                AND NEW.created_at      IS NOT DISTINCT FROM OLD.created_at
                AND NEW.request_id      IS NOT DISTINCT FROM OLD.request_id
                AND NEW.ip_address      IS NOT DISTINCT FROM OLD.ip_address
                AND NEW.user_agent      IS NOT DISTINCT FROM OLD.user_agent
                AND NEW.actor_role      IS NOT DISTINCT FROM OLD.actor_role
                AND NEW.actor_email     IS NOT DISTINCT FROM OLD.actor_email
                -- A foreign key may be released, but an entry may not be
                -- reattributed: pointing it at a different actor is the
                -- single most useful forgery and the one this must stop.
                AND (NEW.actor_id IS NOT DISTINCT FROM OLD.actor_id
                     OR NEW.actor_id IS NULL)
                AND (NEW.custody_session_id IS NOT DISTINCT FROM OLD.custody_session_id
                     OR NEW.custody_session_id IS NULL)
                -- The tombstone may be written once. It may not be cleared or
                -- moved, or the record of when an account was deleted could
                -- be quietly withdrawn.
                AND (NEW.actor_deleted_at IS NOT DISTINCT FROM OLD.actor_deleted_at
                     OR (OLD.actor_deleted_at IS NULL
                         AND NEW.actor_deleted_at IS NOT NULL))
            ) THEN
                RAISE EXCEPTION
                    'audit_logs is append-only: the content of an entry cannot '
                    'be modified. Only de-referencing a deleted actor or '
                    'custody session, and writing the actor tombstone, are '
                    'permitted. Write a new row that supersedes this one.';
            END IF;

            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER_NAME}
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {DELETE_TRIGGER_NAME}
        BEFORE DELETE OR TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION_NAME}();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON audit_logs")
    op.execute(f"DROP TRIGGER IF EXISTS {DELETE_TRIGGER_NAME} ON audit_logs")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_NAME}()")

    op.drop_index("ix_audit_logs_lifecycle_occurred", table_name="audit_logs")

    # Restore the old names, then rebuild the old constraint. Same ordering
    # argument as the upgrade: the constraint must not be in place while rows
    # still carry names it does not allow.
    op.drop_constraint(CONSTRAINT_NAME, "audit_logs", type_="check")

    audit = sa.table("audit_logs", sa.column("action", sa.String))
    # The reversal maps *current* name -> *pre-migration* name. Six lifecycle
    # names had no predecessor at all; folding them onto the closest earlier
    # event keeps the downgrade runnable, and they were not in the log before
    # this migration anyway.
    reversals: dict[str, str] = {new: old for old, new in RENAMED_ACTIONS.items()}
    reversals.update(
        {
            "CUSTODY_STARTED": "CHECK_IN",
            "VEHICLE_CREATED": "CREATE",
            "TICKET_CREATED": "CREATE",
            "PICKUP_IMAGE_CAPTURED": "INSPECTION_CAPTURED",
            "GPS_BREACH": "ALERT_RAISED",
            "CUSTODY_COMPLETED": "CHECK_OUT",
        }
    )
    for current, previous in reversals.items():
        op.execute(
            sa.update(audit).where(audit.c.action == current).values(action=previous)
        )

    op.create_check_constraint(
        CONSTRAINT_NAME, "audit_logs", _action_check(OLD_ACTIONS)
    )

    # Tolerant of a database migrated by an earlier copy of this revision,
    # which had no seq column. A downgrade that hard-fails on an object it is
    # about to remove anyway is a downgrade that cannot be run.
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_seq")
    op.execute("ALTER TABLE audit_logs DROP COLUMN IF EXISTS seq")
    op.drop_column("audit_logs", "metadata")
