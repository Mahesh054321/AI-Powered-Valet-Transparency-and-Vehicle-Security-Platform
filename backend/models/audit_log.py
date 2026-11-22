"""Append-only audit trail.

Every significant state change in the platform is recorded here: who did what,
to which entity, when, from where, and what the row looked like before and
after. This is the table an evaluator opens when they ask "can you prove that
happened?".

Two properties are enforced structurally rather than by convention:

* **Append-only.** There is no ``updated_at``, and no foreign key uses
  ``ON DELETE CASCADE`` from this table. Actors are referenced by a nullable
  ``actor_id`` *plus* a denormalised ``actor_role``/``actor_email`` snapshot, so
  deleting or deactivating a user cannot erase their trail.
* **Denormalised entity reference.** ``entity_type`` and ``entity_id`` are
  plain columns rather than foreign keys to eleven different tables, because a
  single polymorphic log must be able to point at any of them -- and because
  the log has to survive the deletion of the row it describes.

``old_values`` / ``new_values`` are JSONB *diffs*, not whole rows, so a log
entry stays small and never leaks columns the caller did not change (notably
password hashes).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, UUIDPrimaryKeyMixin
from models.enums import check_constraint
from utils.security import Role

ENTITY_TYPE_MAX_LENGTH = 48
ACTION_MAX_LENGTH = 48
IP_MAX_LENGTH = 45  # enough for an IPv6 address
USER_AGENT_MAX_LENGTH = 255


class AuditAction(StrEnum):
    """The vocabulary of auditable operations.

    The first block is the **custody lifecycle**: the fourteen events that
    together tell the story of one vehicle's time in the yard, in the order a
    reviewer would read them. They are named for what happened to the vehicle
    rather than for the code path that happened to run, because that is what an
    auditor asks for -- "when was it checked in?", not "which service method
    fired?".

    The second block is supporting events: generic CRUD, authentication
    failures, and the human decisions (verification, triage, export) that sit
    alongside the lifecycle rather than inside it.

    Each lifecycle event fires **exactly once** per occurrence. Where an
    earlier, more generic name covered the same moment -- ``CHECK_IN`` for
    ``VEHICLE_CHECKED_IN``, ``PHOTO_UPLOADED`` for ``IMAGE_UPLOADED`` -- the
    generic name was retired by migration 0008 rather than kept as a synonym,
    so the log cannot record the same act twice under two names.
    """

    # -- custody lifecycle --------------------------------------------------
    #: A user authenticated successfully.
    USER_LOGIN = "USER_LOGIN"
    #: A vehicle record was created.
    VEHICLE_CREATED = "VEHICLE_CREATED"
    #: A valet ticket was issued for a vehicle.
    TICKET_CREATED = "TICKET_CREATED"
    #: The vehicle was handed over at the gate.
    VEHICLE_CHECKED_IN = "VEHICLE_CHECKED_IN"
    #: Photographs were stored against an inspection.
    IMAGE_UPLOADED = "IMAGE_UPLOADED"
    #: A custody session opened; the platform is now responsible for the car.
    CUSTODY_STARTED = "CUSTODY_STARTED"
    #: A GPS fix landed outside the geofence, or the detector classified the
    #: movement as a breach. Audited separately from the alert it produces
    #: because the breach is the *observation* and the alert is the *response*.
    GPS_BREACH = "GPS_BREACH"
    #: A security alert row was created.
    SECURITY_ALERT_CREATED = "SECURITY_ALERT_CREATED"
    #: The customer asked for the vehicle back.
    PICKUP_REQUESTED = "PICKUP_REQUESTED"
    #: The retrieval model produced a wait estimate.
    RETRIEVAL_PREDICTED = "RETRIEVAL_PREDICTED"
    #: The pickup photograph set was captured and compared with drop-off.
    PICKUP_IMAGE_CAPTURED = "PICKUP_IMAGE_CAPTURED"
    #: The condition pipeline reported a candidate finding.
    DAMAGE_DETECTED = "DAMAGE_DETECTED"
    #: The vehicle was handed back to its owner.
    VEHICLE_PICKED_UP = "VEHICLE_PICKED_UP"
    #: The custody session closed; responsibility ended.
    CUSTODY_COMPLETED = "CUSTODY_COMPLETED"

    # -- supporting events --------------------------------------------------
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    STATUS_CHANGE = "STATUS_CHANGE"
    ZONE_ASSIGN = "ZONE_ASSIGN"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
    ALERT_RESOLVED = "ALERT_RESOLVED"
    #: A drop-off photograph set was stored. The pickup equivalent has its own
    #: lifecycle name because it triggers the comparison.
    INSPECTION_CAPTURED = "INSPECTION_CAPTURED"
    #: A human confirmed or rejected a machine-produced finding. The single most
    #: important audit event in the condition module: it is the record of who
    #: stood behind an algorithm's guess.
    DAMAGE_VERIFIED = "DAMAGE_VERIFIED"
    #: The condition pipeline was run over synthetic demo photographs. Audited so
    #: a demo run is never mistaken for a real inspection later.
    INSPECTION_DEMO_RUN = "INSPECTION_DEMO_RUN"
    #: A geofence was created or re-configured. Audited separately from a plain
    #: ``UPDATE`` because moving a boundary changes what every future GPS fix
    #: means -- a boundary edit is a security-relevant act, not housekeeping.
    GEOFENCE_CONFIGURED = "GEOFENCE_CONFIGURED"
    #: A batch of GPS fixes was produced by the demo simulator rather than by a
    #: receiver. Audited so a synthetic track can never be mistaken later for
    #: a real journey.
    GPS_TRACK_SIMULATED = "GPS_TRACK_SIMULATED"
    EXPORT = "EXPORT"
    SECURITY_ALERT_INVESTIGATING = "SECURITY_ALERT_INVESTIGATING"
    SECURITY_ALERT_REOPENED = "SECURITY_ALERT_REOPENED"
    SAFE_ZONE_CREATED = "SAFE_ZONE_CREATED"
    SAFE_ZONE_UPDATED = "SAFE_ZONE_UPDATED"
    SAFE_ZONE_DELETED = "SAFE_ZONE_DELETED"
    TAMPER_EVENT_RECORDED = "TAMPER_EVENT_RECORDED"
    EMAIL_DISPATCHED = "EMAIL_DISPATCHED"
    EMAIL_DISPATCH_FAILED = "EMAIL_DISPATCH_FAILED"


#: Names retired by migration 0008 in favour of a lifecycle name that says the
#: same thing more plainly. Kept here so the migration can rename history
#: instead of orphaning it, and so a stray old constant fails loudly at import
#: time rather than writing a row the CHECK constraint then rejects.
RETIRED_ACTIONS: dict[str, str] = {
    "LOGIN": "USER_LOGIN",
    "CHECK_IN": "VEHICLE_CHECKED_IN",
    "CHECK_OUT": "VEHICLE_PICKED_UP",
    "PHOTO_UPLOADED": "IMAGE_UPLOADED",
    "ALERT_RAISED": "SECURITY_ALERT_CREATED",
    "DAMAGE_FLAGGED": "DAMAGE_DETECTED",
    "RETRIEVAL_REQUESTED": "PICKUP_REQUESTED",
    "PREDICTION_GENERATED": "RETRIEVAL_PREDICTED",
}


#: The fourteen lifecycle events, in the order they occur during one custody.
#: Used by the timeline view to order and label events, and by the tests to
#: assert that a complete walk produced every one of them.
LIFECYCLE_ACTIONS: tuple[str, ...] = (
    AuditAction.USER_LOGIN.value,
    AuditAction.VEHICLE_CREATED.value,
    AuditAction.TICKET_CREATED.value,
    AuditAction.VEHICLE_CHECKED_IN.value,
    AuditAction.IMAGE_UPLOADED.value,
    AuditAction.CUSTODY_STARTED.value,
    AuditAction.GPS_BREACH.value,
    AuditAction.SECURITY_ALERT_CREATED.value,
    AuditAction.PICKUP_REQUESTED.value,
    AuditAction.RETRIEVAL_PREDICTED.value,
    AuditAction.PICKUP_IMAGE_CAPTURED.value,
    AuditAction.DAMAGE_DETECTED.value,
    AuditAction.VEHICLE_PICKED_UP.value,
    AuditAction.CUSTODY_COMPLETED.value,
)


class AuditOutcome(StrEnum):
    """Whether the audited operation succeeded.

    Failures are logged too: a rejected login or a refused action is often the
    more interesting event.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DENIED = "DENIED"


