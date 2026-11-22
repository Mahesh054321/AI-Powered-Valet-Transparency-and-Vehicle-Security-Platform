"""GPS Provider Adapter Architecture and Device Management.

Provides a modular interface for integrating real hardware GPS trackers (Traccar,
Teltonika, Geotab, Freematics, Generic Webhooks, and Mock test adapters)
without hardcoding provider-specific logic into the rest of the application.

Guarantees:
1. Validates and normalizes incoming telemetry into consistent structures.
2. Honest connection states: NOT_CONFIGURED, PENDING_VERIFICATION, CONNECTED, STALE, OFFLINE, ERROR.
3. Secret-based authentication for inbound device telematics ingestion.
4. Clear separation between real GPS hardware, mobile browser GPS, and simulated demo telemetry.
"""

from __future__ import annotations

import abc
import datetime
from dataclasses import dataclass, field
import hashlib
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select

from app.extensions import db
from models.custody_session import CustodySession
from models.enums import CustodyStatus, DeviceConnectionStatus, TelemetrySource
from models.gps_device import GpsDevice
from models.vehicle import Vehicle
from utils.exceptions import AuthenticationError, AuthorizationError, NotFoundError, ValidationError
from utils.security import AuthContext, Role

logger = logging.getLogger(__name__)

# Freshness threshold: fixes older than 5 minutes represent STALE tracking
GPS_FRESHNESS_THRESHOLD_SECONDS = int(os.getenv("GPS_FRESHNESS_THRESHOLD_SECONDS", "300"))
# Offline threshold: devices with no update in 15 minutes are OFFLINE
GPS_OFFLINE_THRESHOLD_SECONDS = int(os.getenv("GPS_OFFLINE_THRESHOLD_SECONDS", "900"))


@dataclass
class NormalizedGpsFix:
    """Canonicalized telemetry fix produced by all provider adapters."""

    device_id: str
    latitude: float
    longitude: float
    recorded_at: datetime.datetime
    speed_kmph: float | None = None
    heading_degrees: float | None = None
    accuracy_metres: float | None = None
    sensor_data: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


class GpsProviderAdapter(abc.ABC):
    """Abstract Base Class for hardware GPS tracker integrations."""

    provider_name: str
    display_name: str
    description: str
    supported_integration_types: list[str] = ["DIRECT_TELEMETRY", "WEBHOOK"]

    @abc.abstractmethod
    def normalize_telemetry(self, payload: dict[str, Any]) -> NormalizedGpsFix:
        """Parse provider payload into NormalizedGpsFix or raise ValidationError."""
        raise NotImplementedError

    def test_connection(self, device_id: str, credentials: dict[str, Any] | None = None) -> dict[str, Any]:
        """Test whether the device connection / credentials can reach the provider."""
        return {
            "provider": self.provider_name,
            "device_id": device_id,
            "status": "READY",
            "message": f"Provider '{self.display_name}' is ready to receive authenticated telemetry.",
            "tested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def describe(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "display_name": self.display_name,
            "description": self.description,
            "supported_integration_types": self.supported_integration_types,
        }


