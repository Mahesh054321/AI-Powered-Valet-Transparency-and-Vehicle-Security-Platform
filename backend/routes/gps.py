"""GPS telemetry endpoints.

    POST /gps/events                staff - record one fix, or a batch
    POST /gps/mobile                customer or staff - record real mobile browser GPS
    POST /gps/vehicle               staff or tracker - record physical tracker telemetry
    GET  /gps/<custody_id>          owner or staff - the live tracking view
    GET  /gps/<custody_id>/fixes    owner or staff - paginated raw fixes
    GET  /gps/stream/<custody_id>   owner or staff - live real-time SSE stream
    GET  /gps/simulate/scenarios    staff - what the simulator can generate
    POST /gps/simulate              staff - generate and ingest a simulated track

Mounted at both ``/api/v1/gps`` and ``/api/gps`` -- see ``routes/__init__.py``.
"""

from __future__ import annotations

import logging
from queue import Empty

from flask import Blueprint, Response, request, stream_with_context

from models.enums import TelemetrySource
from services import geofence, gps_provider
from utils.exceptions import ValidationError
from utils.pagination import parse_pagination
from utils.requests import json_payload
from utils.responses import created, ok
from utils.security import Role, current_user, require_authentication, require_role
from utils.validation import validate_uuid

logger = logging.getLogger(__name__)


