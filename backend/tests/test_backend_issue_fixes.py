"""Tests verifying resolutions for Issues A, B, C, and D.

Issue A: Alert resolution atomicity, reopening, idempotency, and rollback.
Issue B: Retrieval prediction resolution by ticket ID/custody ID and baseline handling.
Issue C: Custody session PATCH idempotency and lifecycle transition flexibility.
Issue D: Vehicle archiving dependency validation and resource cleanup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.extensions import db
from models.alert import Alert, AlertCategory, AlertStatus as FeedStatus
from models.custody_session import CustodySession
from models.enums import (
    AlertSeverity,
    AlertStatus as SecurityStatus,
    AlertType,
    CustodyStatus,
    TicketStatus,
    VehicleStatus,
)
from models.gps_device import GpsDevice
from models.parking_zone import ParkingZone
from models.security_alert import SecurityAlert
from models.valet_ticket import ValetTicket
from models.vehicle import Vehicle
from services.alerts import raise_from_security_alert
from tests.workflow_helpers import (
    bearer,
    make_user,
    make_vehicle,
    make_zone,
)
from utils.security import Role

pytestmark = pytest.mark.postgres

FEED = "/api/v1/feed"
RETRIEVAL = "/api/v1/retrieval"
SESSIONS = "/api/v1/custody-sessions"
TICKETS = "/api/v1/tickets"
VEHICLES = "/api/v1/vehicles"


# ---------------------------------------------------------------------------
# Issue A: Alert resolution atomicity, reopening, and duplicate prevention
# ---------------------------------------------------------------------------
def test_issue_a_resolve_alert_from_security_alerts_atomic_sync(client, pg_app):
    """Resolving a feed alert originating from security_alerts atomically syncs

    acknowledged_at, acknowledged_by_id, resolved_at, resolved_by_id, satisfying
    ck_security_alerts_terminal_requires_resolution_time and
    ck_security_alerts_resolve_after_ack.
    """
    manager = make_user(Role.VALET_MANAGER)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    # Create custody session
    custody = CustodySession(
        ticket_id=uuid.uuid4(),
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        received_by_id=manager.id,
        status=str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    # Create ticket linked to custody
    ticket = ValetTicket(
        id=custody.ticket_id,
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        ticket_code="VT-202610-TEST01",
        status=str(TicketStatus.ACTIVE),
    )
    db.session.add(ticket)
    db.session.add(custody)
    db.session.commit()

    # Create unacknowledged SecurityAlert
    source = SecurityAlert(
        custody_session_id=custody.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        alert_type=AlertType.GEOFENCE_BREACH.value,
        severity=AlertSeverity.HIGH.value,
        status=SecurityStatus.OPEN.value,
        title="Vehicle left assigned zone",
        reason="Boundary breach detected by smart geofence.",
        confidence=0.95,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    db.session.add(source)
    db.session.commit()

    # Project to Alert feed
    feed_alert, was_created = raise_from_security_alert(source)
    assert was_created is True
    db.session.commit()

    # Resolve via POST /api/v1/feed/<alert_id>/resolve
    res = client.post(
        f"{FEED}/{feed_alert.id}/resolve",
        json={"notes": "Vehicle safely returned to bay."},
        headers=bearer(manager),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["status"] == "RESOLVED"

    # Verify atomic synchronisation on SecurityAlert source row
    db.session.refresh(source)
    assert source.status == SecurityStatus.RESOLVED.value
    assert source.resolved_at is not None
    assert source.resolved_by_id == manager.id
    assert source.acknowledged_at is not None
    assert source.acknowledged_by_id == manager.id
    assert source.resolved_at >= source.acknowledged_at
    assert source.resolution_notes == "Vehicle safely returned to bay."


def test_issue_a_reopen_alert_clears_resolution_fields(client, pg_app):
    """Reopening an alert clears resolution fields on both feed and source rows."""
    manager = make_user(Role.VALET_MANAGER)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    custody = CustodySession(
        ticket_id=uuid.uuid4(),
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        received_by_id=manager.id,
        status=str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    ticket = ValetTicket(
        id=custody.ticket_id,
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        ticket_code="VT-202610-TEST02",
        status=str(TicketStatus.ACTIVE),
    )
    db.session.add(ticket)
    db.session.add(custody)
    db.session.commit()

    source = SecurityAlert(
        custody_session_id=custody.id,
        vehicle_id=vehicle.id,
        alert_type=AlertType.TAMPER_DETECTED.value,
        severity=AlertSeverity.CRITICAL.value,
        status=SecurityStatus.OPEN.value,
        title="Hardware tamper sensor alert",
        reason="Anti-tamper circuit tripped.",
        confidence=0.99,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db.session.add(source)
    db.session.commit()

    feed_alert, _ = raise_from_security_alert(source)
    db.session.commit()

    # Resolve first
    client.post(
        f"{FEED}/{feed_alert.id}/resolve",
        json={"notes": "Sensor false positive."},
        headers=bearer(manager),
    )

    # Reopen via POST /api/v1/alerts/<alert_id>/reopen
    reopen_res = client.post(
        f"/api/v1/alerts/{source.id}/reopen",
        json={"reason": "Secondary breach detected; requires re-investigation."},
        headers=bearer(manager),
    )
    assert reopen_res.status_code == 200, reopen_res.get_json()
    reopened_data = reopen_res.get_json()["data"]
    assert reopened_data["status"] == "REOPENED"
    assert reopened_data["resolved_at"] is None
    assert reopened_data["resolved_by_id"] is None

    # Check underlying SecurityAlert is reopened and fields cleared
    db.session.refresh(source)
    assert source.status == SecurityStatus.REOPENED.value
    assert source.resolved_at is None
    assert source.resolved_by_id is None
    assert "[Reopened]" in source.resolution_notes


def test_issue_a_repeated_resolution_returns_409(client, pg_app):
    """Repeated resolution returns 409 Conflict with meaningful message and rolls back."""
    manager = make_user(Role.VALET_MANAGER)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    custody = CustodySession(
        ticket_id=uuid.uuid4(),
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        received_by_id=manager.id,
        status=str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    ticket = ValetTicket(
        id=custody.ticket_id,
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        ticket_code="VT-202610-TEST03",
        status=str(TicketStatus.ACTIVE),
    )
    db.session.add(ticket)
    db.session.add(custody)
    db.session.commit()

    source = SecurityAlert(
        custody_session_id=custody.id,
        vehicle_id=vehicle.id,
        alert_type=AlertType.UNUSUAL_SPEED.value,
        severity=AlertSeverity.MEDIUM.value,
        status=SecurityStatus.OPEN.value,
        title="Vehicle speed exceeded threshold",
        reason="GPS telemetry recorded speed > zone limit.",
        confidence=0.88,
    )
    db.session.add(source)
    db.session.commit()

    feed_alert, _ = raise_from_security_alert(source)
    db.session.commit()

    # First resolution: 200 OK
    res1 = client.post(
        f"{FEED}/{feed_alert.id}/resolve",
        json={"notes": "Checked speed logs."},
        headers=bearer(manager),
    )
    assert res1.status_code == 200

    # Second resolution: 409 Conflict
    res2 = client.post(
        f"{FEED}/{feed_alert.id}/resolve",
        json={"notes": "Duplicate resolution attempt."},
        headers=bearer(manager),
    )
    assert res2.status_code == 409
    err = res2.get_json()["error"]
    assert "already resolved" in err["message"].lower()


# ---------------------------------------------------------------------------
# Issue B: Retrieval prediction resolution by ticket ID / custody ID
# ---------------------------------------------------------------------------
def test_issue_b_retrieval_lookup_by_ticket_id(client, pg_app):
    """GET /api/v1/retrieval/<ticket_id> resolves the ticket to its custody session."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    # Issue ticket and check in
    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    checked_in = client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone.id)},
        headers=bearer(operator),
    )
    assert checked_in.status_code == 200
    custody_id = checked_in.get_json()["data"]["ticket"]["custody_session"]["id"]

    # Before prediction, calling with ticket_id returns 404 with guidance to predict
    get_before = client.get(f"{RETRIEVAL}/{ticket_id}", headers=bearer(customer))
    assert get_before.status_code == 404
    assert "predict" in get_before.get_json()["error"]["message"]

    # Request prediction using ticket_id
    pred_res = client.post(
        f"{RETRIEVAL}/predict",
        json={"ticket_id": ticket_id},
        headers=bearer(customer),
    )
    assert pred_res.status_code in (200, 201), pred_res.get_json()
    pred_data = pred_res.get_json()["data"]
    assert pred_data["estimated_minutes"] is not None

    # Now read back by ticket_id: returns 200 with stored estimate
    read_by_ticket = client.get(f"{RETRIEVAL}/{ticket_id}", headers=bearer(customer))
    assert read_by_ticket.status_code == 200
    assert read_by_ticket.get_json()["data"]["estimated_minutes"] == pred_data["estimated_minutes"]

    # Also read back by custody_id: matches exactly
    read_by_custody = client.get(f"{RETRIEVAL}/{custody_id}", headers=bearer(customer))
    assert read_by_custody.status_code == 200
    assert (
        read_by_custody.get_json()["data"]["estimated_minutes"]
        == pred_data["estimated_minutes"]
    )


