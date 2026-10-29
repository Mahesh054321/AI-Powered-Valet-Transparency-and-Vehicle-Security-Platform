"""Endpoint tests for vehicle security and geofencing.

These run against real PostgreSQL: a zone is created through the API, a vehicle
is checked in, position fixes are posted, the geofence engine classifies them,
alerts are persisted, and the results are read back. Nothing is stubbed.

The cases the module is required to cover:

* **GPS events** - accepted, stored with provenance, and classified;
* **geofence boundaries** - created from ``centre + radius``, read back with the
  polygon the map needs;
* **classification** - ``NORMAL``, ``WARNING``, ``SUSPICIOUS`` and ``BREACH``
  reachable through HTTP, each with the inputs that produced it;
* **security alerts** - raised for a breach, throttled rather than duplicated,
  and triageable only by a manager or administrator;
* **the simulator** - every scenario ingestible, deterministic, and labelled
  ``SIMULATED`` end to end.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from ai.anomaly import geofence as engine
from app.extensions import db
from models.audit_log import AuditLog
from models.enums import AlertStatus, MovementStatus, TelemetrySource
from models.gps_event import GpsEvent
from models.security_alert import SecurityAlert
from tests.workflow_helpers import bearer, make_user, make_vehicle, unique
from utils.security import Role

pytestmark = pytest.mark.postgres

GEOFENCES = "/api/v1/geofences"
GPS = "/api/v1/gps"
ALERTS = "/api/v1/alerts"
TICKETS = "/api/v1/tickets"

#: The yard used by these tests: small enough that a fix a few hundred metres
#: out is unambiguously a breach.
CENTRE = (27.4935, 77.6745)
RADIUS = 100.0


@pytest.fixture(autouse=True)
def _clean(pg_app):
    yield
    db.session.rollback()


def north_of(distance_metres: float) -> tuple[float, float]:
    return engine.destination_point(CENTRE[0], CENTRE[1], 0.0, distance_metres)


def _context(client):
    """A checked-in vehicle in a manager-created geofence, plus every actor."""
    manager = make_user(Role.VALET_MANAGER)
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    outsider = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    zone_response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Test yard",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": RADIUS,
        },
        headers=bearer(manager),
    )
    assert zone_response.status_code == 201, zone_response.get_json()
    zone_id = zone_response.get_json()["data"]["id"]

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201, issued.get_json()
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    checked_in = client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": zone_id},
        headers=bearer(operator),
    )
    assert checked_in.status_code == 200, checked_in.get_json()

    fetched = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(operator))
    session_id = fetched.get_json()["data"]["ticket"]["custody_session"]["id"]

    return {
        "manager": manager,
        "operator": operator,
        "customer": customer,
        "outsider": outsider,
        "vehicle": vehicle,
        "zone_id": zone_id,
        "session_id": session_id,
    }


def ago(**delta) -> str:
    """An ISO-8601 timestamp in the past, so tests never depend on wall-clock luck."""
    from datetime import UTC, datetime, timedelta

    return (datetime.now(tz=UTC) - timedelta(**delta)).isoformat()


def post_fix(client, context, latitude, longitude, **extra):
    body = {
        "custody_id": str(context["session_id"]),
        "latitude": latitude,
        "longitude": longitude,
    }
    body.update(extra)
    return client.post(f"{GPS}/events", json=body, headers=bearer(context["operator"]))


# ---------------------------------------------------------------------------
# Geofences
# ---------------------------------------------------------------------------
def test_a_geofence_is_created_from_a_centre_and_a_radius(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "North yard",
            "latitude": 27.5,
            "longitude": 77.68,
            "radius": 120.0,
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["radius_metres"] == 120.0
    assert data["centre"] == {"latitude": 27.5, "longitude": 77.68}
    # A circle is generated so the map has something to draw and the same
    # point-in-polygon test serves both shapes.
    assert len(data["geofence_polygon"]) == engine.CIRCLE_RING_POINTS
    assert data["status"] == "ACTIVE"


def test_an_explicit_polygon_overrides_the_generated_circle(client, pg_app):
    context = _context(client)
    polygon = [
        [77.6730, 27.4920],
        [77.6760, 27.4920],
        [77.6760, 27.4950],
        [77.6730, 27.4920],
    ]
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Irregular yard",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "geofence_polygon": polygon,
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["geofence_polygon"] == polygon


def test_a_duplicate_zone_code_is_a_conflict(client, pg_app):
    context = _context(client)
    code = unique("ZONE").upper()[:32]
    body = {
        "code": code,
        "name": "First",
        "latitude": CENTRE[0],
        "longitude": CENTRE[1],
        "radius": 80.0,
    }
    assert client.post(GEOFENCES, json=body, headers=bearer(context["manager"])).status_code == 201
    duplicate = client.post(GEOFENCES, json=body, headers=bearer(context["manager"]))
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["details"]["code"] == code


def test_a_zero_radius_is_refused_because_every_fix_would_be_a_breach(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "No yard",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 0,
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422
    assert "zero-radius" in response.get_json()["error"]["message"]


def test_an_absurdly_large_radius_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "A county",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 900_000,
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422


def test_a_polygon_vertex_that_is_not_a_pair_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Malformed",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "geofence_polygon": [[77.6730, 27.4920], [77.6760], [77.6760, 27.4950]],
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422


def test_a_vertex_outside_the_legal_coordinate_ranges_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Off the map",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "geofence_polygon": [[77.6730, 27.4920], [77.6760, 95.0], [77.6760, 27.4950]],
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422


def test_a_swapped_vertex_order_is_accepted_when_both_values_are_legal(client, pg_app):
    """Documents a real limitation rather than pretending the API catches it.

    At this latitude a ``[lat, lon]`` pair read as ``[lon, lat]`` still lands
    inside both legal ranges, so no range check can reject it. The API stores
    the ring as given and the map draws exactly that, so the failure is
    visible on the dashboard rather than silent - but a reviewer should know
    the boundary is only as trustworthy as the coordinates that defined it.
    """
    context = _context(client)
    swapped = [[27.4920, 77.6730], [27.4920, 77.6760], [27.4950, 77.6760]]
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Swapped but legal",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "geofence_polygon": swapped,
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["geofence_polygon"] == swapped


def test_a_polygon_with_fewer_than_three_vertices_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "A line",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "geofence_polygon": [[77.673, 27.492], [77.676, 27.492]],
        },
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422


def test_a_missing_required_field_is_reported_by_name(client, pg_app):
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={"name": "No code", "latitude": CENTRE[0], "longitude": CENTRE[1], "radius": 90},
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "code"


def test_an_operator_cannot_create_a_geofence(client, pg_app):
    """Moving a boundary changes the meaning of every future fix."""
    context = _context(client)
    response = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "Sneaky",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 90,
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 403
    assert "VALET_MANAGER" in response.get_json()["error"]["details"]["required"]


def test_an_anonymous_caller_cannot_list_geofences(client, pg_app):
    assert client.get(GEOFENCES).status_code == 401


def test_geofences_are_listed_with_the_geometry_the_map_needs(client, pg_app):
    context = _context(client)
    response = client.get(GEOFENCES, headers=bearer(context["customer"]))
    assert response.status_code == 200
    zones = response.get_json()["data"]["zones"]
    assert any(zone["id"] == str(context["zone_id"]) for zone in zones)
    for zone in zones:
        assert zone["geofence_polygon"]
        assert zone["centre"]["latitude"] is not None


def test_a_geofence_can_be_reconfigured_and_the_change_is_audited(client, pg_app):
    context = _context(client)
    response = client.patch(
        f"{GEOFENCES}/{context['zone_id']}",
        json={"radius": 180.0},
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["radius_metres"] == 180.0

    actions = [
        row.action
        for row in db.session.execute(
            select(AuditLog).where(AuditLog.entity_id == uuid.UUID(context["zone_id"]))
        ).scalars()
    ]
    assert actions.count("GEOFENCE_CONFIGURED") == 2  # creation plus the move


def test_a_nonexistent_zone_is_a_404(client, pg_app):
    context = _context(client)
    response = client.patch(
        f"{GEOFENCES}/{uuid.uuid4()}", json={"radius": 90}, headers=bearer(context["manager"])
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GPS ingest
# ---------------------------------------------------------------------------
def test_a_fix_inside_the_geofence_is_recorded_as_normal(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, CENTRE[0], CENTRE[1], speed=0.0)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["movement_status"] == MovementStatus.NORMAL.value
    assert data["gps_event"]["is_within_zone"] is True
    assert data["gps_event"]["distance_from_zone_metres"] < 0
    assert data["alerts_raised"] == []


def test_a_fix_far_outside_the_geofence_is_a_breach_and_raises_an_alert(client, pg_app):
    context = _context(client)
    position = north_of(500.0)
    response = post_fix(client, context, position[0], position[1], speed=5.0)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["movement_status"] == MovementStatus.BREACH.value
    assert data["gps_event"]["is_within_zone"] is False
    assert len(data["alerts_raised"]) == 1
    alert = data["alerts_raised"][0]
    assert alert["alert_type"] == "GEOFENCE_BREACH"
    assert alert["severity"] == "HIGH"
    assert alert["status"] == "OPEN"
    assert alert["reason"]
    assert 0.0 <= alert["confidence"] <= 1.0
    assert alert["evidence"]["distance_from_boundary_metres"] > 0


def test_a_fix_just_outside_the_boundary_is_only_a_warning(client, pg_app):
    context = _context(client)
    position = north_of(110.0)
    response = post_fix(client, context, position[0], position[1], speed=1.0)
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["movement_status"] == MovementStatus.WARNING.value
    assert data["alerts_raised"][0]["severity"] == "LOW"


def test_speeding_inside_the_yard_is_flagged(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, CENTRE[0], CENTRE[1], speed=55.0)
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["movement_status"] == MovementStatus.SUSPICIOUS.value
    assert any(a["alert_type"] == "UNUSUAL_SPEED" for a in data["alerts_raised"])


def test_the_verdict_is_stored_alongside_the_inputs_that_produced_it(client, pg_app):
    context = _context(client)
    # 100 m beyond the boundary: past the 25 m buffer, inside the 150 m breach
    # margin, so this is the SUSPICIOUS band rather than a full breach.
    position = north_of(200.0)
    response = post_fix(client, context, position[0], position[1], speed=5.0)
    event_id = response.get_json()["data"]["gps_event"]["id"]

    row = db.session.get(GpsEvent, uuid.UUID(event_id))
    assert row.movement_status == MovementStatus.SUSPICIOUS.value
    assert row.zone_id == uuid.UUID(context["zone_id"])
    assert float(row.distance_from_zone_metres) == pytest.approx(100.0, abs=5.0)
    assert row.is_within_zone is False
    # Every input needed to re-derive the verdict is on the same row.
    assert float(row.speed_kmph) == 5.0


def test_provenance_defaults_to_manual_for_a_keyed_in_fix(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, CENTRE[0], CENTRE[1], speed=0.0)
    event = response.get_json()["data"]["gps_event"]
    assert event["source"] == TelemetrySource.MANUAL.value
    assert event["is_simulated"] is False


def test_the_briefs_field_names_are_accepted_alongside_the_schemas(client, pg_app):
    """``custody_id``/``speed``/``timestamp`` as specified, not only the schema's."""
    from datetime import datetime

    context = _context(client)
    stamp = ago(minutes=15)
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "speed": 3.5,
            "timestamp": stamp,
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()
    event = response.get_json()["data"]["gps_event"]
    assert event["speed_kmph"] == 3.5
    # Stored as the schema's own field name, with the supplied instant intact.
    assert datetime.fromisoformat(event["recorded_at"]) == datetime.fromisoformat(stamp)