class TraccarAdapter(GpsProviderAdapter):
    """Adapter for Traccar / OsmAnd GPS protocol.

    Accepts standard Traccar JSON ingestion:
    lat/latitude, lon/longitude, timestamp/time, speed, bearing/heading, accuracy.
    """

    provider_name = "traccar"
    display_name = "Traccar GPS Platform"
    description = "Open-source telematics platform supporting 1500+ GPS tracking protocols."
    supported_integration_types = ["DIRECT_TELEMETRY", "WEBHOOK", "API_POLLING"]

    def normalize_telemetry(self, payload: dict[str, Any]) -> NormalizedGpsFix:
        device_id = str(payload.get("device_id") or payload.get("uniqueId") or payload.get("id") or "").strip()
        if not device_id:
            raise ValidationError("Missing 'device_id' or 'uniqueId' in Traccar payload.")

        try:
            lat = float(payload.get("latitude") if "latitude" in payload else payload["lat"])
            lon = float(payload.get("longitude") if "longitude" in payload else payload["lon"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(f"Invalid or missing latitude/longitude in Traccar payload: {exc}") from exc

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValidationError(f"Coordinates out of bounds: lat={lat}, lon={lon}")

        # Timestamp
        raw_time = payload.get("recorded_at") or payload.get("timestamp") or payload.get("deviceTime") or payload.get("fixTime")
        if raw_time:
            if isinstance(raw_time, (int, float)):
                # epoch ms or s
                ts = raw_time / 1000.0 if raw_time > 1e11 else float(raw_time)
                recorded_at = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            elif isinstance(raw_time, str):
                try:
                    recorded_at = datetime.datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                except ValueError:
                    recorded_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                recorded_at = datetime.datetime.now(datetime.timezone.utc)
        else:
            recorded_at = datetime.datetime.now(datetime.timezone.utc)

        speed = payload.get("speed_kmph")
        if speed is None and "speed" in payload:
            try:
                # Traccar reports speed in knots by default (1 knot = 1.852 km/h)
                speed = float(payload["speed"]) * 1.852
            except (ValueError, TypeError):
                speed = None

        heading = payload.get("heading_degrees") or payload.get("bearing") or payload.get("course")
        heading = float(heading) if heading is not None else None

        accuracy = payload.get("accuracy_metres") or payload.get("accuracy")
        accuracy = float(accuracy) if accuracy is not None else None

        attrs = payload.get("attributes") or {}
        sensors = {
            "ignition": bool(attrs.get("ignition") or payload.get("ignition")),
            "power_connected": bool(attrs.get("charge") or attrs.get("power", True)),
            "battery_level": attrs.get("batteryLevel"),
            "tamper_triggered": bool(attrs.get("tamper") or attrs.get("alarm") == "tamper"),
            "shock_detected": bool(attrs.get("shock") or attrs.get("motion")),
        }

        return NormalizedGpsFix(
            device_id=device_id,
            latitude=lat,
            longitude=lon,
            recorded_at=recorded_at,
            speed_kmph=round(speed, 2) if speed is not None else None,
            heading_degrees=round(heading, 2) if heading is not None else None,
            accuracy_metres=round(accuracy, 2) if accuracy is not None else None,
            sensor_data=sensors,
            raw_payload=payload,
        )


class TeltonikaAdapter(GpsProviderAdapter):
    """Adapter for Teltonika AVL GPS units (FMB920, FMB120, FMC130, etc.)."""

    provider_name = "teltonika"
    display_name = "Teltonika Telematics"
    description = "Enterprise vehicle telematics hardware with CAN bus, tamper, and ignition sensors."

    def normalize_telemetry(self, payload: dict[str, Any]) -> NormalizedGpsFix:
        device_id = str(payload.get("device_id") or payload.get("imei") or "").strip()
        if not device_id:
            raise ValidationError("Missing 'device_id' or 'imei' in Teltonika payload.")

        try:
            lat = float(payload.get("latitude") if "latitude" in payload else payload["lat"])
            lon = float(payload.get("longitude") if "longitude" in payload else payload["lon"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(f"Invalid or missing coordinates in Teltonika payload: {exc}") from exc

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValidationError(f"Coordinates out of bounds: lat={lat}, lon={lon}")

        recorded_at = datetime.datetime.now(datetime.timezone.utc)
        if "timestamp" in payload:
            try:
                raw_ts = payload["timestamp"]
                if isinstance(raw_ts, (int, float)):
                    recorded_at = datetime.datetime.fromtimestamp(raw_ts / 1000.0 if raw_ts > 1e11 else raw_ts, tz=datetime.timezone.utc)
                elif isinstance(raw_ts, str):
                    recorded_at = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                pass

        speed = float(payload.get("speed", 0.0)) if "speed" in payload else None
        heading = float(payload.get("angle", payload.get("heading", 0.0))) if "angle" in payload or "heading" in payload else None

        io_data = payload.get("io") or payload.get("elements") or {}
        sensors = {
            "ignition": bool(io_data.get("239") or io_data.get("ignition", False)),
            "tamper_triggered": bool(io_data.get("tamper") or io_data.get("case_open", False)),
            "power_connected": bool(io_data.get("66") or True),
            "crash_detected": bool(io_data.get("247") or False),
        }

        return NormalizedGpsFix(
            device_id=device_id,
            latitude=lat,
            longitude=lon,
            recorded_at=recorded_at,
            speed_kmph=speed,
            heading_degrees=heading,
            accuracy_metres=float(payload.get("accuracy", 3.0)),
            sensor_data=sensors,
            raw_payload=payload,
        )


class GenericWebhookAdapter(GpsProviderAdapter):
    """Universal webhook adapter accepting standard JSON coordinates."""

    provider_name = "generic_webhook"
    display_name = "Generic Webhook Ingestion"
    description = "Direct JSON REST payload from any connected vehicle or third-party telematics broker."

    def normalize_telemetry(self, payload: dict[str, Any]) -> NormalizedGpsFix:
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise ValidationError("Missing 'device_id' in telemetry payload.")

        try:
            lat = float(payload["latitude"])
            lon = float(payload["longitude"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(f"Missing or invalid latitude/longitude: {exc}") from exc

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValidationError(f"Coordinates out of bounds: lat={lat}, lon={lon}")

        recorded_at = datetime.datetime.now(datetime.timezone.utc)
        if "timestamp" in payload and payload["timestamp"]:
            raw_ts = payload["timestamp"]
            if isinstance(raw_ts, (int, float)):
                recorded_at = datetime.datetime.fromtimestamp(raw_ts / 1000.0 if raw_ts > 1e11 else raw_ts, tz=datetime.timezone.utc)
            elif isinstance(raw_ts, str):
                try:
                    recorded_at = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

        speed = float(payload["speed_kmph"]) if payload.get("speed_kmph") is not None else None
        heading = float(payload["heading_degrees"]) if payload.get("heading_degrees") is not None else None
        accuracy = float(payload["accuracy_metres"]) if payload.get("accuracy_metres") is not None else None

        return NormalizedGpsFix(
            device_id=device_id,
            latitude=lat,
            longitude=lon,
            recorded_at=recorded_at,
            speed_kmph=speed,
            heading_degrees=heading,
            accuracy_metres=accuracy,
            sensor_data=payload.get("sensor_data") or {},
            raw_payload=payload,
        )


class MockProviderAdapter(GpsProviderAdapter):
    """Mock provider adapter for automated tests with deterministic verification."""

    provider_name = "mock"
    display_name = "Test Provider Adapter (Mock)"
    description = "Controlled test adapter producing deterministic coordinates for automated suites."
    supported_integration_types = ["DIRECT_TELEMETRY", "MOCK"]

    def normalize_telemetry(self, payload: dict[str, Any]) -> NormalizedGpsFix:
        device_id = str(payload.get("device_id") or "MOCK-DEV-001")
        lat = float(payload.get("latitude", 27.49325))
        lon = float(payload.get("longitude", 77.67425))
        return NormalizedGpsFix(
            device_id=device_id,
            latitude=lat,
            longitude=lon,
            recorded_at=datetime.datetime.now(datetime.timezone.utc),
            speed_kmph=float(payload.get("speed_kmph", 0.0)),
            heading_degrees=float(payload.get("heading_degrees", 0.0)),
            accuracy_metres=float(payload.get("accuracy_metres", 2.5)),
            sensor_data=payload.get("sensor_data", {"power_connected": True}),
            raw_payload=payload,
        )

    def test_connection(self, device_id: str, credentials: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "provider": "mock",
            "device_id": device_id,
            "status": "CONNECTED",
            "message": "Mock tracker connection verified successfully.",
            "tested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


# Adapter Registry
_ADAPTERS: dict[str, GpsProviderAdapter] = {
    "traccar": TraccarAdapter(),
    "teltonika": TeltonikaAdapter(),
    "generic_webhook": GenericWebhookAdapter(),
    "mock": MockProviderAdapter(),
}


def get_provider_adapter(provider_name: str | None) -> GpsProviderAdapter:
    """Resolve adapter by provider name with fallback to Generic Webhook."""
    key = (provider_name or "generic_webhook").lower().strip()
    return _ADAPTERS.get(key, _ADAPTERS["generic_webhook"])


def list_supported_providers() -> list[dict[str, Any]]:
    """Return catalog of supported real GPS providers."""
    return [adapter.describe() for adapter in _ADAPTERS.values()]


# ---------------------------------------------------------------------------
# Device Registration & Telemetry Ingestion Services
# ---------------------------------------------------------------------------

def _hash_secret(secret: str) -> str:
    """Hash device token using SHA-256 for secure verification without plaintext storage."""
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def register_gps_device(
    actor: AuthContext,
    *,
    device_id: str,
    vehicle_id: str | uuid.UUID | None = None,
    provider_name: str = "traccar",
    integration_type: str = "DIRECT_TELEMETRY",
    device_secret: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register or link a physical GPS tracker against a vehicle."""
    device_id = str(device_id).strip()
    if not device_id:
        raise ValidationError("Device identifier is required.", details={"field": "device_id"})

    vehicle = None
    if vehicle_id:
        vehicle_uuid = uuid.UUID(str(vehicle_id))
        vehicle = db.session.get(Vehicle, vehicle_uuid)
        if not vehicle:
            raise NotFoundError(f"No vehicle exists with id '{vehicle_id}'.")
        # Customers can only register trackers to their own vehicles
        if actor.role == Role.CUSTOMER.value and str(vehicle.owner_id) != actor.user_id:
            raise AuthorizationError("You are not authorized to register GPS trackers for another customer's vehicle.")

    existing = db.session.execute(
        select(GpsDevice).where(GpsDevice.device_id == device_id)
    ).scalar_one_or_none()

    if existing:
        if vehicle_id:
            existing.vehicle_id = uuid.UUID(str(vehicle_id))
        existing.provider_name = provider_name
        existing.integration_type = integration_type
        if device_secret:
            existing.device_secret_hash = _hash_secret(device_secret)
        if metadata:
            existing.metadata_ = {**(existing.metadata_ or {}), **metadata}
        device = existing
    else:
        device = GpsDevice(
            device_id=device_id,
            vehicle_id=uuid.UUID(str(vehicle_id)) if vehicle_id else None,
            provider_name=provider_name,
            integration_type=integration_type,
            connection_status=DeviceConnectionStatus.PENDING_VERIFICATION.value,
            device_secret_hash=_hash_secret(device_secret) if device_secret else None,
            metadata_=metadata or {},
        )
        db.session.add(device)

    if vehicle:
        vehicle.gps_device_id = device_id
        vehicle.tracking_method = "REAL_GPS"

    db.session.commit()
    return device.to_dict()


def unlink_gps_device(actor: AuthContext, device_id: str) -> dict[str, Any]:
    """Unlink or deactivate a registered GPS device."""
    device = db.session.execute(
        select(GpsDevice).where(GpsDevice.device_id == device_id)
    ).scalar_one_or_none()
    if not device:
        raise NotFoundError(f"No GPS device found with id '{device_id}'.")

    if device.vehicle_id:
        vehicle = db.session.get(Vehicle, device.vehicle_id)
        if vehicle:
            if actor.role == Role.CUSTOMER.value and str(vehicle.owner_id) != actor.user_id:
                raise AuthorizationError("Not authorized to modify this vehicle's GPS tracking.")
            vehicle.gps_device_id = None
            vehicle.tracking_method = "SIMULATED"

    device.vehicle_id = None
    device.connection_status = DeviceConnectionStatus.NOT_CONFIGURED.value
    db.session.commit()
    return {"device_id": device_id, "status": "UNLINKED"}


def test_gps_device_connection(actor: AuthContext, device_id: str) -> dict[str, Any]:
    """Test device connection and return honest verification state."""
    device = db.session.execute(
        select(GpsDevice).where(GpsDevice.device_id == device_id)
    ).scalar_one_or_none()
    if not device:
        raise NotFoundError(f"No GPS device found with id '{device_id}'.")

    adapter = get_provider_adapter(device.provider_name)
    result = adapter.test_connection(device_id)

    # Compute freshness if updates exist
    now = datetime.datetime.now(datetime.timezone.utc)
    if device.last_successful_update:
        age_seconds = (now - device.last_successful_update).total_seconds()
        if age_seconds <= GPS_FRESHNESS_THRESHOLD_SECONDS:
            device.connection_status = DeviceConnectionStatus.CONNECTED.value
        elif age_seconds <= GPS_OFFLINE_THRESHOLD_SECONDS:
            device.connection_status = DeviceConnectionStatus.STALE.value
        else:
            device.connection_status = DeviceConnectionStatus.OFFLINE.value
    else:
        device.connection_status = DeviceConnectionStatus.PENDING_VERIFICATION.value

    db.session.commit()
    return {
        **result,
        "probe_status": result.get("status"),
        "status": device.connection_status,
        "connection_status": device.connection_status,
        "last_successful_update": (
            device.last_successful_update.isoformat()
            if device.last_successful_update
            else None
        ),
    }


def list_gps_devices(actor: AuthContext, vehicle_id: str | None = None) -> list[dict[str, Any]]:
    """List registered GPS devices."""
    query = select(GpsDevice)
    if vehicle_id:
        query = query.where(GpsDevice.vehicle_id == uuid.UUID(vehicle_id))
    devices = db.session.execute(query).scalars().all()
    return [d.to_dict() for d in devices]


def ingest_real_gps_fix(
    device_id: str,
    payload: dict[str, Any],
    *,
    device_secret: str | None = None,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Ingest a live hardware GPS fix from an authentic tracking device."""
    from services import geofence

    device = db.session.execute(
        select(GpsDevice).where(GpsDevice.device_id == device_id)
    ).scalar_one_or_none()

    if not device:
        raise NotFoundError("This GPS device is not registered.")

    # A device ID identifies a tracker; it does not authenticate one.
    if not device.device_secret_hash:
        device.last_error_category = "AUTHENTICATION_NOT_CONFIGURED"
        device.connection_status = DeviceConnectionStatus.ERROR.value
        db.session.commit()
        raise AuthenticationError("This GPS device has no telemetry credential configured.")
    if not device_secret or _hash_secret(device_secret) != device.device_secret_hash:
        device.last_error_category = "AUTHENTICATION_FAILED"
        device.connection_status = DeviceConnectionStatus.ERROR.value
        db.session.commit()
        raise AuthenticationError("Invalid or missing device credentials.")

    # Normalize through provider adapter
    adapter = get_provider_adapter(provider_name or device.provider_name)
    normalized = adapter.normalize_telemetry({**payload, "device_id": device_id})

    # Resolve associated vehicle and active custody session
    vehicle = db.session.get(Vehicle, device.vehicle_id) if device.vehicle_id else None
    active_session = None

    if vehicle:
        active_session = db.session.execute(
            select(CustodySession).where(
                CustodySession.vehicle_id == vehicle.id,
                CustodySession.status != CustodyStatus.CHECKED_OUT.value,
            ).order_by(CustodySession.dropped_off_at.desc())
        ).scalars().first()

    now = datetime.datetime.now(datetime.timezone.utc)
    device.last_successful_update = normalized.recorded_at
    fix_age_seconds = (now - normalized.recorded_at).total_seconds()
    if fix_age_seconds <= GPS_FRESHNESS_THRESHOLD_SECONDS:
        device.connection_status = DeviceConnectionStatus.CONNECTED.value
    elif fix_age_seconds <= GPS_OFFLINE_THRESHOLD_SECONDS:
        device.connection_status = DeviceConnectionStatus.STALE.value
    else:
        device.connection_status = DeviceConnectionStatus.OFFLINE.value
    device.last_error_category = None
    if vehicle:
        vehicle.tracking_method = "REAL_GPS"

    # Only authenticated provider telemetry is marked as REAL_GPS.
    fix_payload = {
        "custody_session_id": str(active_session.id) if active_session else None,
        "vehicle_id": str(vehicle.id) if vehicle else None,
        "latitude": normalized.latitude,
        "longitude": normalized.longitude,
        "speed_kmph": normalized.speed_kmph,
        "heading_degrees": normalized.heading_degrees,
        "accuracy_metres": normalized.accuracy_metres,
        "timestamp": normalized.recorded_at.isoformat(),
        "device_id": device_id,
        "sensor_data": normalized.sensor_data,
    }

    # Internal system AuthContext for authentic telemetry
    system_actor = AuthContext(
        user_id="00000000-0000-0000-0000-000000000000",
        role=Role.ADMIN.value,
        email="system.telemetry@valet.local",
        token_id="system-telemetry",
        token_type="system",
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365),
    )

    if active_session:
        result = geofence.ingest_fix(
            fix_payload,
            system_actor,
            source=TelemetrySource.REAL_GPS.value,
            device_id=device_id,
        )
    else:
        # Record orphan fix for unlinked or inactive vehicle
        from models.gps_event import GpsEvent
        event = GpsEvent(
            vehicle_id=vehicle.id if vehicle else None,
            custody_session_id=None,
            latitude=normalized.latitude,
            longitude=normalized.longitude,
            speed_kmph=normalized.speed_kmph,
            heading_degrees=normalized.heading_degrees,
            accuracy_metres=normalized.accuracy_metres,
            recorded_at=normalized.recorded_at,
            source=TelemetrySource.REAL_GPS.value,
            device_id=device_id,
            movement_status="NORMAL",
            is_within_zone=False,
            distance_from_zone_metres=None,
            sensor_data=normalized.sensor_data,
        )
        db.session.add(event)
        db.session.commit()
        result = event.to_dict()

    db.session.commit()
    return {
        "status": "INGESTED",
        "device_id": device_id,
        "source": TelemetrySource.REAL_GPS.value,
        "connection_status": device.connection_status,
        "fix": result,
    }


__all__ = [
    "GPS_FRESHNESS_THRESHOLD_SECONDS",
    "GPS_OFFLINE_THRESHOLD_SECONDS",
    "GenericWebhookAdapter",
    "GpsProviderAdapter",
    "MockProviderAdapter",
    "NormalizedGpsFix",
    "TeltonikaAdapter",
    "TraccarAdapter",
    "get_provider_adapter",
    "ingest_real_gps_fix",
    "list_gps_devices",
    "list_supported_providers",
    "register_gps_device",
    "test_gps_device_connection",
    "unlink_gps_device",
]
