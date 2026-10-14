"""Model-level logic: enums, status vocabularies, image references, passwords.

These run without PostgreSQL, so `pytest` still works on a machine with no
database installed. Schema-level behaviour (constraints, cascades, indexes) is
covered in ``test_database_models.py``.
"""

from __future__ import annotations

import pytest

from models import (
    ALL_MODELS,
    MODELS_BY_TABLE,
    AlertSeverity,
    AuditAction,
    CustodyStatus,
    DamageSeverity,
    InspectionStage,
    TelemetrySource,
    UserStatus,
    Vehicle,
    ZoneStatus,
    choices,
    validate_status,
)
from models.audit_log import ENTITY_TYPES, assert_valid_entity_type
from models.damage_detection import DamageType, DetectionMethod, VehicleRegion
from models.enums import check_constraint, status_column
from utils.exceptions import ValidationError
from utils.image_ref import (
    ImageRef,
    assert_not_binary,
    normalise_image_ref,
    validate_image_ref_dict,
    validate_image_ref_list,
)

GOOD_REF = {
    "path": "inspections/2026/09/front-left.jpg",
    "byte_size": 248_371,
    "width": 3024,
    "height": 4032,
    "mime_type": "image/jpeg",
    "sha256": "a" * 64,
}


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------
def test_every_domain_model_is_registered():
    """A model missing from models/__init__ would be dropped by autogenerate."""
    assert len(ALL_MODELS) == 15
    assert set(MODELS_BY_TABLE) == {
        "users",
        "vehicles",
        "parking_zones",
        "valet_tickets",
        "custody_sessions",
        "gps_events",
        "vehicle_inspections",
        "damage_detections",
        "security_alerts",
        "retrieval_predictions",
        # Phase 11: the centralized alert feed, its per-recipient read markers,
        # and the notification delivery ledger.
        "alerts",
        "alert_reads",
        "notifications",
        "audit_logs",
        "revoked_tokens",
    }


def test_every_table_has_timestamps_or_is_deliberately_immutable():
    """Append-only tables carry created_at but deliberately no updated_at."""
    # alert_reads joins the append-only set: a read marker is recorded once and
    # never edited, so it carries created_at and deliberately no updated_at.
    immutable = {"gps_events", "audit_logs", "revoked_tokens", "alert_reads"}
    for model in ALL_MODELS:
        columns = {column.name for column in model.__table__.columns}
        assert "created_at" in columns, f"{model.__tablename__} lacks created_at"
        if model.__tablename__ in immutable:
            assert "updated_at" not in columns, (
                f"{model.__tablename__} is append-only and must not have updated_at"
            )
        else:
            assert "updated_at" in columns, f"{model.__tablename__} lacks updated_at"


def test_every_table_uses_uuid_primary_keys():
    for model in ALL_MODELS:
        primary_key = model.__table__.primary_key
        assert len(primary_key.columns) == 1
        assert primary_key.columns.values()[0].name == "id"


def test_no_table_stores_binary_or_blob_columns():
    """The mandate: image bytes never live in relational tables."""
    forbidden = {"BLOB", "BYTEA", "VARBINARY", "IMAGE"}
    for model in ALL_MODELS:
        for column in model.__table__.columns:
            type_name = str(column.type).upper()
            assert not any(name in type_name for name in forbidden), (
                f"{model.__tablename__}.{column.name} uses {type_name}"
            )


# ---------------------------------------------------------------------------
# Status vocabularies
# ---------------------------------------------------------------------------
def test_choices_returns_plain_strings():
    assert choices(UserStatus) == ("PENDING", "ACTIVE", "SUSPENDED", "DEACTIVATED")
    assert all(isinstance(value, str) for value in choices(CustodyStatus))


def test_validate_status_accepts_members_and_rejects_the_rest():
    assert validate_status("ACTIVE", UserStatus) == "ACTIVE"
    with pytest.raises(ValueError, match="must be one of"):
        validate_status("SUPERUSER", UserStatus)


def test_custody_status_is_open_until_checked_out():
    open_states = [status for status in CustodyStatus if status.is_open]
    assert CustodyStatus.CHECKED_OUT not in open_states
    assert CustodyStatus.PARKED in open_states
    assert CustodyStatus.RETRIEVAL_IN_PROGRESS in open_states


def test_severity_enums_are_ordered():
    assert DamageSeverity.COSMETIC.rank < DamageSeverity.SEVERE.rank
    assert AlertSeverity.LOW.rank < AlertSeverity.CRITICAL.rank


def test_telemetry_source_distinguishes_simulated_from_hardware():
    """The platform must never blur the two."""
    assert TelemetrySource.SIMULATED.value == "SIMULATED"
    assert TelemetrySource.HARDWARE.value == "HARDWARE"
    assert TelemetrySource.SIMULATED is not TelemetrySource.HARDWARE


