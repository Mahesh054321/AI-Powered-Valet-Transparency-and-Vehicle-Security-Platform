"""Status vocabularies shared by the models, the migrations and the API.

Every status-like column in the platform is stored as ``VARCHAR`` guarded by a
``CHECK`` constraint rather than a native PostgreSQL ``ENUM`` type. That is a
deliberate trade-off:

* adding a status later is a one-line change here plus a migration that swaps a
  CHECK constraint, instead of ``ALTER TYPE ... ADD VALUE`` -- and unlike a
  native enum, a value can also be *removed*;
* the CHECK constraint names the exact permitted set in the catalog, so a
  reviewer running ``\\d custody_sessions`` sees the vocabulary inline.

The cost is losing enum-level type safety inside PostgreSQL. We recover it in
Python: these ``StrEnum`` classes are the single source of truth, and
``check_constraint()`` builds the SQL from them, so a model and its migration
cannot disagree.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import mapped_column


class UserStatus(StrEnum):
    """Lifecycle of a platform account."""

    PENDING = "PENDING"  # registered, not yet confirmed
    ACTIVE = "ACTIVE"  # may authenticate
    SUSPENDED = "SUSPENDED"  # temporarily blocked by a manager
    DEACTIVATED = "DEACTIVATED"  # soft-deleted; never hard-delete an actor


class VehicleStatus(StrEnum):
    """Lifecycle of a registered vehicle."""

    REGISTERED = "REGISTERED"  # on file, not currently in custody
    IN_CUSTODY = "IN_CUSTODY"  # an open custody session exists
    FLAGGED = "FLAGGED"  # outstanding unacknowledged damage or alert
    ARCHIVED = "ARCHIVED"  # removed from the customer's active fleet


class ZoneStatus(StrEnum):
    """Lifecycle of a parking zone."""

    ACTIVE = "ACTIVE"  # accepting vehicles
    MAINTENANCE = "MAINTENANCE"  # temporarily closed
    FULL = "FULL"  # at capacity
    RETIRED = "RETIRED"  # no longer used; historical rows keep referencing it


class TicketStatus(StrEnum):
    """Lifecycle of the digital valet ticket."""

    ISSUED = "ISSUED"  # generated at registration, before drop-off
    ACTIVE = "ACTIVE"  # vehicle handed over, custody open
    COMPLETED = "COMPLETED"  # vehicle returned to the customer
    CANCELLED = "CANCELLED"  # voided before or during custody
    EXPIRED = "EXPIRED"  # never completed within the retention window


class CustodyStatus(StrEnum):
    """The custody window: drop-off to pickup."""

    CHECKED_IN = "CHECKED_IN"  # dropped off, awaiting zone assignment
    PARKED = "PARKED"  # assigned to a zone, being monitored
    IN_TRANSIT = "IN_TRANSIT"  # a valet is moving the vehicle
    RETRIEVAL_REQUESTED = "RETRIEVAL_REQUESTED"  # customer asked for the car
    RETRIEVAL_IN_PROGRESS = "RETRIEVAL_IN_PROGRESS"  # valet is fetching it
    READY_FOR_PICKUP = "READY_FOR_PICKUP"  # staged at the handover point
    CHECKED_OUT = "CHECKED_OUT"  # returned; custody closed
    DISPUTED = "DISPUTED"  # condition disputed by the customer

    @property
    def is_open(self) -> bool:
        """True while the platform is responsible for the vehicle."""
        return self is not CustodyStatus.CHECKED_OUT


class InspectionStage(StrEnum):
    """When a photo set was captured. Exactly two per custody session."""

    DROP_OFF = "DROP_OFF"
    PICKUP = "PICKUP"


class InspectionOutcome(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"  # captured, not yet assessed
    CLEAN = "CLEAN"  # no damage found
    DAMAGE_FOUND = "DAMAGE_FOUND"  # at least one detection recorded
    REVIEWED = "REVIEWED"  # a manager has signed off


class DamageSeverity(StrEnum):
    """Ordered severity so findings can be sorted and filtered."""

    COSMETIC = "COSMETIC"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"

    @property
    def rank(self) -> int:
        return list(DamageSeverity).index(self)


class VerificationStatus(StrEnum):
    """Where a machine-produced finding stands in human review.

    ``UNVERIFIED`` is the default and the important one: it means nobody has
    looked at the algorithm's guess yet. The UI must render those as *potential*
    damage. Only ``CONFIRMED`` may be presented as a settled fact, and only
    ``CONFIRMED`` findings count towards a customer-facing damage charge.
    """

    UNVERIFIED = "UNVERIFIED"  # algorithm output, no human has reviewed it
    CONFIRMED = "CONFIRMED"  # a manager/admin stood behind the finding
    REJECTED = "REJECTED"  # a manager/admin dismissed it as a false positive


class AlertType(StrEnum):
    """Kinds of anomaly the geofence module can raise."""

    GEOFENCE_BREACH = "GEOFENCE_BREACH"  # left the assigned zone
    ROUTE_DEVIATION = "ROUTE_DEVIATION"  # took an unexpected path
    UNUSUAL_SPEED = "UNUSUAL_SPEED"  # moving faster than the zone allows
    ABNORMAL_MOVEMENT = "ABNORMAL_MOVEMENT"  # movement with no active task
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"  # touched by an unassigned operator
    TAMPER_DETECTED = "TAMPER_DETECTED"  # vision pipeline reports interference
    TELEMETRY_LOSS = "TELEMETRY_LOSS"  # no fix for longer than the threshold


class AlertSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return list(AlertSeverity).index(self)


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"  # a manager has seen it
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"  # assessed as a false positive


class MovementStatus(StrEnum):
    """Geofence verdict for a single GPS fix.

    Ordered from benign to severe. The classification is deliberately
    *conservative and explainable*: every status is reached by a rule whose
    inputs are stored on the row (distance from the zone boundary, speed, the
    recent movement pattern), so any verdict can be re-derived by hand.

    This is a rule engine, not a trained model. Nothing here has learned
    anything about a specific yard, and nothing here claims to.
    """

    #: Inside the geofence, stationary or crawling. Nothing to report.
    NORMAL = "NORMAL"
    #: Approaching the boundary, or faster than the zone allows, but still
    #: inside. Deliberately a soft state so operators are not paged for GPS
    #: jitter: the boundary band is ``buffer_metres`` wide.
    WARNING = "WARNING"
    #: Outside the boundary, or an implausible movement pattern (e.g. moving at
    #: road speed with no active retrieval task). Needs a human to look.
    SUSPICIOUS = "SUSPICIOUS"
    #: Outside the boundary and beyond the tolerance band, or clearly beyond
    #: any speed the zone permits. Escalates immediately.
    BREACH = "BREACH"

    @property
    def rank(self) -> int:
        return list(MovementStatus).index(self)


class MlClassification(StrEnum):
    """The prototype security classifier's output.

    Deliberately binary and deliberately *not* the same vocabulary as
    :class:`MovementStatus`. The rule engine measures and grades a fix against a
    known boundary; this classifier estimates how much a fix resembles the
    hand-written "suspicious" examples it was trained on. Conflating the two
    would let a prototype's guess overwrite a measurement.

    See ``docs/AI-INTEGRITY.md``: this is a prototype trained on synthetic data.
    """

    NORMAL = "NORMAL"
    SUSPICIOUS = "SUSPICIOUS"


class TelemetrySource(StrEnum):
    """Provenance of a GPS fix. Surfaced in the UI on every reading.

    The project mandate is that simulated data must never be mistaken for
    hardware data, so provenance is stored per row rather than inferred from a
    global setting.
    """

    SIMULATED = "SIMULATED"  # generated by the demo telemetry simulator
    HARDWARE = "HARDWARE"  # a real GPS/GNSS receiver
    MANUAL = "MANUAL"  # keyed in by a valet operator


class PredictionStatus(StrEnum):
    """Lifecycle of a retrieval prediction."""

    PENDING = "PENDING"  # computed, dispatch not yet started
    DISPATCHED = "DISPATCHED"  # a valet was sent at the recommended time
    FULFILLED = "FULFILLED"  # vehicle reached READY_FOR_PICKUP
    SUPERSEDED = "SUPERSEDED"  # a later prediction replaced this one
    EXPIRED = "EXPIRED"  # never acted on


#: Zone states in which a vehicle may actually be parked.
PARKABLE_ZONE_STATUSES: tuple[ZoneStatus, ...] = (ZoneStatus.ACTIVE, ZoneStatus.FULL)


def choices(enum: type[StrEnum]) -> tuple[str, ...]:
    """The permitted values of ``enum`` as plain strings."""
    return tuple(member.value for member in enum)


def check_constraint(
    column: str,
    enum: type[StrEnum],
    *,
    name: str | None = None,
) -> CheckConstraint:
    """Build ``CHECK (column IN (...))`` from an enum.

    Used by both the models and the migration, so the permitted set is written
    in exactly one place.
    """
    allowed = ", ".join(f"'{value}'" for value in choices(enum))
    return CheckConstraint(f"{column} IN ({allowed})", name=name or f"ck_{column}_valid")


def status_column(
    enum: type[StrEnum],
    default: StrEnum,
    *,
    length: int = 32,
    nullable: bool = False,
):
    """A VARCHAR status column defaulting to ``default``.

    The CHECK constraint is declared separately on the table, which keeps this
    helper free of dialect-specific DDL. ``enum`` is accepted so callers name
    the vocabulary they mean even though the type itself is ``String``.
    """
    if default not in choices(enum):
        raise ValueError(f"{default!r} is not a member of {enum.__name__}")
    return mapped_column(
        String(length),
        nullable=nullable,
        default=str(default),
        server_default=str(default),
    )


def validate_status(value: str, enum: type[StrEnum], *, field: str = "status") -> str:
    """Raise ``ValueError`` unless ``value`` belongs to ``enum``.

    Lets service code fail fast with a clear message instead of surfacing an
    opaque CHECK violation from PostgreSQL.
    """
    if value not in choices(enum):
        raise ValueError(f"{field} must be one of {', '.join(choices(enum))}; got {value!r}")
    return value


__all__ = [
    "PARKABLE_ZONE_STATUSES",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "CustodyStatus",
    "DamageSeverity",
    "InspectionOutcome",
    "InspectionStage",
    "MovementStatus",
    "MlClassification",
    "PredictionStatus",
    "TelemetrySource",
    "TicketStatus",
    "UserStatus",
    "VerificationStatus",
    "VehicleStatus",
    "ZoneStatus",
    "check_constraint",
    "choices",
    "status_column",
    "validate_status",
]
