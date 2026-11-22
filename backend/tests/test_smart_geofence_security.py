"""Tests for Smart Geofencing, Real GPS sources, Tamper detection, and SSE streaming."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai.anomaly import geofence as engine
from app.extensions import db
from models.enums import AlertStatus, MovementStatus, TelemetrySource, ZoneStatus, ZoneType
from models.gps_event import GpsEvent
from models.parking_zone import ParkingZone
from models.security_alert import SecurityAlert
from tests.workflow_helpers import bearer, make_user, make_vehicle, unique
from utils.security import Role

pytestmark = pytest.mark.postgres

GEOFENCES = "/api/v1/geofences"
GPS = "/api/v1/gps"
ALERTS = "/api/v1/alerts"
TICKETS = "/api/v1/tickets"
CENTRE = (28.4595, 77.0266)  # Gurgaon test coordinates


def _setup_context(client):
    manager = make_user(Role.VALET_MANAGER)
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user(Role.CUSTOMER)
    outsider = make_user(Role.CUSTOMER)
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    zone_resp = client.post(
        GEOFENCES,
        json={
            "code": unique("SZ").upper()[:32],
            "name": "Main Valet Lot",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 150.0,
            "zone_type": "CIRCLE",
        },
        headers=bearer(manager),
    )
    assert zone_resp.status_code == 201
    zone_id = zone_resp.get_json()["data"]["id"]

    ticket_resp = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert ticket_resp.status_code == 201
    ticket_id = ticket_resp.get_json()["data"]["ticket"]["id"]

    checked_in = client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": zone_id},
        headers=bearer(operator),
    )
    assert checked_in.status_code == 200

    fetched = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(operator))
    session_id = fetched.get_json()["data"]["ticket"]["custody_session"]["id"]

    return {
        "manager": manager,
        "operator": operator,
        "customer": customer,
        "outsider": outsider,
        "vehicle": vehicle,
        "zone_id": zone_id,
        "ticket_id": ticket_id,
        "session_id": session_id,
    }


def test_preview_geofence_calculation(client, pg_app):
    """POST /geofences/preview calculates metrics for circles and rectangles without saving."""
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()

    # Circle preview
    resp = client.post(
        f"{GEOFENCES}/preview",
        json={
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "zone_type": "CIRCLE",
            "radius": 80.0,
            "buffer_metres": 20.0,
        },
        headers=bearer(manager),
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["zone_type"] == "CIRCLE"
    assert data["effective_radius_metres"] == 80.0
    assert len(data["geofence_polygon"]) >= 32
    assert data["buffer_metres"] == 20.0

    # Rectangle preview
    resp_rect = client.post(
        f"{GEOFENCES}/preview",
        json={
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "zone_type": "RECTANGLE",
            "length_metres": 120.0,
            "width_metres": 60.0,
            "orientation_degrees": 45.0,
        },
        headers=bearer(manager),
    )
    assert resp_rect.status_code == 200
    rect_data = resp_rect.get_json()["data"]
    assert rect_data["zone_type"] == "RECTANGLE"
    assert len(rect_data["geofence_polygon"]) == 5
    assert rect_data["estimated_area_sq_metres"] > 0


def test_customer_can_create_and_manage_safe_zone_for_own_vehicle(client, pg_app):
    """Customers can create safe zones for their vehicles with RBAC isolation."""
    ctx = _setup_context(client)
    customer = ctx["customer"]
    outsider = ctx["outsider"]
    vehicle = ctx["vehicle"]

    # Outsider cannot create safe zone for another customer's vehicle
    forbidden = client.post(
        GEOFENCES,
        json={
            "code": unique("SZ-OUT").upper()[:32],
            "name": "Outsider Attempt",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": 100.0,
            "vehicle_id": str(vehicle.id),
        },
        headers=bearer(outsider),
    )
    assert forbidden.status_code == 403

    # Customer can create safe zone for their own vehicle
    created_resp = client.post(
        GEOFENCES,
        json={
            "code": unique("SZ-CUST").upper()[:32],
            "name": "Aisha Home Yard",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "zone_type": "RECTANGLE",
            "length_metres": 100.0,
            "width_metres": 50.0,
            "vehicle_id": str(vehicle.id),
        },
        headers=bearer(customer),
    )
    assert created_resp.status_code == 201
    zone_id = created_resp.get_json()["data"]["id"]

    # Customer can fetch zone details
    zone_details = client.get(f"{GEOFENCES}/{zone_id}", headers=bearer(customer))
    assert zone_details.status_code == 200
    assert zone_details.get_json()["data"]["zone_type"] == "RECTANGLE"

    # Customer can deactivate zone
    deact = client.post(f"{GEOFENCES}/{zone_id}/deactivate", headers=bearer(customer))
    assert deact.status_code == 200
    assert deact.get_json()["data"]["status"] == ZoneStatus.DEACTIVATED.value

    # Customer can reactivate zone
    act = client.post(f"{GEOFENCES}/{zone_id}/activate", headers=bearer(customer))
    assert act.status_code == 200
    assert act.get_json()["data"]["status"] == "ACTIVE"


def test_mobile_gps_tracking_flow(client, pg_app):
    """POST /gps/mobile ingests authorized mobile browser coordinates and marks source MOBILE_GPS."""
    ctx = _setup_context(client)
    customer = ctx["customer"]
    session_id = str(ctx["session_id"])

    now_iso = datetime.now(UTC).isoformat()
    resp = client.post(
        f"{GPS}/mobile",
        json={
            "custody_id": session_id,
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "accuracy_metres": 12.5,
            "speed": 0.0,
            "timestamp": now_iso,
            "device_id": "iphone-15-pro-safari",
        },
        headers=bearer(customer),
    )
    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert data["gps_event"]["source"] == TelemetrySource.MOBILE_GPS.value
    assert data["movement_status"] == MovementStatus.NORMAL.value

    # Verify live tracking view contains distinct phone_fix and vehicle_fix
    tracking = client.get(f"{GPS}/{session_id}", headers=bearer(customer))
    assert tracking.status_code == 200
    t_data = tracking.get_json()["data"]
    assert t_data["phone_fix"] is not None
    assert t_data["phone_fix"]["source"] == TelemetrySource.MOBILE_GPS.value
    assert "freshness" in t_data
    assert t_data["freshness"] == "LIVE"


def test_vehicle_gps_with_hardware_sensor_signals(client, pg_app):
    """POST /gps/vehicle records hardware sensor signals (tamper, power, shock, ignition)."""
    ctx = _setup_context(client)
    manager = ctx["manager"]
    session_id = str(ctx["session_id"])

    # Tracker tamper alarm triggers BREACH and security alert
    resp = client.post(
        f"{GPS}/vehicle",
        json={
            "custody_id": session_id,
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "accuracy_metres": 4.0,
            "speed": 0.0,
            "device_id": "teltonika-fmb920-101",
            "tamper_triggered": True,
            "power_connected": False,
            "shock_detected": True,
        },
        headers=bearer(manager),
    )
    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert data["movement_status"] == MovementStatus.BREACH.value
    alerts = data["alerts_raised"]
    assert len(alerts) > 0
    types = [a["alert_type"] for a in alerts]
    assert "DEVICE_TAMPER_ALARM" in types

    # Tracking view reflects real hardware sensors
    tracking = client.get(f"{GPS}/{session_id}", headers=bearer(manager))
    assert tracking.status_code == 200
    t_data = tracking.get_json()["data"]
    assert t_data["sensors"]["tamper_triggered"] is True
    assert t_data["sensors"]["power_connected"] is False


def test_alert_lifecycle_investigate_and_reopen(client, pg_app):
    """Alerts can be moved to INVESTIGATING and reopened after resolution."""
    ctx = _setup_context(client)
    manager = ctx["manager"]
    session_id = str(ctx["session_id"])

    # Trigger an alert by placing fix 500m outside the yard
    outside_lat = CENTRE[0] + 0.01  # ~1100m north
    resp = client.post(
        f"{GPS}/vehicle",
        json={
            "custody_id": session_id,
            "latitude": outside_lat,
            "longitude": CENTRE[1],
            "accuracy_metres": 5.0,
            "speed": 10.0,
            "device_id": "gps-tracker-99",
        },
        headers=bearer(manager),
    )
    assert resp.status_code == 201
    alert_id = resp.get_json()["data"]["alerts_raised"][0]["id"]

    # Manager acknowledges
    ack = client.post(f"{ALERTS}/{alert_id}/acknowledge", headers=bearer(manager))
    assert ack.status_code == 200
    assert ack.get_json()["data"]["status"] == AlertStatus.ACKNOWLEDGED.value

    # Manager marks as investigating
    inv = client.post(
        f"{ALERTS}/{alert_id}/investigate",
        json={"notes": "Security dispatched to north perimeter gate."},
        headers=bearer(manager),
    )
    assert inv.status_code == 200
    assert inv.get_json()["data"]["status"] == AlertStatus.INVESTIGATING.value

    # Manager resolves alert
    res = client.post(
        f"{ALERTS}/{alert_id}/resolve",
        json={"notes": "Authorized perimeter testing."},
        headers=bearer(manager),
    )
    assert res.status_code == 200
    assert res.get_json()["data"]["status"] == AlertStatus.RESOLVED.value

    # Reopening alert transitions it back to OPEN
    reopened = client.post(
        f"{ALERTS}/{alert_id}/reopen",
        json={"reason": "Secondary alarm tripped on same vehicle."},
        headers=bearer(manager),
    )
    assert reopened.status_code == 200
    assert reopened.get_json()["data"]["status"] == AlertStatus.REOPENED.value


def test_sse_stream_initial_snapshot(client, pg_app):
    """GET /gps/stream/<custody_id> returns text/event-stream with initial snapshot."""
    ctx = _setup_context(client)
    customer = ctx["customer"]
    session_id = str(ctx["session_id"])

    # Stream connection with bearer token in query parameter (EventSource mode)
    token = bearer(customer)["Authorization"].replace("Bearer ", "")
    response = client.get(
        f"{GPS}/stream/{session_id}?token={token}",
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    first_chunk = next(response.response)
    if isinstance(first_chunk, bytes):
        first_chunk = first_chunk.decode("utf-8")
    assert "event: snapshot" in first_chunk or "data:" in first_chunk
