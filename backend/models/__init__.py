"""SQLAlchemy models for the valet transparency platform.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic autogenerate diffs against -- so each new model module must be
imported here, or migrations will try to drop the table.

Entity relationships at a glance::

    users 1──N vehicles 1──N custody_sessions 1──N gps_events
      │              │              │
      │              │              ├──N vehicle_inspections 1──N damage_detections
      │              │              ├──N security_alerts
      │              │              ├──N retrieval_predictions
      │              │              └──N audit_logs (SET NULL, never CASCADE)
      │              │
      └──1──N valet_tickets 1──1 custody_sessions
                               N──1 parking_zones

Conventions: UUID surrogate primary keys (``gen_random_uuid()`` from pgcrypto),
``created_at``/``updated_at`` with server defaults, ``VARCHAR`` status columns
guarded by CHECK constraints whose permitted values come from
``models/enums.py``, an index on every foreign key, and partial unique indexes
for the business rules that are easy to get wrong in application code.

No table stores image bytes. Photographs, QR codes and annotated evidence are
written to ``storage/media/`` (or object storage) and referenced by validated
path plus metadata -- see ``utils/image_ref.py``.
"""

from __future__ import annotations

from app.extensions import db
from models.alert import (
    CATEGORY_BY_SOURCE_TYPE,
    Alert,
    AlertAudience,
    AlertCategory,
    AlertRead,
    category_for,
)
from models.alert import AlertStatus as UnifiedAlertStatus
from models.audit_log import ENTITY_TYPES, AuditAction, AuditLog, AuditOutcome
from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.custody_session import CustodySession
from models.damage_detection import DamageDetection, DamageType, DetectionMethod, VehicleRegion
from models.enums import (
    PARKABLE_ZONE_STATUSES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    BoundaryState,
    CustodyStatus,
    DamageSeverity,
    InspectionOutcome,
    InspectionStage,
    PredictionStatus,
    TelemetryFreshness,
    TelemetrySource,
    TicketStatus,
    UserStatus,
    VehicleStatus,
    ZoneStatus,
    ZoneType,
    check_constraint,
    choices,
    status_column,
    validate_status,
)
from models.gps_device import GpsDevice
from models.gps_event import GpsEvent
from models.notification import Notification, NotificationChannel, NotificationStatus
from models.parking_zone import ParkingZone
from models.retrieval_prediction import RetrievalPrediction
from models.revoked_token import RevocationReason, RevokedToken
from models.security_alert import SecurityAlert
from models.user import User
from models.valet_ticket import ValetTicket
from models.vehicle import Vehicle
from models.vehicle_inspection import VehicleInspection

#: Every domain model, in dependency order. Used by the seeder and by tests
#: that need to assert the full schema is registered.
ALL_MODELS: tuple[type[Base], ...] = (
    User,
    Vehicle,
    GpsDevice,
    ParkingZone,
    ValetTicket,
    CustodySession,
    GpsEvent,
    VehicleInspection,
    DamageDetection,
    SecurityAlert,
    RetrievalPrediction,
    Alert,
    AlertRead,
    Notification,
    AuditLog,
    RevokedToken,
)

#: Table name -> model class, for schema assertions.
MODELS_BY_TABLE: dict[str, type[Base]] = {
    model.__tablename__: model for model in ALL_MODELS  # type: ignore[attr-defined]
}

__all__ = [
    "ALL_MODELS",
    "CATEGORY_BY_SOURCE_TYPE",
    "Alert",
    "AlertAudience",
    "AlertCategory",
    "AlertRead",
    "UnifiedAlertStatus",
    "category_for",
    "Notification",
    "NotificationChannel",
    "NotificationStatus",
    "ENTITY_TYPES",
    "MODELS_BY_TABLE",
    "PARKABLE_ZONE_STATUSES",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "AuditAction",
    "AuditLog",
    "AuditOutcome",
    "Base",
    "BoundaryState",
    "CustodySession",
    "CustodyStatus",
    "DamageDetection",
    "DamageSeverity",
    "DamageType",
    "DetectionMethod",
    "GpsDevice",
    "GpsEvent",
    "InspectionOutcome",
    "InspectionStage",
    "ParkingZone",
    "PredictionStatus",
    "RetrievalPrediction",
    "RevocationReason",
    "RevokedToken",
    "SecurityAlert",
    "TelemetryFreshness",
    "TelemetrySource",
    "TicketStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserStatus",
    "ValetTicket",
    "Vehicle",
    "VehicleInspection",
    "VehicleRegion",
    "VehicleStatus",
    "ZoneStatus",
    "ZoneType",
    "check_constraint",
    "choices",
    "db",
    "status_column",
    "validate_status",
]
