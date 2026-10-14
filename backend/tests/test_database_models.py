"""Schema verification against a real PostgreSQL database.

These tests assert the *live* schema, not the models' opinion of it: that every
table, foreign key, index and CHECK constraint the migration promised actually
exists in the catalog. They are skipped automatically when PostgreSQL is not
reachable, so the rest of the suite still runs without a database.

The CHECK-constraint test matters most: it is what stops ``models/enums.py``
and the migration from drifting apart, since the migration deliberately writes
its permitted values as literals rather than reading the enum.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import inspect

pytestmark = pytest.mark.postgres

EXPECTED_TABLES = {
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
    "audit_logs",
}


@pytest.fixture()
def inspector(pg_app):
    from app.extensions import db

    return inspect(db.engine)


@pytest.fixture()
def check_constraints(pg_app):
    """{constraint_name: sql_definition} for every CHECK in the public schema."""
    from sqlalchemy import text

    from app.extensions import db

    rows = db.session.execute(
        text(
            "SELECT con.conname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
            "WHERE nsp.nspname = 'public' AND con.contype = 'c'"
        )
    ).fetchall()
    return dict(rows)


def test_every_domain_table_exists(inspector):
    present = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - present
    assert not missing, f"missing tables: {', '.join(sorted(missing))}"


def test_every_table_has_created_at(inspector):
    for table in sorted(EXPECTED_TABLES):
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "created_at" in columns, f"{table} lacks created_at"


def test_mutable_tables_have_updated_at(inspector):
    """gps_events and audit_logs are append-only and deliberately omit it."""
    for table in sorted(EXPECTED_TABLES - {"gps_events", "audit_logs"}):
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "updated_at" in columns, f"{table} lacks updated_at"


def test_append_only_tables_have_no_updated_at(inspector):
    for table in ("gps_events", "audit_logs"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "updated_at" not in columns, f"{table} must stay append-only"


def test_every_primary_key_is_a_single_uuid_column(inspector):
    for table in sorted(EXPECTED_TABLES):
        primary_key = inspector.get_pk_constraint(table)
        assert primary_key["constrained_columns"] == ["id"], (
            f"{table} primary key is {primary_key['constrained_columns']}"
        )
        id_column = next(
            column for column in inspector.get_columns(table) if column["name"] == "id"
        )
        assert "UUID" in str(id_column["type"]).upper(), f"{table}.id is not a UUID"


def test_no_table_has_a_binary_column(inspector):
    """The mandate: image bytes never live in relational tables."""
    for table in sorted(EXPECTED_TABLES):
        for column in inspector.get_columns(table):
            type_name = str(column["type"]).upper()
            assert "BYTEA" not in type_name, f"{table}.{column['name']} is BYTEA"


def test_expected_foreign_keys_exist(inspector):
    expected = {
        "vehicles": {("owner_id", "users")},
        "valet_tickets": {("customer_id", "users"), ("vehicle_id", "vehicles")},
        "custody_sessions": {
            ("ticket_id", "valet_tickets"),
            ("vehicle_id", "vehicles"),
            ("zone_id", "parking_zones"),
            ("assigned_valet_id", "users"),
        },
        "gps_events": {("custody_session_id", "custody_sessions")},
        "vehicle_inspections": {("custody_session_id", "custody_sessions")},
        "damage_detections": {("inspection_id", "vehicle_inspections")},
        "security_alerts": {("custody_session_id", "custody_sessions")},
        "retrieval_predictions": {("custody_session_id", "custody_sessions")},
        "audit_logs": {("actor_id", "users")},
    }

    for table, wanted in expected.items():
        actual = {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
        }
        missing = wanted - actual
        assert not missing, f"{table} is missing foreign keys: {missing}"


def test_deleting_a_user_cascades_to_their_vehicles(inspector):
    fks = {
        fk["constrained_columns"][0]: fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("vehicles")
    }
    assert fks["owner_id"] == "CASCADE"


def test_deleting_a_custody_session_cascades_to_its_children(inspector):
    for table in ("gps_events", "vehicle_inspections", "security_alerts",
                  "retrieval_predictions"):
        fks = {
            fk["constrained_columns"][0]: fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys(table)
        }
        assert fks["custody_session_id"] == "CASCADE", f"{table} should cascade"


def test_audit_logs_never_cascade_from_a_custody_session(inspector):
    """The audit trail must outlive the rows it describes."""
    fks = {
        fk["constrained_columns"][0]: fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("audit_logs")
    }
    assert fks["custody_session_id"] == "SET NULL"
    assert fks["actor_id"] == "SET NULL"


def test_audit_logs_reference_entities_by_value_not_foreign_key(inspector):
    """A polymorphic log must be able to outlive the row it points at."""
    columns = {column["name"] for column in inspector.get_columns("audit_logs")}
    assert {"entity_type", "entity_id"} <= columns
    referred = {fk["referred_table"] for fk in inspector.get_foreign_keys("audit_logs")}
    assert "vehicles" not in referred


def test_the_documented_indexes_exist(inspector):
    expected = {
        "users": {"ix_users_email", "ix_users_role_status"},
        "vehicles": {"ix_vehicles_owner_id", "uq_vehicles_normalized_plate_active"},
        "custody_sessions": {"uq_custody_sessions_vehicle_open", "uq_custody_sessions_ticket"},
        "vehicle_inspections": {"uq_vehicle_inspections_session_stage"},
        "gps_events": {"ix_gps_events_session_recorded_desc"},
        "security_alerts": {"ix_security_alerts_open_queue"},
        "audit_logs": {"ix_audit_logs_entity", "ix_audit_logs_custody_occurred"},
    }

    for table, wanted in expected.items():
        actual = {index["name"] for index in inspector.get_indexes(table)}
        missing = wanted - actual
        assert not missing, f"{table} is missing indexes: {missing}"


def test_the_open_custody_unique_index_is_partial(inspector):
    """Only *open* sessions are constrained, so history is unaffected."""
    index = next(
        item
        for item in inspect_indexes_with_definitions(inspector, "custody_sessions")
        if item["name"] == "uq_custody_sessions_vehicle_open"
    )
    assert index["unique"] is True
    assert "CHECKED_OUT" in (index.get("dialect_options", {}).get("postgresql_where") or "")


def inspect_indexes_with_definitions(inspector, table):
    """Indexes plus their WHERE clause, read from the catalog."""
    from sqlalchemy import text

    from app.extensions import db

    rows = db.session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = :t"
        ),
        {"t": table},
    ).fetchall()
    return [
        {
            "name": name,
            "unique": "UNIQUE" in definition.upper(),
            "dialect_options": {
                "postgresql_where": definition.split(" WHERE ", 1)[1]
                if " WHERE " in definition
                else ""
            },
        }
        for name, definition in rows
    ]


# ---------------------------------------------------------------------------
# CHECK constraints - the guard against enum/migration drift
# ---------------------------------------------------------------------------
def test_status_check_constraints_match_the_python_enums(check_constraints):
    """The live CHECK must permit exactly what models/enums.py declares."""
    from models import (
        AlertSeverity,
        AlertStatus,
        AlertType,
        AuditAction,
        AuditOutcome,
        CustodyStatus,
        DamageSeverity,
        InspectionOutcome,
        InspectionStage,
        PredictionStatus,
        TelemetrySource,
        TicketStatus,
        UserStatus,
        VehicleStatus,
        ZoneStatus,
        choices,
    )
    from models.damage_detection import DamageType, DetectionMethod, VehicleRegion
    from utils.security import Role

    cases = [
        ("ck_users_role_valid", Role),
        ("ck_users_status_valid", UserStatus),
        ("ck_vehicles_status_valid", VehicleStatus),
        ("ck_parking_zones_status_valid", ZoneStatus),
        ("ck_valet_tickets_status_valid", TicketStatus),
        ("ck_custody_sessions_status_valid", CustodyStatus),
        ("ck_vehicle_inspections_stage_valid", InspectionStage),
        ("ck_vehicle_inspections_outcome_valid", InspectionOutcome),
        ("ck_damage_detections_severity_valid", DamageSeverity),
        ("ck_damage_detections_damage_type_valid", DamageType),
        ("ck_damage_detections_region_valid", VehicleRegion),
        ("ck_damage_detections_method_valid", DetectionMethod),
        ("ck_security_alerts_alert_type_valid", AlertType),
        ("ck_security_alerts_severity_valid", AlertSeverity),
        ("ck_security_alerts_status_valid", AlertStatus),
        ("ck_gps_events_source_valid", TelemetrySource),
        ("ck_retrieval_predictions_status_valid", PredictionStatus),
        ("ck_audit_logs_action_valid", AuditAction),
        ("ck_audit_logs_outcome_valid", AuditOutcome),
    ]

    for name, enum in cases:
        assert name in check_constraints, f"CHECK {name} is missing from the database"
        definition = check_constraints[name]
        # PostgreSQL rewrites `col IN ('A','B')` as
        # `((col)::text = ANY (ARRAY['A'::character varying, ...])::text[])`,
        # so the values have to be pulled out of the quoted literals rather
        # than by splitting on commas -- the casts contain commas themselves.
        permitted = set(re.findall(r"'([^']*)'::character varying", definition))
        assert permitted, f"could not read any values out of {name}: {definition}"
        assert permitted == set(choices(enum)), (
            f"{name} drifted from {enum.__name__}: "
            f"db={sorted(permitted)} model={sorted(choices(enum))}"
        )


def test_confidence_columns_are_constrained_to_a_probability(check_constraints):
    for name in (
        "ck_damage_detections_confidence_is_probability",
        "ck_security_alerts_confidence_is_probability",
        "ck_retrieval_predictions_confidence_is_probability",
    ):
        assert name in check_constraints, f"{name} is missing"
        assert "0" in check_constraints[name] and "1" in check_constraints[name]


def test_explainability_is_enforced_by_the_database(check_constraints):
    """A finding or alert without a reason cannot be written."""
    assert "ck_damage_detections_reasoning_required" in check_constraints
    assert "ck_security_alerts_reason_required" in check_constraints


def test_a_ml_claim_requires_a_model_version(check_constraints):
    assert "ck_damage_detections_ml_requires_model_version" in check_constraints


def test_occupancy_cannot_exceed_capacity(check_constraints):
    assert "ck_parking_zones_occupancy_within_capacity" in check_constraints


def test_a_vehicle_cannot_be_in_two_open_sessions(check_constraints, pg_app):
    """Enforced by the partial unique index, asserted here as a real insert."""
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from database.seed import seed_demo_data
    from models import CustodySession, CustodyStatus

    seed_demo_data()
    db.session.commit()

    open_session = (
        db.session.query(CustodySession)
        .filter(CustodySession.status != CustodyStatus.CHECKED_OUT.value)
        .first()
    )
    assert open_session is not None, "the seeded dataset must include an open session"

    duplicate = CustodySession(
        ticket_id=open_session.ticket_id,
        vehicle_id=open_session.vehicle_id,
        status=str(CustodyStatus.PARKED),
    )
    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_seeding_is_idempotent(pg_app):
    """Running the seeder twice must not duplicate a single row."""
    from sqlalchemy import text

    from app.extensions import db
    from database.seed import run

    # `run()` is the real entry point: it bootstraps the admin *and* seeds the
    # demo dataset, so the assertion below covers both paths.
    first_run = run(pg_app)
    assert first_run.created, "the first run must create something"
    first = _row_counts(db, text)

    second_run = run(pg_app)
    assert not second_run.created, (
        f"the second run must create nothing, but created: {second_run.created}"
    )
    second = _row_counts(db, text)

    assert first == second, f"seeding duplicated rows: {first} -> {second}"
    # 1 admin + 6 demo accounts (3 customers, 2 valet operators, 1 manager).
    assert first["users"] == 7
    # An open session, a closed one, and one awaiting pickup.
    assert first["custody_sessions"] == 3
    assert first["audit_logs"] == 4  # 1 admin creation + 3 demo history entries

    # Every seeded fix must declare its provenance. The project mandate is that
    # simulated telemetry is never mistaken for hardware telemetry, so this is
    # asserted rather than trusted.
    assert first["gps_events"] > 0, "the seeded dataset must include GPS fixes"
    unlabelled = db.session.execute(
        text("SELECT count(*) FROM gps_events WHERE source <> 'SIMULATED'")
    ).scalar_one()
    assert unlabelled == 0, f"{unlabelled} seeded GPS fix(es) not labelled SIMULATED"


def _row_counts(db, text) -> dict[str, int]:
    rows = db.session.execute(
        text(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables "
            "WHERE schemaname = 'public' ORDER BY relname"
        )
    ).fetchall()
    # pg_stat_user_tables is approximate, so count each table directly. The
    # names come from PostgreSQL's own catalog and are pinned to the known set
    # below, so the interpolated identifier cannot be attacker-controlled.
    counts = {}
    for name, _ in rows:
        if name not in EXPECTED_TABLES and name not in {"alembic_version", "schema_meta"}:
            continue
        counts[name] = db.session.execute(
            text(f'SELECT count(*) FROM "{name}"')  # noqa: S608 - allow-listed above
        ).scalar_one()
    return counts