def create_gps_blueprint() -> Blueprint:
    """Build a fresh telemetry blueprint.

    Fresh per call because Flask refuses to register one blueprint object
    twice, and this is mounted at two prefixes.
    """
    blueprint = Blueprint("gps", __name__)

    @blueprint.get("/providers")
    @require_authentication
    def list_providers():
        """List supported physical GPS tracker adapters."""
        return ok({"providers": gps_provider.list_supported_providers()})

    @blueprint.get("/devices")
    @require_authentication
    def list_devices():
        """List registered GPS devices."""
        vehicle_id = request.args.get("vehicle_id")
        return ok({"devices": gps_provider.list_gps_devices(current_user(), vehicle_id=vehicle_id)})

    @blueprint.post("/devices")
    @require_authentication
    def register_device():
        """Register or link a GPS device to a vehicle."""
        payload = json_payload()
        device_id = payload.get("device_id")
        if not device_id:
            raise ValidationError("device_id is required.", details={"field": "device_id"})
        result = gps_provider.register_gps_device(
            current_user(),
            device_id=str(device_id),
            vehicle_id=payload.get("vehicle_id"),
            provider_name=payload.get("provider_name", "traccar"),
            integration_type=payload.get("integration_type", "DIRECT_TELEMETRY"),
            device_secret=payload.get("device_secret"),
            metadata=payload.get("metadata"),
        )
        return created(result)

    @blueprint.delete("/devices/<device_id>")
    @require_authentication
    def unlink_device(device_id: str):
        """Unlink or deactivate a GPS tracker."""
        return ok(gps_provider.unlink_gps_device(current_user(), device_id))

    @blueprint.get("/devices/<device_id>/test")
    @require_authentication
    def test_device(device_id: str):
        """Test whether the device connection and provider are active."""
        return ok(gps_provider.test_gps_device_connection(current_user(), device_id))

    @blueprint.post("/devices/<device_id>/telemetry")
    def ingest_device_telemetry(device_id: str):
        """Ingest an authenticated hardware GPS fix from a registered tracker (REAL_GPS)."""
        payload = json_payload()
        device_secret = (
            request.headers.get("X-Device-Key")
            or request.headers.get("X-API-Key")
            or payload.get("device_secret")
        )
        if not device_secret and request.headers.get("Authorization", "").startswith("Bearer "):
            device_secret = request.headers.get("Authorization", "")[7:].strip()
        result = gps_provider.ingest_real_gps_fix(
            device_id,
            payload,
            device_secret=device_secret,
            provider_name=payload.get("provider_name"),
        )
        return created(result)

    @blueprint.post("/ingest/real")
    def ingest_real_telemetry():
        """Ingest real GPS fix by payload device ID."""
        payload = json_payload()
        device_id = payload.get("device_id")
        if not device_id:
            raise ValidationError("device_id is required.", details={"field": "device_id"})
        device_secret = (
            request.headers.get("X-Device-Key")
            or request.headers.get("X-API-Key")
            or payload.get("device_secret")
        )
        if not device_secret and request.headers.get("Authorization", "").startswith("Bearer "):
            device_secret = request.headers.get("Authorization", "")[7:].strip()
        result = gps_provider.ingest_real_gps_fix(
            str(device_id),
            payload,
            device_secret=device_secret,
            provider_name=payload.get("provider_name"),
        )
        return created(result)

    @blueprint.post("/events")
    @require_authentication
    def ingest_events():
        """Record position fixes.

        Accepts a single fix object or ``{"fixes": [...]}``. Both shapes reach
        the same service path, so a batch is classified exactly as its members
        would be individually.
        """
        payload = json_payload()
        requested_source = payload.get("source")
        if requested_source not in (None, TelemetrySource.MANUAL.value):
            raise ValidationError(
                "The source for this endpoint is MANUAL. Use /gps/mobile for authorized "
                "browser fixes or registered device telemetry for REAL_GPS.",
                details={"field": "source"},
            )
        source = TelemetrySource.MANUAL.value
        device_id = payload.get("device_id")
        actor = current_user()

        batch = payload.get("fixes")
        if batch is not None:
            result = geofence.ingest_batch(
                batch, actor, source=source, device_id=device_id
            )
            return created(result)
        return created(geofence.ingest_fix(payload, actor, source=source, device_id=device_id))

    @blueprint.post("/mobile")
    @require_authentication
    def ingest_mobile():
        """Record position fixes from an authorized mobile browser (MOBILE_GPS)."""
        payload = json_payload()
        actor = current_user()
        device_id = payload.get("device_id") or f"mobile-{actor.user_id[:8]}"
        batch = payload.get("fixes")
        if batch is not None:
            result = geofence.ingest_batch(
                batch, actor, source=TelemetrySource.MOBILE_GPS.value, device_id=device_id
            )
            return created(result)
        return created(
            geofence.ingest_fix(
                payload,
                actor,
                source=TelemetrySource.MOBILE_GPS.value,
                device_id=device_id,
            )
        )

    @blueprint.post("/vehicle")
    @require_role(Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN)
    def ingest_vehicle():
        """Record staff-entered telemetry and sensor observations (MANUAL)."""
        payload = json_payload()
        actor = current_user()
        device_id = payload.get("device_id")
        batch = payload.get("fixes")
        if batch is not None:
            result = geofence.ingest_batch(
                batch, actor, source=TelemetrySource.MANUAL.value, device_id=device_id
            )
            return created(result)
        return created(
            geofence.ingest_fix(
                payload,
                actor,
                source=TelemetrySource.MANUAL.value,
                device_id=device_id,
            )
        )

    @blueprint.get("/stream/<custody_id>")
    @require_authentication
    def stream_tracking(custody_id: str):
        """Server-Sent Events (SSE) real-time streaming of telemetry and alerts."""
        validate_uuid(custody_id, field="custody_id")
        actor = current_user()
        session = geofence._load_session(custody_id)
        geofence._require_tracking_access(actor, session)

        from services.streaming import broadcaster, sse_event

        def event_generator():
            try:
                initial_view = geofence.tracking_view(custody_id, actor)
                yield sse_event("snapshot", initial_view)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to render initial SSE snapshot: %s", exc)
                yield sse_event("error", {"message": str(exc)})

            queue = broadcaster.subscribe(custody_id)
            try:
                while True:
                    try:
                        event_name, data = queue.get(timeout=15.0)
                        yield sse_event(event_name, data)
                    except Empty:
                        yield ": keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                broadcaster.unsubscribe(custody_id, queue)

        return Response(
            stream_with_context(event_generator()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @blueprint.get("/simulate/scenarios")
    @require_authentication
    def simulator_scenarios():
        """The tracks the simulator can generate."""
        return ok({"scenarios": geofence.simulator_scenarios()})

    @blueprint.post("/simulate")
    @require_authentication
    def simulate():
        """Generate a simulated track and ingest it as SIMULATED telemetry.

        Every fix passes through the real classification path, so the verdicts
        this produces are computed rather than scripted.
        """
        payload = json_payload()
        custody_id = validate_uuid(
            payload.get("custody_id") or payload.get("custody_session_id"),
            field="custody_id",
        )
        scenario = str(payload.get("scenario") or "parked")
        result = geofence.simulate_track(
            custody_id,
            current_user(),
            scenario=scenario,
            seed=payload.get("seed"),
            zone_id=payload.get("zone_id"),
        )
        return created(result)

    @blueprint.get("/<custody_id>")
    @require_authentication
    def tracking(custody_id: str):
        """The live tracking view: position, zone, geofence, status, alerts."""
        validate_uuid(custody_id, field="custody_id")
        return ok(geofence.tracking_view(custody_id, current_user()))

    @blueprint.get("/<custody_id>/fixes")
    @require_authentication
    def fixes(custody_id: str):
        """Paginated raw telemetry, newest first."""
        validate_uuid(custody_id, field="custody_id")
        paging = parse_pagination(default_page_size=100)
        status = request.args.get("status")
        result = geofence.list_fixes(
            custody_id,
            current_user(),
            page=paging.page,
            page_size=paging.page_size,
            status=status,
        )
        return ok(result, meta={"page": paging.page, "page_size": paging.page_size})

    return blueprint


__all__ = ["create_gps_blueprint"]