# ---------------------------------------------------------------------------
# Issue C: Custody session PATCH idempotency and lifecycle flexibility
# ---------------------------------------------------------------------------
def test_issue_c_custody_patch_identical_status_is_idempotent(client, pg_app):
    """PATCH with existing status returns 200 OK idempotently rather than 409 Conflict."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    ticket_id = issued.get_json()["data"]["ticket"]["id"]
    client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone.id)},
        headers=bearer(operator),
    )
    ticket_data = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(operator)).get_json()["data"]["ticket"]
    session_id = ticket_data["custody_session"]["id"]

    # Session starts at PARKED after check-in.
    # PATCH with PARKED again (idempotent duplicate request)
    repeat_patch = client.patch(
        f"{SESSIONS}/{session_id}",
        json={"status": "PARKED"},
        headers=bearer(operator),
    )
    assert repeat_patch.status_code == 200, repeat_patch.get_json()
    assert repeat_patch.get_json()["data"]["custody_session"]["status"] == "PARKED"


def test_issue_c_custody_transitions_retrieval_requested_to_retrieval_in_progress(client, pg_app):
    """A valet must start the retrieval before staging the vehicle."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    ticket_id = issued.get_json()["data"]["ticket"]["id"]
    client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone.id)},
        headers=bearer(operator),
    )
    ticket_data = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(operator)).get_json()["data"]["ticket"]
    session_id = ticket_data["custody_session"]["id"]

    # Customer requests retrieval
    req_res = client.patch(
        f"{SESSIONS}/{session_id}",
        json={"status": "RETRIEVAL_REQUESTED"},
        headers=bearer(customer),
    )
    assert req_res.status_code == 200

    # Valet starts the explicit retrieval operation.
    move_res = client.patch(
        f"{SESSIONS}/{session_id}",
        json={"status": "RETRIEVAL_IN_PROGRESS"},
        headers=bearer(operator),
    )
    assert move_res.status_code == 200
    assert move_res.get_json()["data"]["custody_session"]["status"] == "RETRIEVAL_IN_PROGRESS"