def test_check_constraint_names_the_permitted_set():
    constraint = check_constraint("status", ZoneStatus)
    assert constraint.name == "ck_status_valid"
    assert "'ACTIVE'" in str(constraint.sqltext)
    assert "'RETIRED'" in str(constraint.sqltext)


def test_status_column_rejects_a_default_outside_the_vocabulary():
    with pytest.raises(ValueError, match="is not a member of"):
        status_column(InspectionStage, UserStatus.ACTIVE)


# ---------------------------------------------------------------------------
# Plate normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("UP 78 AB 1234", "UP78AB1234"),
        ("up78ab1234", "UP78AB1234"),
        ("UP-78-AB-1234", "UP78AB1234"),
        ("  up 78 ab 1234  ", "UP78AB1234"),
    ],
)
def test_plate_normalisation_makes_equivalent_plates_equal(raw, expected):
    assert Vehicle.normalize_plate(raw) == expected


# ---------------------------------------------------------------------------
# Audit log vocabulary
# ---------------------------------------------------------------------------
def test_audit_entity_types_cover_every_domain_table_except_themselves():
    """The log points at other tables; it never points at itself."""
    assert "audit_logs" not in ENTITY_TYPES
    assert set(ENTITY_TYPES) <= set(MODELS_BY_TABLE)


def test_assert_valid_entity_type_rejects_typos():
    assert assert_valid_entity_type("vehicles") == "vehicles"
    with pytest.raises(ValueError, match="entity_type must be one of"):
        assert_valid_entity_type("vehicals")


def test_audit_actions_include_the_custody_workflow():
    required = {
        AuditAction.VEHICLE_CHECKED_IN,
        AuditAction.VEHICLE_PICKED_UP,
        AuditAction.ZONE_ASSIGN,
        AuditAction.SECURITY_ALERT_CREATED,
        AuditAction.DAMAGE_DETECTED,
        AuditAction.PICKUP_REQUESTED,
    }
    assert required <= set(AuditAction)


# ---------------------------------------------------------------------------
# Honest method attribution
# ---------------------------------------------------------------------------
def test_detection_methods_do_not_overstate_a_trained_model():
    assert DetectionMethod.CLASSICAL_CV.value == "CLASSICAL_CV"
    assert DetectionMethod.ML_MODEL.value == "ML_MODEL"
    assert DamageType.SCRATCH in set(DamageType)
    assert VehicleRegion.REAR_BUMPER in set(VehicleRegion)


# ---------------------------------------------------------------------------
# Image references - paths, never bytes
# ---------------------------------------------------------------------------
def test_binary_payloads_are_rejected_outright():
    with pytest.raises(ValidationError, match="not image bytes"):
        assert_not_binary(b"\xff\xd8\xff\xe0jpeg-bytes")


def test_normalise_accepts_a_relative_path_and_backslashes():
    assert normalise_image_ref("inspections/2026/front.jpg") == "inspections/2026/front.jpg"
    assert normalise_image_ref("inspections\\2026\\front.jpg") == "inspections/2026/front.jpg"


def test_normalise_accepts_object_storage_uris():
    assert normalise_image_ref("s3://valet-media/inspections/a.jpg").startswith("s3://")
    assert normalise_image_ref("https://cdn.example.com/inspections/a.jpg").startswith("https://")


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "../escape.jpg",
        "inspections/../../escape.jpg",
        "inspections//double.jpg",
        "",
        "   ",
        "inspections/\x00null.jpg",
    ],
)
def test_normalise_rejects_unsafe_references(bad):
    with pytest.raises(ValidationError):
        normalise_image_ref(bad)


def test_normalise_rejects_binary_payloads():
    with pytest.raises(ValidationError):
        normalise_image_ref(bytearray(b"\x89PNG"))


def test_validate_image_ref_dict_requires_every_metadata_key():
    incomplete = {key: value for key, value in GOOD_REF.items() if key != "sha256"}
    with pytest.raises(ValidationError, match="missing required key"):
        validate_image_ref_dict(incomplete)


def test_validate_image_ref_dict_rejects_a_bad_digest():
    with pytest.raises(ValidationError, match="sha256 must be 64 lowercase"):
        validate_image_ref_dict({**GOOD_REF, "sha256": "not-a-digest"})


def test_validate_image_ref_dict_rejects_non_positive_dimensions():
    with pytest.raises(ValidationError, match="dimensions must be positive"):
        validate_image_ref_dict({**GOOD_REF, "height": 0})


def test_image_ref_round_trips_through_a_dict():
    payload = validate_image_ref_dict(GOOD_REF)
    restored = ImageRef.from_dict(payload)
    assert restored.path == GOOD_REF["path"]
    assert restored.as_dict()["sha256"] == "a" * 64


def test_validate_image_ref_list_accepts_none_as_empty():
    assert validate_image_ref_list(None) == []


def test_validate_image_ref_list_rejects_a_bare_list_of_bytes():
    with pytest.raises(ValidationError):
        validate_image_ref_list([b"\xff\xd8\xff"])
