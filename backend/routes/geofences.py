"""Geofence endpoints.

    GET   /geofences            authenticated - zones with the geometry the map needs
    POST  /geofences            manager/admin - create a zone and its boundary
    PATCH /geofences/<zone_id>  manager/admin - move or reconfigure a boundary

Creating a geofence is a manager-level act. Moving a boundary changes the
meaning of every future GPS fix, so it is not something an operator should be
able to do in passing, and every change is audited as ``GEOFENCE_CONFIGURED``.

The API takes ``centre + radius`` as specified in the brief and generates a
circular ring into the stored polygon, so one point-in-polygon test serves both
a generated circle and an irregular real-world yard. An explicit
``geofence_polygon`` overrides the generated one.

Mounted at both ``/api/v1/geofences`` and ``/api/geofences``.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import geofence
from utils.requests import json_payload
from utils.responses import created, ok
from utils.security import current_user, require_authentication
from utils.validation import validate_uuid


def create_geofences_blueprint() -> Blueprint:
    """Build a fresh geofence blueprint (mounted at two prefixes)."""
    blueprint = Blueprint("geofences", __name__)

    @blueprint.get("")
    @blueprint.get("/")
    @require_authentication
    def index():
        """Every zone with its polygon, centre, radius and occupancy."""
        return ok(geofence.list_geofences(current_user(), status=request.args.get("status")))

    @blueprint.post("")
    @blueprint.post("/")
    @require_authentication
    def create():
        """Create a parking zone and its geofence."""
        return created(geofence.create_geofence(json_payload(), current_user()))

    @blueprint.patch("/<zone_id>")
    @require_authentication
    def update(zone_id: str):
        """Reconfigure an existing geofence."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.update_geofence(zone_id, json_payload(), current_user()))

    return blueprint


__all__ = ["create_geofences_blueprint"]