def _batch_fix(context, position, minutes_ago: int) -> dict:
    latitude, longitude = position
    return {
        "custody_id": str(context["session_id"]),
        "latitude": latitude,
        "longitude": longitude,
        "speed": 4.0,
        # Spaced a minute apart: the movement rules divide displacement by the
        # interval, so a realistic gap matters to the verdict.
        "timestamp": ago(minutes=minutes_ago),
    }


def test_a_batch_of_fixes_is_accepted_in_one_request(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={
            "source": "MANUAL",
            "fixes": [
                # Oldest first, all in the past.
                _batch_fix(context, CENTRE, 30),
                _batch_fix(context, north_of(50.0), 29),
                _batch_fix(context, north_of(400.0), 28),
            ],
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["accepted"] == 3
    statuses = [item["movement_status"] for item in data["results"]]
    assert statuses == [
        MovementStatus.NORMAL.value,
        MovementStatus.NORMAL.value,
        MovementStatus.BREACH.value,
    ]


def test_a_batch_fix_without_a_timestamp_is_refused(client, pg_app):
    """Un-timestamped fixes would be stamped microseconds apart.

    The movement rules divide displacement by that interval, so the whole
    upload would be read as physically impossible speed. Refusing the input is
    better than silently producing a wrong verdict for every fix in it.
    """
    context = _context(client)
    latitude, longitude = CENTRE
    response = client.post(
        f"{GPS}/events",
        json={
            "fixes": [
                {
                    "custody_id": str(context["session_id"]),
                    "latitude": latitude,
                    "longitude": longitude,
                }
            ]
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422, response.get_json()
    details = response.get_json()["error"]["details"]
    assert details["field"] == "timestamp"
    assert details["fixes_missing_timestamp"] == [0]


def test_an_empty_batch_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events", json={"fixes": []}, headers=bearer(context["operator"])
    )
    assert response.status_code == 422


def test_a_missing_custody_id_is_reported_by_name(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={"latitude": CENTRE[0], "longitude": CENTRE[1]},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "custody_id"


def test_an_unknown_custody_session_is_a_404_not_a_403(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={"custody_id": str(uuid.uuid4()), "latitude": CENTRE[0], "longitude": CENTRE[1]},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 404


def test_a_malformed_custody_id_is_a_422_not_a_500(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={"custody_id": "not-a-uuid", "latitude": CENTRE[0], "longitude": CENTRE[1]},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422


def test_a_latitude_out_of_range_is_refused(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, 91.0, CENTRE[1], speed=0.0)
    assert response.status_code == 422


def test_a_negative_speed_is_refused(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, CENTRE[0], CENTRE[1], speed=-5)
    assert response.status_code == 422


def test_a_fix_timestamped_far_in_the_future_is_refused(client, pg_app):
    """An alert raised by it could not be acknowledged until that time passed.

    The schema requires acknowledgement to come after the alert occurred, which
    is right. So a fix from a device with a skewed clock would otherwise create
    an alert a manager cannot act on -- discovered during an incident, which is
    the worst time to discover it.
    """
    from datetime import UTC, datetime, timedelta

    context = _context(client)
    future = (datetime.now(tz=UTC) + timedelta(hours=2)).isoformat()
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "timestamp": future,
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422, response.get_json()
    details = response.get_json()["error"]["details"]
    assert details["field"] == "timestamp"
    assert details["maximum_skew_seconds"] == pytest.approx(300.0)
    assert "clock" in response.get_json()["error"]["message"]


def test_a_fix_slightly_ahead_of_the_server_clock_is_still_accepted(client, pg_app):
    """Real receivers drift by seconds; only a large skew is rejected."""
    from datetime import UTC, datetime, timedelta

    context = _context(client)
    soon = (datetime.now(tz=UTC) + timedelta(seconds=20)).isoformat()
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "timestamp": soon,
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()


def test_an_alert_from_the_future_explains_itself_instead_of_failing_opaquely(client, pg_app):
    """Pre-existing skewed rows must be diagnosable, not just unactionable.

    The ingest guard stops new ones; this covers a row that already exists.
    """
    from datetime import UTC, datetime, timedelta

    context = _context(client)
    alert = _raise_breach(client, context)

    row = db.session.get(SecurityAlert, uuid.UUID(alert["id"]))
    row.occurred_at = datetime.now(tz=UTC) + timedelta(hours=1)
    db.session.commit()

    response = client.post(
        f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["manager"])
    )
    assert response.status_code == 409, response.get_json()
    details = response.get_json()["error"]["details"]
    assert details["hint"] == "correct_the_reporting_device_clock"
    assert "future" in response.get_json()["error"]["message"]


def test_a_customer_cannot_post_telemetry(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
        },
        headers=bearer(context["customer"]),
    )
    assert response.status_code == 403


def test_an_unknown_provenance_value_is_refused(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "source": "GUESSED",
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Alert throttling
# ---------------------------------------------------------------------------
def test_a_second_breach_within_the_cooldown_does_not_raise_a_second_alert(client, pg_app):
    """A vehicle driving away produces many fixes; only one alert should follow."""
    context = _context(client)
    first = post_fix(client, context, *north_of(500.0), speed=5.0)
    assert len(first.get_json()["data"]["alerts_raised"]) == 1

    second = post_fix(client, context, *north_of(520.0), speed=5.0)
    data = second.get_json()["data"]
    # Scoped to the geofence alert: the second fix is now further out and has a
    # predecessor, so other alert types may legitimately fire for the first
    # time. Suppression is per type, not global.
    assert [a["alert_type"] for a in data["alerts_raised"]] and all(
        a["alert_type"] != "GEOFENCE_BREACH" for a in data["alerts_raised"]
    )
    assert "GEOFENCE_BREACH" in data["alerts_suppressed"]

    rows = db.session.execute(
        select(SecurityAlert).where(
            SecurityAlert.custody_session_id == uuid.UUID(context["session_id"]),
            SecurityAlert.alert_type == "GEOFENCE_BREACH",
        )
    ).scalars().all()
    assert len(rows) == 1


def test_suppression_is_reported_rather_than_silent(client, pg_app):
    context = _context(client)
    post_fix(client, context, *north_of(500.0), speed=5.0)
    response = post_fix(client, context, *north_of(505.0), speed=5.0)
    # The fix is still classified; only the notification is throttled.
    assert response.get_json()["data"]["movement_status"] == MovementStatus.BREACH.value
    assert response.get_json()["data"]["alerts_suppressed"]


def test_distinct_alert_types_are_not_suppressed_by_each_other(client, pg_app):
    context = _context(client)
    response = post_fix(client, context, *north_of(500.0), speed=5.0)
    post_fix(client, context, CENTRE[0], CENTRE[1], speed=55.0)
    speed_alerts = db.session.execute(
        select(SecurityAlert).where(
            SecurityAlert.custody_session_id == uuid.UUID(context["session_id"]),
            SecurityAlert.alert_type == "UNUSUAL_SPEED",
        )
    ).scalars().all()
    assert speed_alerts, response.get_json()


# ---------------------------------------------------------------------------
# Tracking view
# ---------------------------------------------------------------------------
def test_the_tracking_view_exposes_the_vehicle_zone_boundary_and_status(client, pg_app):
    context = _context(client)
    post_fix(client, context, CENTRE[0], CENTRE[1], speed=0.0)
    post_fix(client, context, *north_of(500.0), speed=5.0)

    response = client.get(
        f"{GPS}/{context['session_id']}", headers=bearer(context["operator"])
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]

    assert data["vehicle"]["plate_number"] == context["vehicle"].plate_number
    assert data["zone"]["id"] == str(context["zone_id"])
    assert data["zone"]["geofence_polygon"]
    assert data["zone"]["effective_radius_metres"] == pytest.approx(100.0, abs=1.0)
    assert data["latest_fix"]["movement_status"] == MovementStatus.BREACH.value
    assert data["movement_status"] == MovementStatus.BREACH.value
    assert data["fix_count"] == 2
    assert data["status_counts"][MovementStatus.BREACH.value] == 1
    assert data["last_updated_at"]
    assert data["open_alert_count"] >= 1
    assert len(data["track"]) == 2


def test_the_tracking_view_labels_simulated_telemetry(client, pg_app):
    context = _context(client)
    client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "parked", "seed": 1},
        headers=bearer(context["operator"]),
    )
    response = client.get(
        f"{GPS}/{context['session_id']}", headers=bearer(context["operator"])
    )
    data = response.get_json()["data"]
    assert data["has_simulated_telemetry"] is True
    assert data["has_hardware_telemetry"] is False
    assert TelemetrySource.SIMULATED.value in data["sources"]
    assert "SIMULATED" in data["telemetry_note"]
    assert all(fix["is_simulated"] for fix in data["track"])


def test_a_customer_sees_their_own_tracking_view(client, pg_app):
    context = _context(client)
    post_fix(client, context, CENTRE[0], CENTRE[1], speed=0.0)
    response = client.get(
        f"{GPS}/{context['session_id']}", headers=bearer(context["customer"])
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["vehicle"]["plate_number"] == (
        context["vehicle"].plate_number
    )


def test_another_customer_gets_a_404_rather_than_a_403(client, pg_app):
    context = _context(client)
    response = client.get(
        f"{GPS}/{context['session_id']}", headers=bearer(context["outsider"])
    )
    assert response.status_code == 404


def test_tracking_an_unknown_session_is_a_404(client, pg_app):
    context = _context(client)
    response = client.get(f"{GPS}/{uuid.uuid4()}", headers=bearer(context["operator"]))
    assert response.status_code == 404


def test_fixes_can_be_paged_and_filtered_by_status(client, pg_app):
    context = _context(client)
    post_fix(client, context, CENTRE[0], CENTRE[1], speed=0.0)
    post_fix(client, context, *north_of(500.0), speed=5.0)

    response = client.get(
        f"{GPS}/{context['session_id']}/fixes",
        query_string={"status": MovementStatus.BREACH.value, "page_size": 10},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["total"] == 1
    assert data["fixes"][0]["movement_status"] == MovementStatus.BREACH.value


def test_an_unknown_status_filter_is_refused_with_the_allowed_values(client, pg_app):
    context = _context(client)
    response = client.get(
        f"{GPS}/{context['session_id']}/fixes",
        query_string={"status": "EXPLODED"},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422
    assert MovementStatus.NORMAL.value in response.get_json()["error"]["details"]["allowed"]


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------
def test_the_simulator_catalogue_lists_every_scenario(client, pg_app):
    from ai.synthetic.gps_tracks import TRACK_SCENARIOS

    context = _context(client)
    response = client.get(f"{GPS}/simulate/scenarios", headers=bearer(context["operator"]))
    assert response.status_code == 200
    scenarios = response.get_json()["data"]["scenarios"]
    assert {item["id"] for item in scenarios} == set(TRACK_SCENARIOS)
    assert all(item["label"] for item in scenarios)


@pytest.mark.parametrize(
    "scenario", ["parked", "jitter", "arrival", "retrieval", "unauthorised", "telemetry_loss"]
)
def test_every_simulator_scenario_ingests_and_labels_its_telemetry(client, pg_app, scenario):
    context = _context(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": scenario, "seed": 21},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]

    assert data["source"] == TelemetrySource.SIMULATED.value
    assert data["is_simulated"] is True
    assert data["fix_count"] > 0
    assert sum(data["status_counts"].values()) == data["fix_count"]
    assert data["notes"], "the response must carry the simulated-data warning"
    assert any("SIMULATED" in note for note in data["notes"])

    rows = db.session.execute(
        select(GpsEvent).where(GpsEvent.custody_session_id == uuid.UUID(context["session_id"]))
    ).scalars().all()
    assert rows
    assert all(row.source == TelemetrySource.SIMULATED.value for row in rows)
    assert all(row.movement_status in {item.value for item in MovementStatus} for row in rows)


def test_the_simulated_track_is_deterministic_for_a_seed(client, pg_app):
    context = _context(client)
    body = {"custody_id": str(context["session_id"]), "scenario": "unauthorised", "seed": 99}
    first = client.post(f"{GPS}/simulate", json=body, headers=bearer(context["operator"]))
    counts_a = first.get_json()["data"]["status_counts"]

    other = _context(client)
    body["custody_id"] = str(other["session_id"])
    second = client.post(f"{GPS}/simulate", json=body, headers=bearer(other["operator"]))
    counts_b = second.get_json()["data"]["status_counts"]
    assert counts_a == counts_b


def test_the_unauthorised_scenario_produces_alerts(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "unauthorised", "seed": 5},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert MovementStatus.BREACH.value in data["status_counts"]
    assert data["alerts_raised"]
    types = {alert["alert_type"] for alert in data["alerts_raised"]}
    assert "GEOFENCE_BREACH" in types


def test_the_parked_scenario_produces_no_breach(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "parked", "seed": 8},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["status_counts"] == {MovementStatus.NORMAL.value: data["fix_count"]}
    assert data["alerts_raised"] == []


def _custody_without_zone(client):
    """A checked-in vehicle whose custody session names no parking zone."""
    from models.custody_session import CustodySession
    from models.enums import CustodyStatus
    from models.valet_ticket import ValetTicket

    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201, issued.get_json()
    ticket = db.session.get(ValetTicket, uuid.UUID(issued.get_json()["data"]["ticket"]["id"]))

    custody = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=None,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        status=str(CustodyStatus.PARKED),
        odometer_at_drop_off=10_000,
        fuel_level_percent=70,
    )
    db.session.add(custody)
    db.session.commit()
    return {"operator": operator, "session_id": str(custody.id)}


def test_simulating_without_an_assigned_zone_is_a_conflict(client, pg_app):
    """There is no geofence to move around, so say so instead of guessing one."""
    context = _custody_without_zone(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": context["session_id"], "scenario": "parked"},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error"]["details"]["hint"] == "assign_zone_first"


def test_a_fix_with_no_assigned_zone_says_it_could_not_be_evaluated(client, pg_app):
    """Not silently NORMAL: an unjudgeable fix is not a safe one."""
    context = _custody_without_zone(client)
    response = client.post(
        f"{GPS}/events",
        json={
            "custody_id": context["session_id"],
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "speed": 0.0,
        },
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["movement_status"] == MovementStatus.WARNING.value
    assert data["gps_event"]["zone_id"] is None
    assert data["gps_event"]["distance_from_zone_metres"] is None
    assert data["gps_event"]["is_within_zone"] is False
    assert any("No parking zone geometry" in reason for reason in data["reasons"])


def test_an_unknown_scenario_is_refused_with_the_allowed_values(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "teleport"},
        headers=bearer(context["operator"]),
    )
    assert response.status_code == 422
    assert "parked" in response.get_json()["error"]["details"]["allowed"]


def test_a_customer_cannot_generate_simulated_telemetry(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "parked"},
        headers=bearer(context["customer"]),
    )
    assert response.status_code == 403


def test_a_simulated_track_is_audited_as_simulated(client, pg_app):
    context = _context(client)
    client.post(
        f"{GPS}/simulate",
        json={"custody_id": str(context["session_id"]), "scenario": "arrival", "seed": 2},
        headers=bearer(context["operator"]),
    )
    rows = db.session.execute(
        select(AuditLog).where(AuditLog.action == "GPS_TRACK_SIMULATED")
    ).scalars().all()
    assert rows
    assert rows[-1].new_values["source"] == TelemetrySource.SIMULATED.value
    assert rows[-1].new_values["scenario"] == "arrival"


# ---------------------------------------------------------------------------
# Alert triage
# ---------------------------------------------------------------------------
def _raise_breach(client, context):
    response = post_fix(client, context, *north_of(500.0), speed=5.0)
    return response.get_json()["data"]["alerts_raised"][0]


def test_alerts_are_listed_newest_first(client, pg_app):
    context = _context(client)
    _raise_breach(client, context)
    response = client.get(ALERTS, headers=bearer(context["manager"]))
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["total"] >= 1
    assert data["alerts"][0]["alert_type"] == "GEOFENCE_BREACH"
    assert data["alerts"][0]["is_open"] is True


def test_alerts_can_be_filtered_by_severity(client, pg_app):
    context = _context(client)
    _raise_breach(client, context)
    response = client.get(
        ALERTS,
        query_string={"severity": "HIGH", "custody_session_id": str(context["session_id"])},
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 200
    assert all(a["severity"] == "HIGH" for a in response.get_json()["data"]["alerts"])


def test_an_invalid_severity_filter_is_refused(client, pg_app):
    context = _context(client)
    response = client.get(
        ALERTS, query_string={"severity": "APOCALYPTIC"}, headers=bearer(context["manager"])
    )
    assert response.status_code == 422


def test_a_customer_sees_only_their_own_alerts(client, pg_app):
    context = _context(client)
    _raise_breach(client, context)

    own = client.get(ALERTS, headers=bearer(context["customer"]))
    assert own.status_code == 200
    assert own.get_json()["data"]["total"] >= 1

    other = _context(client)
    strangers = client.get(ALERTS, headers=bearer(other["customer"]))
    assert strangers.status_code == 200
    assert all(
        alert["custody_session_id"] != str(context["session_id"])
        for alert in strangers.get_json()["data"]["alerts"]
    )


def test_a_manager_can_acknowledge_an_alert(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["manager"])
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == AlertStatus.ACKNOWLEDGED.value
    assert data["acknowledged_at"]
    assert data["acknowledged_by_email"] == context["manager"].email
    assert data["is_open"] is True  # acknowledged is not closed


def test_acknowledgement_is_recorded_in_the_audit_log(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    client.post(f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["manager"]))
    # Scoped to this alert rather than reading the last row of an unordered
    # result set. The test database is session-scoped and shared, so "the
    # newest row" is whatever order PostgreSQL happens to return -- asserting
    # on position makes this pass or fail depending on unrelated tests.
    rows = db.session.execute(
        select(AuditLog).where(
            AuditLog.action == "ALERT_ACKNOWLEDGED",
            AuditLog.entity_id == uuid.UUID(alert["id"]),
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_email == context["manager"].email
    assert rows[0].outcome == "SUCCESS"


def test_a_manager_can_resolve_an_alert(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/resolve",
        json={"notes": "Valet was relocating the vehicle."},
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == AlertStatus.RESOLVED.value
    assert data["resolved_at"]
    assert data["resolution_notes"] == "Valet was relocating the vehicle."
    assert data["is_open"] is False


def test_an_alert_can_be_dismissed_as_a_false_positive(client, pg_app):
    """Dismissal is kept distinct: it is feedback about the detector itself."""
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/resolve",
        json={"dismiss": True, "notes": "GPS multipath beside the wall."},
        headers=bearer(context["manager"]),
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == AlertStatus.DISMISSED.value


def test_resolving_without_acknowledging_still_records_who_saw_it(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(context["manager"])
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["acknowledged_at"] is not None
    assert data["resolved_at"] >= data["acknowledged_at"]


def test_an_already_resolved_alert_cannot_be_resolved_again(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    client.post(f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(context["manager"]))
    again = client.post(
        f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(context["manager"])
    )
    assert again.status_code == 409


def test_an_already_resolved_alert_cannot_be_acknowledged(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    client.post(f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(context["manager"]))
    response = client.post(
        f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["manager"])
    )
    assert response.status_code == 409


def test_an_operator_cannot_acknowledge_an_alert(client, pg_app):
    """The person who drove the vehicle does not judge the alert about it."""
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["operator"])
    )
    assert response.status_code == 403
    assert "VALET_MANAGER" in response.get_json()["error"]["details"]["required"]


def test_an_operator_cannot_resolve_an_alert(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(context["operator"])
    )
    assert response.status_code == 403


def test_a_customer_cannot_acknowledge_an_alert(client, pg_app):
    context = _context(client)
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/acknowledge", headers=bearer(context["customer"])
    )
    assert response.status_code == 403


def test_an_unknown_alert_is_a_404(client, pg_app):
    context = _context(client)
    response = client.post(
        f"{ALERTS}/{uuid.uuid4()}/acknowledge", headers=bearer(context["manager"])
    )
    assert response.status_code == 404


def test_an_admin_may_also_triage(client, pg_app):
    context = _context(client)
    admin = make_user(Role.ADMIN)
    db.session.commit()
    alert = _raise_breach(client, context)
    response = client.post(
        f"{ALERTS}/{alert['id']}/resolve", json={}, headers=bearer(admin)
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The unversioned alias
# ---------------------------------------------------------------------------
def test_the_unversioned_aliases_behave_identically(client, pg_app):
    context = _context(client)

    posted = client.post(
        "/api/gps/events",
        json={
            "custody_id": str(context["session_id"]),
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "speed": 0.0,
        },
        headers=bearer(context["operator"]),
    )
    assert posted.status_code == 201, posted.get_json()

    tracked = client.get(
        f"/api/gps/{context['session_id']}", headers=bearer(context["operator"])
    )
    assert tracked.status_code == 200
    assert tracked.get_json()["data"]["fix_count"] == 1

    assert client.get("/api/geofences", headers=bearer(context["manager"])).status_code == 200
    assert client.get("/api/alerts", headers=bearer(context["manager"])).status_code == 200