#: Table names that may legitimately appear in ``entity_type``. Enforced by the
#: CHECK constraint built from this tuple in the migration and by
#: ``assert_valid_entity_type`` here, so a typo cannot silently fragment the log.
ENTITY_TYPES: tuple[str, ...] = (
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
    "alerts",
    "notifications",
)


def assert_valid_entity_type(entity_type: str) -> str:
    """Guard against logging an entity type the schema does not know about."""
    if entity_type not in ENTITY_TYPES:
        raise ValueError(
            f"entity_type must be one of {', '.join(ENTITY_TYPES)}; got {entity_type!r}"
        )
    return entity_type


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    #: Row-insertion time. Deliberately *not* TimestampMixin: this table is
    #: append-only, so there is no ``updated_at``. ``occurred_at`` below is the
    #: time of the audited action, which may differ from insertion.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: Monotonic insertion order, assigned by the database.
    #:
    #: Timestamps alone cannot order this table. One request routinely writes
    #: several entries inside a single transaction -- a check-in writes both
    #: ``VEHICLE_CHECKED_IN`` and ``CUSTODY_STARTED`` -- and every one of them
    #: receives the same ``occurred_at``. Ordering by ``(occurred_at, id)``
    #: then falls back to a random UUID, which renders a timeline in an
    #: arbitrary order and can put "custody started" *before* "vehicle checked
    #: in". An identity column is the only ordering an append-only log can
    #: actually promise.
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(), nullable=False, unique=True, index=True
    )

    __table_args__ = (
        check_constraint("action", AuditAction),
        check_constraint("outcome", AuditOutcome),
        check_constraint("actor_role", Role),
        CheckConstraint(
            "entity_type IN ({})".format(", ".join(f"'{name}'" for name in ENTITY_TYPES)),
            name="ck_audit_logs_entity_type_known",
        ),
        # An actor reference and its snapshot must agree: either both or neither.
        # A snapshot must be either attributed to a live account or explicitly
        # marked as retained after that account was deleted. A CHECK alone
        # cannot tell those apart - both look like `actor_id IS NULL` with the
        # snapshot populated - so `set_actor_tombstone_on_user_delete()` writes
        # the tombstone and this constraint then has something real to test.
        CheckConstraint(
            "actor_role IS NULL OR actor_id IS NOT NULL OR actor_deleted_at IS NOT NULL",
            name="ck_audit_logs_snapshot_attributed_or_tombstoned",
        ),
        # A failure still needs a reason.
        CheckConstraint(
            "outcome = 'SUCCESS' OR (failure_reason IS NOT NULL AND length(failure_reason) > 0)",
            name="ck_audit_logs_failure_requires_reason",
        ),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_occurred_at", "occurred_at"),
        Index("ix_audit_logs_actor", "actor_id"),
        Index("ix_audit_logs_request_id", "request_id"),
        # "Everything that happened during this custody", newest first.
        Index(
            "ix_audit_logs_custody_occurred",
            text("custody_session_id, occurred_at DESC"),
        ),
        {
            "comment": (
                "Append-only audit trail. No updated_at and no cascading "
                "deletes: actor details are snapshotted so the trail survives "
                "the deletion of the rows it describes."
            )
        },
    )

    # -- who ----------------------------------------------------------------
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_logs_actor_id_users"),
        index=True,
        comment="NULL for anonymous events such as a failed login.",
    )
    actor_role: Mapped[str | None] = mapped_column(
        String(32), comment="Snapshot of the actor's role at the time of the action."
    )
    actor_email: Mapped[str | None] = mapped_column(
        String(254), comment="Snapshot of the actor's email at the time of the action."
    )
    actor_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment=(
            "Set by an AFTER DELETE trigger on users when the actor's row was "
            "removed. Marks the snapshot as deliberately retained rather than "
            "never attributed, which is what lets the audit trail outlive the "
            "account it describes."
        ),
    )

    # -- what ---------------------------------------------------------------
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=str(AuditOutcome.SUCCESS),
        server_default=str(AuditOutcome.SUCCESS),
    )
    entity_type: Mapped[str] = mapped_column(String(ENTITY_TYPE_MAX_LENGTH), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        comment="Identifier of the affected row. Not a foreign key, so the log "
        "survives the row's deletion.",
    )

    #: JSONB diffs of only the columns that changed. Never whole rows.
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)

    #: Free-form context for the event: the numbers behind a prediction, the
    #: scenario a simulated track used, the plate a vehicle was created with.
    #: Distinct from ``new_values`` on purpose -- that column answers "what
    #: changed on the row?", this one answers "what else should a reviewer
    #: know about the moment?". Mapped as ``metadata_`` because ``metadata``
    #: is taken by SQLAlchemy's declarative base.
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    description: Mapped[str | None] = mapped_column(String(500))
    failure_reason: Mapped[str | None] = mapped_column(
        String(500), comment="Required when outcome is not SUCCESS."
    )

    # -- context ------------------------------------------------------------
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        comment="Correlates the entry with the X-Request-Id in the API logs.",
    )
    ip_address: Mapped[str | None] = mapped_column(String(IP_MAX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH))

    # -- relationships ------------------------------------------------------
    # Note: no relationship to custody_sessions with cascade delete. A custody
    # session's history must outlive the session row itself.
    custody_session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "custody_sessions.id",
            ondelete="SET NULL",
            name="fk_audit_logs_custody_session_id_custody_sessions",
        ),
        index=True,
    )
    actor: Mapped[User | None] = relationship("User")  # noqa: F821

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "seq": self.seq,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_role": self.actor_role,
            "actor_email": self.actor_email,
            "actor_deleted_at": (
                self.actor_deleted_at.isoformat() if self.actor_deleted_at else None
            ),
            "action": self.action,
            "outcome": self.outcome,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "metadata": self.metadata_,
            "description": self.description,
            "failure_reason": self.failure_reason,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "request_id": self.request_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "custody_session_id": (
                str(self.custody_session_id) if self.custody_session_id else None
            ),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog {self.action} {self.entity_type} outcome={self.outcome}>"


__all__ = [
    "ENTITY_TYPES",
    "LIFECYCLE_ACTIONS",
    "RETIRED_ACTIONS",
    "AuditAction",
    "AuditLog",
    "AuditOutcome",
    "assert_valid_entity_type",
]
