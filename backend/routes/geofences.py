"""Geofence endpoints.

    GET    /geofences                   authenticated - list safe zones with geometry
    POST   /geofences                   manager/admin/customer - create zone/safe boundary
    POST   /geofences/preview           authenticated - preview boundary calculation
    GET    /geofences/<zone_id>         authenticated - safe zone details
    PUT    /geofences/<zone_id>         manager/admin/creator - update/replace safe zone
    PATCH  /geofences/<zone_id>         manager/admin/creator - reconfigure boundary
    DELETE /geofences/<zone_id>         manager/admin/creator - delete inactive zone
    POST   /geofences/<zone_id>/activate    manager/admin/creator - activate safe zone
    POST   /geofences/<zone_id>/deactivate  manager/admin/creator - deactivate safe zone

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

    @blueprint.post("/preview")
    @require_authentication
    def preview():
        """Calculate and preview a safe zone boundary and effective area without saving."""
        return ok(geofence.preview_geofence(json_payload(), current_user()))

    @blueprint.get("/<zone_id>")
    @require_authentication
    def get_zone(zone_id: str):
        """Fetch a specific safe zone / geofence details."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.get_geofence(zone_id, current_user()))

    @blueprint.put("/<zone_id>")
    @require_authentication
    def replace_zone(zone_id: str):
        """Update or reconfigure a safe zone boundary."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.update_geofence(zone_id, json_payload(), current_user()))

    @blueprint.patch("/<zone_id>")
    @require_authentication
    def update(zone_id: str):
        """Reconfigure an existing geofence."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.update_geofence(zone_id, json_payload(), current_user()))

    @blueprint.delete("/<zone_id>")
    @require_authentication
    def delete_zone(zone_id: str):
        """Delete an inactive safe zone."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.delete_geofence(zone_id, current_user()))

    @blueprint.post("/<zone_id>/activate")
    @require_authentication
    def activate_zone(zone_id: str):
        """Activate an inactive safe zone."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.activate_geofence(zone_id, current_user()))

    @blueprint.post("/<zone_id>/deactivate")
    @require_authentication
    def deactivate_zone(zone_id: str):
        """Deactivate an active safe zone without deleting it."""
        validate_uuid(zone_id, field="zone_id")
        return ok(geofence.deactivate_geofence(zone_id, current_user()))

    return blueprint


__all__ = ["create_geofences_blueprint"]
