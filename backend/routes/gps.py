"""GPS telemetry endpoints.

    POST /gps/events                staff - record one fix, or a batch
    GET  /gps/<custody_id>          owner or staff - the live tracking view
    GET  /gps/<custody_id>/fixes    owner or staff - paginated raw fixes
    GET  /gps/simulate/scenarios    staff - what the simulator can generate
    POST /gps/simulate              staff - generate and ingest a simulated track

These handlers only translate HTTP into service calls. The geofence maths lives
in :mod:`ai.anomaly.geofence`, the workflow in :mod:`services.geofence`, and the
track generator in :mod:`ai.synthetic.gps_tracks`.

**Provenance.** ``POST /gps/events`` records what it is given and defaults to
``MANUAL`` -- a position keyed in by a person. The simulator endpoint forces
``SIMULATED`` and cannot be talked out of it. There is no endpoint that lets a
caller claim ``HARDWARE`` on someone else's behalf without saying so
explicitly, because the whole point of the column is that a reader can tell
where a fix came from.

Mounted at both ``/api/v1/gps`` and ``/api/gps`` -- see ``routes/__init__.py``.
"""

from __future__ import annotations

from flask import Blueprint, request

from models.enums import TelemetrySource
from services import geofence
from utils.pagination import parse_pagination
from utils.requests import json_payload
from utils.responses import created, ok
from utils.security import current_user, require_authentication
from utils.validation import validate_choice, validate_uuid


def create_gps_blueprint() -> Blueprint:
    """Build a fresh telemetry blueprint.

    Fresh per call because Flask refuses to register one blueprint object
    twice, and this is mounted at two prefixes.
    """
    blueprint = Blueprint("gps", __name__)

    @blueprint.post("/events")
    @require_authentication
    def ingest_events():
        """Record position fixes.

        Accepts a single fix object or ``{"fixes": [...]}``. Both shapes reach
        the same service path, so a batch is classified exactly as its members
        would be individually.
        """
        payload = json_payload()
        source = validate_choice(
            payload.get("source") or "MANUAL",
            [item.value for item in TelemetrySource],
            field="source",
        )
        device_id = payload.get("device_id")
        actor = current_user()

        batch = payload.get("fixes")
        if batch is not None:
            result = geofence.ingest_batch(
                batch, actor, source=source, device_id=device_id
            )
            return created(result)
        return created(geofence.ingest_fix(payload, actor, source=source, device_id=device_id))

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
        # Validated in the service, like the condition module's demo scenarios:
        # ``validate_choice`` upper-cases its input, which would reject these
        # deliberately lowercase identifiers.
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
