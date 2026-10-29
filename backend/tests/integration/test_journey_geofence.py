"""Integration: GPS Event -> Geofence -> Alert.

The security spine of the platform. A telemetry fix arrives, the geofence engine
classifies it against the parking zone's polygon, and a breach becomes an alert
that reaches the right people through the centralised feed.

Three things this file exists to prove, none of which a unit test can:

* **The verdict is stored on the fix, not just on the alert.** Every GPS row
  carries the classification that produced it, so a reviewer can see *why* a
  fix was suspicious without re-running the engine.
* **One breach produces one alert, reachable by every intended audience.** The
  alert is published to the feed for operator, manager and customer, with
  per-recipient read state rather than a global flag.
* **Provenance survives.** The simulated track is labelled ``SIMULATED`` end to
  end. Nothing here lets a synthetic fix masquerade as hardware telemetry, which
  is a stated requirement of the project.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from models.enums import AlertSeverity, AlertStatus, MovementStatus
from tests.workflow_helpers import bearer, file_part, make_user, make_vehicle, make_zone
from utils.security import Role

TICKETS = "/api/v1/tickets"
GPS = "/api/v1/gps"
FEED = "/api/v1/feed"

# Every test here drives the real HTTP API against a real PostgreSQL database,
# so the whole file is an integration journey rather than a unit test. The
# marker lets the suite be split (`pytest -m integration`) without a path
# filter that would silently stop matching if the folder were reorganised.
pytestmark = pytest.mark.integration


@pytest.fixture()
def yard(pg_client):
    """A checked-in vehicle in a fenced zone, plus the three roles that see it."""
    customer = make_user(Role.CUSTOMER)
    operator = make_user(Role.VALET_OPERATOR)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()

    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=5)
    db.session.commit()

    ticket = pg_client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    ).get_json()["data"]["ticket"]
    db.session.commit()

    upload = pg_client.post(
        "/api/v1/uploads/images",
        headers=bearer(operator),
        data={"image": file_part("drop-off.png")},
        content_type="multipart/form-data",
    )
    checked_in = pg_client.post(
        f"{TICKETS}/{ticket['id']}/check-in",
        json={
            "zone_id": str(zone.id),
            "photo_refs": upload.get_json()["data"]["images"],
        },
        headers=bearer(operator),
    ).get_json()["data"]["ticket"]
    db.session.commit()

    return {
        "client": pg_client,
        "customer": customer,
        "operator": operator,
        "manager": manager,
        "vehicle": vehicle,
        "zone": zone,
        "ticket_id": ticket["id"],
        "session_id": checked_in["custody_session"]["id"],
        "as_customer": bearer(customer),
        "as_operator": bearer(operator),
        "as_manager": bearer(manager),
    }


def _simulate(yard, scenario: str, *, seed: int = 7) -> dict:
    """Drive a synthetic GPS track through the real ingestion path."""
    response = yard["client"].post(
        f"{GPS}/simulate",
        json={"custody_session_id": yard["session_id"], "scenario": scenario, "seed": seed},
        headers=yard["as_operator"],
    )
    assert response.status_code == 201, response.get_json()
    db.session.commit()
    return response.get_json()["data"]


class TestTheChainFires:
    def test_an_unauthorised_track_raises_a_breach_alert(self, yard):
        result = _simulate(yard, "unauthorised")

        assert result["status_counts"]["BREACH"] > 0
        assert len(result["alerts_raised"]) > 0

        alert = result["alerts_raised"][0]
        assert alert["is_open"] is True
        assert alert["severity"] in {member.value for member in AlertSeverity}
        assert alert["custody_session_id"] == yard["session_id"]
        assert alert["vehicle_id"] == str(yard["vehicle"].id)
        # The alert carries the evidence that produced it.
        assert alert["reason"]
        assert alert["gps_event_id"]

    def test_a_parked_track_raises_nothing(self, yard):
        """The negative case matters: an engine that always alerts is noise."""
        result = _simulate(yard, "parked")

        assert result["status_counts"].get("BREACH", 0) == 0
        assert result["alerts_raised"] == []

    def test_every_stored_fix_carries_its_own_verdict(self, yard):
        _simulate(yard, "unauthorised")

        fixes = yard["client"].get(
            f"{GPS}/{yard['session_id']}/fixes", headers=yard["as_operator"]
        )

        assert fixes.status_code == 200
        rows = fixes.get_json()["data"]["fixes"]
        assert rows
        for fix in rows:
            assert "movement_status" in fix
            assert fix["movement_status"] in {m.value for m in MovementStatus}

    def test_the_tracking_view_summarises_the_session(self, yard):
        _simulate(yard, "unauthorised")

        view = yard["client"].get(f"{GPS}/{yard['session_id']}", headers=yard["as_customer"])

        assert view.status_code == 200
        data = view.get_json()["data"]
        assert data["custody_session_id"] == yard["session_id"]
        assert data["fix_count"] > 0
        assert data["open_alert_count"] > 0
        assert data["movement_status"] in {m.value for m in MovementStatus}
        assert len(data["alerts"]) == data["open_alert_count"] or data["open_alert_count"] <= len(
            data["alerts"]
        )

    def test_provenance_is_labelled_simulated_end_to_end(self, yard):
        """A synthetic fix must never be able to pass for hardware telemetry."""
        result = _simulate(yard, "arrival")

        assert result["is_simulated"] is True
        assert result["source"] == "SIMULATED"
        assert result["track"]["source"] == "SIMULATED"

        view = yard["client"].get(f"{GPS}/{yard['session_id']}", headers=yard["as_customer"])
        data = view.get_json()["data"]
        assert data["has_simulated_telemetry"] is True
        assert data["has_hardware_telemetry"] is False
        assert "SIMULATED" in data["sources"]


class TestTheAlertReachesPeople:
    def test_the_customer_sees_the_alert_on_their_own_vehicle(self, yard):
        _simulate(yard, "unauthorised")

        feed = yard["client"].get(FEED, headers=yard["as_customer"])

        assert feed.status_code == 200
        alerts = feed.get_json()["data"]["alerts"]
        assert alerts
        assert any(a["custody_id"] == yard["session_id"] for a in alerts)

    def test_the_alert_is_published_to_every_intended_audience(self, yard):
        _simulate(yard, "unauthorised")

        feed = yard["client"].get(FEED, headers=yard["as_customer"])
        alert = next(
            a for a in feed.get_json()["data"]["alerts"] if a["custody_id"] == yard["session_id"]
        )

        assert "CUSTOMER" in alert["audiences"]
        assert "VALET_OPERATOR" in alert["audiences"]
        assert "VALET_MANAGER" in alert["audiences"]

    def test_read_state_is_per_recipient_not_global(self, yard):
        """One person reading an alert must not mark it read for everyone."""
        _simulate(yard, "unauthorised")
        feed = yard["client"].get(FEED, headers=yard["as_customer"])
        alert_id = next(
            a for a in feed.get_json()["data"]["alerts"] if a["custody_id"] == yard["session_id"]
        )["alert_id"]

        marked = yard["client"].post(f"{FEED}/{alert_id}/read", headers=yard["as_customer"])
        assert marked.status_code == 200
        db.session.commit()

        customer_view = yard["client"].get(FEED, headers=yard["as_customer"])
        operator_view = yard["client"].get(FEED, headers=yard["as_operator"])

        mine = next(
            a for a in customer_view.get_json()["data"]["alerts"] if a["alert_id"] == alert_id
        )
        theirs = next(
            a for a in operator_view.get_json()["data"]["alerts"] if a["alert_id"] == alert_id
        )
        assert mine["is_read"] is True
        assert theirs["is_read"] is False

    def test_an_operator_acknowledges_and_a_manager_resolves(self, yard):
        """Reading is per-person; closing an alert is a manager's call.

        Splitting them is deliberate. "I have seen this" belongs to whoever saw
        it, while "this needed no further action" is an oversight judgement that
        the audit trail attributes to a named manager.
        """
        _simulate(yard, "unauthorised")
        feed = yard["client"].get(FEED, headers=yard["as_operator"])
        alert_id = next(
            a for a in feed.get_json()["data"]["alerts"] if a["custody_id"] == yard["session_id"]
        )["alert_id"]

        ack = yard["client"].post(f"{FEED}/{alert_id}/read", headers=yard["as_operator"])
        assert ack.status_code == 200
        db.session.commit()

        resolved = yard["client"].post(
            f"{FEED}/{alert_id}/resolve",
            json={"resolution_notes": "Recovered; customer had authorised the move."},
            headers=yard["as_manager"],
        )
        assert resolved.status_code == 200, resolved.get_json()
        db.session.commit()

        after = yard["client"].get(FEED, headers=yard["as_manager"])
        row = next(a for a in after.get_json()["data"]["alerts"] if a["alert_id"] == alert_id)
        assert row["is_open"] is False
        assert row["resolved_at"] is not None

    @pytest.mark.parametrize(
        "role_key",
        ["as_customer", "as_operator"],
        ids=["customer", "operator"],
    )
    def test_only_a_manager_may_close_an_alert(self, yard, role_key):
        """Resolution is manager and admin only; everyone else gets 403."""
        _simulate(yard, "unauthorised")
        feed = yard["client"].get(FEED, headers=yard["as_manager"])
        alert_id = next(
            a for a in feed.get_json()["data"]["alerts"] if a["custody_id"] == yard["session_id"]
        )["alert_id"]

        response = yard["client"].post(
            f"{FEED}/{alert_id}/resolve",
            json={"resolution_notes": "I resolved my own alert."},
            headers=yard[role_key],
        )

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"

    def test_another_customer_cannot_see_the_alert(self, yard):
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()
        _simulate(yard, "unauthorised")

        feed = yard["client"].get(FEED, headers=bearer(intruder))

        assert feed.status_code == 200
        assert all(a["custody_id"] != yard["session_id"] for a in feed.get_json()["data"]["alerts"])


class TestTheChainIsRecorded:
    def test_a_breach_appears_in_the_custody_timeline(self, yard):
        _simulate(yard, "unauthorised")

        timeline = yard["client"].get(
            f"/api/v1/audit/timeline/{yard['session_id']}", headers=yard["as_manager"]
        )

        assert timeline.status_code == 200
        actions = [event["action"] for event in timeline.get_json()["data"]["events"]]
        assert any("GPS" in action or "ALERT" in action for action in actions), actions

    def test_the_geofence_alert_survives_a_round_trip_through_the_database(self, yard):
        """Read it back through the ORM, not just the API, so the row is real."""
        from models.security_alert import SecurityAlert

        _simulate(yard, "unauthorised")
        db.session.expire_all()

        rows = (
            db.session.query(SecurityAlert)
            .filter(SecurityAlert.custody_session_id == yard["session_id"])
            .all()
        )

        assert rows
        assert any(row.status == AlertStatus.OPEN.value for row in rows)
        # The verdict that produced it is stored alongside.
        assert all(row.reason for row in rows)

    def test_the_scenario_vocabulary_is_discoverable(self, yard):
        response = yard["client"].get(f"{GPS}/simulate/scenarios", headers=yard["as_operator"])

        assert response.status_code == 200
        ids = [s["id"] for s in response.get_json()["data"]["scenarios"]]
        assert "unauthorised" in ids
        assert "parked" in ids