# ---------------------------------------------------------------------------
# Issue D: Vehicle deletion / archiving dependency handling
# ---------------------------------------------------------------------------
def test_issue_d_vehicle_archiving_blocked_by_open_custody(client, pg_app):
    """Archiving a vehicle while in open custody is refused with 409 and session details."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA8001")
    zone = make_zone()
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    ticket_id = issued.get_json()["data"]["ticket"]["id"]
    client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone.id)},
        headers=bearer(operator),
    )

    # Attempt to archive vehicle
    del_res = client.delete(f"{VEHICLES}/{vehicle.id}", headers=bearer(customer))
    assert del_res.status_code == 409
    err = del_res.get_json()["error"]
    assert "currently in custody" in err["message"]
    assert "custody_session_id" in err["details"]


def test_issue_d_vehicle_archiving_blocked_by_active_ticket(client, pg_app):
    """Archiving a vehicle with an active issued ticket is refused with 409."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA8002")
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201

    # Attempt to archive vehicle before check-in while ticket is open
    del_res = client.delete(f"{VEHICLES}/{vehicle.id}", headers=bearer(customer))
    assert del_res.status_code == 409
    assert "active ticket" in del_res.get_json()["error"]["message"]


def test_issue_d_vehicle_archiving_unlinks_gps_device_and_frees_resources(client, pg_app):
    """Archiving an idle vehicle soft-deletes and unlinks any paired GPS tracker device."""
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA8003")
    db.session.commit()

    # Link a GPS device
    device = GpsDevice(
        device_id="TR-8003-TEST",
        vehicle_id=vehicle.id,
        provider_name="traccar",
        connection_status="CONNECTED",
    )
    db.session.add(device)
    vehicle.gps_device_id = "TR-8003-TEST"
    db.session.commit()

    # Archive vehicle
    del_res = client.delete(f"{VEHICLES}/{vehicle.id}", headers=bearer(customer))
    assert del_res.status_code == 204

    # Verify vehicle status is ARCHIVED
    db.session.refresh(vehicle)
    assert vehicle.status == VehicleStatus.ARCHIVED.value

    # Verify device was unlinked
    db.session.refresh(device)
    assert device.vehicle_id is None
    assert device.connection_status == "NOT_CONFIGURED"
