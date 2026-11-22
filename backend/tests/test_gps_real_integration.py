"""Tests for real GPS tracker registration, provider adapters, and email test delivery."""

from __future__ import annotations

import uuid
import pytest

from app.extensions import db
from models.enums import DeviceConnectionStatus, TelemetrySource, VehicleStatus
from models.gps_device import GpsDevice
from models.vehicle import Vehicle
from services import gps_provider
from tests.workflow_helpers import bearer, make_user, make_vehicle, unique
from utils.security import AuthContext, Role

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _clean(pg_app):
    yield
    db.session.rollback()


def test_register_gps_device_and_link_to_vehicle(pg_client):
    manager = make_user(Role.VALET_MANAGER)
    customer = make_user(Role.CUSTOMER)
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    device_id = f"TELTONIKA-{unique('DEV')[:8]}"

    # Register device
    res = pg_client.post(
        "/api/v1/gps/devices",
        json={
            "device_id": device_id,
            "vehicle_id": str(vehicle.id),
            "provider_name": "teltonika",
            "integration_type": "DIRECT_TELEMETRY",
            "device_secret": "secure-tracker-token",
        },
        headers=bearer(manager),
    )
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["device_id"] == device_id
    assert data["provider_name"] == "teltonika"

    # Verify vehicle was updated
    db.session.refresh(vehicle)
    assert vehicle.gps_device_id == device_id
    assert vehicle.tracking_method == "REAL_GPS"


def test_customer_cannot_link_device_to_another_vehicle(pg_client):
    customer1 = make_user(Role.CUSTOMER)
    customer2 = make_user(Role.CUSTOMER)
    vehicle = make_vehicle(customer1.id)
    db.session.commit()

    # Customer 2 attempts to register device for Customer 1's vehicle
    res = pg_client.post(
        "/api/v1/gps/devices",
        json={
            "device_id": f"ROGUE-{unique('DEV')[:8]}",
            "vehicle_id": str(vehicle.id),
            "provider_name": "generic_webhook",
        },
        headers=bearer(customer2),
    )
    assert res.status_code in (401, 403), res.get_json()


def test_test_device_connection(pg_client):
    manager = make_user(Role.VALET_MANAGER)
    device_id = f"MOCK-{unique('DEV')[:8]}"
    device = GpsDevice(
        device_id=device_id,
        provider_name="mock",
        connection_status=DeviceConnectionStatus.NOT_CONFIGURED.value,
    )
    db.session.add(device)
    db.session.commit()

    res = pg_client.get(
        f"/api/v1/gps/devices/{device.device_id}/test",
        headers=bearer(manager),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["device_id"] == device_id
    assert data["status"] == DeviceConnectionStatus.PENDING_VERIFICATION.value
    assert data["probe_status"] == "CONNECTED"


def test_ingest_real_gps_telemetry(pg_client):
    admin = make_user(Role.ADMIN)
    db.session.commit()
    device_id = f"WEBHOOK-{unique('DEV')[:8]}"
    secret = "secret-pass-123"

    # Register device with secret via API
    reg_res = pg_client.post(
        "/api/v1/gps/devices",
        json={
            "device_id": device_id,
            "provider_name": "generic_webhook",
            "device_secret": secret,
        },
        headers=bearer(admin),
    )
    assert reg_res.status_code == 201, reg_res.get_json()

    # Ingest fix
    res = pg_client.post(
        f"/api/v1/gps/devices/{device_id}/telemetry",
        json={
            "latitude": 27.49350,
            "longitude": 77.67480,
            "speed_kmph": 24.5,
            "heading_degrees": 92.0,
            "accuracy_metres": 3.8,
            "device_secret": secret,
        },
    )
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["status"] == "INGESTED"
    assert data["source"] == TelemetrySource.REAL_GPS.value
    assert data["connection_status"] == DeviceConnectionStatus.CONNECTED.value


def test_invalid_coordinates_rejected_by_adapter(pg_client):
    admin = make_user(Role.ADMIN)
    device_id = f"INVALID-{unique('DEV')[:8]}"
    secret = "invalid-coordinate-test-secret"
    registration = pg_client.post(
        "/api/v1/gps/devices",
        json={
            "device_id": device_id,
            "provider_name": "generic_webhook",
            "device_secret": secret,
        },
        headers=bearer(admin),
    )
    assert registration.status_code == 201, registration.get_json()

    res = pg_client.post(
        f"/api/v1/gps/devices/{device_id}/telemetry",
        json={
            "latitude": 195.0,  # Invalid!
            "longitude": 77.67480,
        },
        headers={"X-Device-Key": secret},
    )
    assert res.status_code == 422, res.get_json()


def test_email_test_endpoint_authorized(pg_client, monkeypatch):
    manager = make_user(Role.VALET_MANAGER)
    customer = make_user(Role.CUSTOMER)
    db.session.commit()

    # Customer should be forbidden
    res = pg_client.post(
        "/api/v1/alerts/test-email",
        json={"email": "test@example.com"},
        headers=bearer(customer),
    )
    assert res.status_code in (401, 403)

    # Manager allowed (mock mode)
    monkeypatch.setenv("EMAIL_MOCK_DISPATCH", "true")
    res = pg_client.post(
        "/api/v1/alerts/test-email",
        json={"email": "manager.verify@example.com"},
        headers=bearer(manager),
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["status"] == "DELIVERED"
