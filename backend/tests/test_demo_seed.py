"""Tests for the demonstration dataset and the reset/reseed command.

These exist because the demo is what an examiner actually looks at, and the
failure mode is silent: a seeder that quietly stops labelling its GPS rows, or
an alert that cites a fix which no longer exists, still "works" - it just
misrepresents the platform. So the invariants the demo promises are asserted
here against the database, not against the code that wrote it.

PostgreSQL only: the seeder writes real rows through the real models.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.extensions import db

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def seeded(pg_app):
    """Seed once for the module and hand back the app."""
    from database.seed import run

    run(pg_app)
    return pg_app


def _scalar(app, sql: str) -> int:
    with app.app_context():
        return int(db.session.execute(text(sql)).scalar_one())


# ---------------------------------------------------------------------------
# The curated layer matches the brief
# ---------------------------------------------------------------------------
def test_curated_seed_has_three_customers(seeded):
    count = _scalar(
        seeded,
        "SELECT count(*) FROM users WHERE role = 'CUSTOMER' "
        "AND notes NOT LIKE '%Generated operational history%'",
    )
    assert count == 3, f"expected 3 curated customers, found {count}"


def test_curated_seed_has_three_vehicles(seeded):
    count = _scalar(
        seeded, "SELECT count(*) FROM vehicles WHERE notes = '[DEMO] Seeded demonstration vehicle.'"
    )
    assert count >= 3, f"expected at least 3 curated vehicles, found {count}"


def test_curated_seed_has_two_valets_and_one_manager(seeded):
    valets = _scalar(seeded, "SELECT count(*) FROM users WHERE role = 'VALET_OPERATOR'")
    managers = _scalar(seeded, "SELECT count(*) FROM users WHERE role = 'VALET_MANAGER'")
    assert valets == 2, f"expected 2 valet operators, found {valets}"
    assert managers == 1, f"expected 1 manager, found {managers}"


def test_curated_seed_has_at_least_two_zones(seeded):
    count = _scalar(seeded, "SELECT count(*) FROM parking_zones")
    assert count >= 2, f"expected at least 2 parking zones, found {count}"


def test_every_curated_customer_owns_a_vehicle(seeded):
    """A customer with no vehicle has an empty dashboard, which looks broken."""
    orphans = _scalar(
        seeded,
        "SELECT count(*) FROM users u WHERE u.role = 'CUSTOMER' "
        "AND u.notes NOT LIKE '%Generated operational history%' "
        "AND NOT EXISTS (SELECT 1 FROM vehicles v WHERE v.owner_id = u.id)",
    )
    assert orphans == 0, f"{orphans} curated customer(s) own no vehicle"


# ---------------------------------------------------------------------------
# Provenance labelling - the project mandate
# ---------------------------------------------------------------------------
def test_every_seeded_gps_fix_is_labelled_simulated(seeded):
    total = _scalar(seeded, "SELECT count(*) FROM gps_events")
    assert total > 0, "the seeded dataset must include GPS fixes"

    unlabelled = _scalar(seeded, "SELECT count(*) FROM gps_events WHERE source <> 'SIMULATED'")
    assert unlabelled == 0, f"{unlabelled} GPS fix(es) not labelled SIMULATED"

    hardware = _scalar(
        seeded,
        "SELECT count(*) FROM gps_events "
        "WHERE source IN ('HARDWARE', 'VEHICLE_GPS', 'CAR_GPS', 'REAL_GPS')",
    )
    assert hardware == 0, f"{hardware} GPS fix(es) falsely claim real hardware provenance"


def test_seeded_telemetry_device_ids_admit_they_are_simulated(seeded):
    """The device id is what a reviewer sees on the tracking panel."""
    rows = []
    with seeded.app_context():
        rows = db.session.execute(
            text("SELECT DISTINCT device_id FROM gps_events WHERE device_id IS NOT NULL")
        ).fetchall()
    assert rows, "seeded fixes should carry a device id"
    for (device_id,) in rows:
        assert device_id.startswith("SIM-"), f"device id {device_id!r} does not read as simulated"


def test_both_normal_and_suspicious_movement_samples_exist(seeded):
    """A dashboard that is all alarms, or all clear, demonstrates nothing."""
    normal = _scalar(seeded, "SELECT count(*) FROM gps_events WHERE movement_status = 'NORMAL'")
    flagged = _scalar(
        seeded, "SELECT count(*) FROM gps_events WHERE movement_status IN ('SUSPICIOUS', 'BREACH')"
    )
    assert normal > 0, "no NORMAL movement samples seeded"
    assert flagged > 0, "no SUSPICIOUS/BREACH movement samples seeded"


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------
def test_no_alert_cites_a_missing_gps_fix(seeded):
    orphans = _scalar(
        seeded,
        "SELECT count(*) FROM security_alerts a WHERE a.gps_event_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM gps_events g WHERE g.id = a.gps_event_id)",
    )
    assert orphans == 0, f"{orphans} alert(s) cite a GPS fix that does not exist"


def test_seeded_predictions_admit_their_history_is_synthetic(seeded):
    """The retrieval model is a prototype over synthetic data; say so per row."""
    with seeded.app_context():
        rows = db.session.execute(
            text("SELECT inputs_snapshot, explanation FROM retrieval_predictions")
        ).fetchall()
    assert rows, "the seeded dataset must include retrieval predictions"
    for snapshot, explanation in rows:
        assert snapshot.get("history_is_synthetic") is True, (
            "a seeded prediction does not flag its history as synthetic"
        )
        assert "synthetic" in (explanation or "").lower() or "demonstration" in (
            explanation or ""
        ).lower(), "a seeded prediction's explanation does not disclose its provenance"


def test_no_seeded_prediction_claims_a_trained_model(seeded):
    """The mandate forbids implying a model was trained when it was not."""
    with seeded.app_context():
        rows = db.session.execute(
            text("SELECT method, model_version FROM retrieval_predictions")
        ).fetchall()
    assert rows
    for method, model_version in rows:
        assert model_version is None or model_version.startswith("retrieval-baseline"), (
            f"unexpected model_version {model_version!r} on a seeded prediction"
        )
        assert "heuristic" in method or "baseline" in method, (
            f"seeded prediction method {method!r} reads as a trained model"
        )


# ---------------------------------------------------------------------------
# The reset/reseed command
# ---------------------------------------------------------------------------
def test_verify_passes_on_a_seeded_database(seeded):
    from utils.scripts.seed_demo import verify

    counts, problems = verify(seeded)
    assert problems == [], f"verification reported problems: {problems}"
    assert counts["curated_customers"] == 3
    assert counts["curated_vehicles"] >= 3
    assert counts["gps_events"] > 0


def test_verify_detects_unlabelled_telemetry(seeded):
    """The check has to have teeth: mislabel a row and it must complain."""
    from utils.scripts.seed_demo import verify

    with seeded.app_context():
        target = db.session.execute(text("SELECT id FROM gps_events LIMIT 1")).scalar_one()
        db.session.execute(
            text("UPDATE gps_events SET source = 'REAL_GPS' WHERE id = :id"), {"id": target}
        )
        db.session.commit()
    try:
        _counts, problems = verify(seeded)
        assert any("hardware provenance" in problem for problem in problems), (
            f"verification missed a mislabelled fix; problems were {problems}"
        )
    finally:
        with seeded.app_context():
            db.session.execute(
                text("UPDATE gps_events SET source = 'SIMULATED' WHERE id = :id"), {"id": target}
            )
            db.session.commit()


def test_verify_detects_a_missing_customer(seeded):
    """Demote one customer and the check must notice the count drop.

    Changing the role rather than deleting the row avoids cascading through
    every ticket that references the customer; the count check is what is
    under test, not the cascade behaviour.
    """
    from utils.scripts.seed_demo import verify

    with seeded.app_context():
        email = db.session.execute(
            text(
                "SELECT email FROM users WHERE role = 'CUSTOMER' "
                "AND notes NOT LIKE '%Generated operational history%' "
                "ORDER BY email LIMIT 1"
            )
        ).scalar_one()
        db.session.execute(
            text("UPDATE users SET role = 'VALET_OPERATOR' WHERE email = :e"), {"e": email}
        )
        db.session.commit()
    try:
        counts, problems = verify(seeded)
        assert counts["curated_customers"] == 2
        assert any("customer" in problem for problem in problems), (
            f"verification missed a missing customer; problems were {problems}"
        )
    finally:
        with seeded.app_context():
            db.session.execute(
                text("UPDATE users SET role = 'CUSTOMER' WHERE email = :e"), {"e": email}
            )
            db.session.commit()


def test_reseeding_is_idempotent(seeded):
    from database.seed import run

    before = _scalar(seeded, "SELECT count(*) FROM gps_events")
    result = run(seeded)
    after = _scalar(seeded, "SELECT count(*) FROM gps_events")

    assert result.created == [], f"a second seed created rows: {result.created}"
    assert before == after, f"reseeding changed the GPS row count {before} -> {after}"
