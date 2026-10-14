"""Vehicle endpoints.

    GET    /vehicles               auth - the caller's vehicles (staff: all)
    POST   /vehicles               auth - register a vehicle
    GET    /vehicles/<id>          auth - one vehicle
    PATCH  /vehicles/<id>          auth - edit a vehicle
    DELETE /vehicles/<id>          auth - archive it (never a hard delete)
    GET    /vehicles/<id>/sessions auth - previous valet sessions

A customer may only reach their own vehicles. That ownership decision lives in
``services/vehicles``, not here, so a new handler cannot forget it.
"""

from __future__ import annotations

from flask import Blueprint, request

from models.enums import VehicleStatus
from services import vehicles as vehicle_service
from utils.pagination import parse_pagination
from utils.requests import json_payload, query_flag
from utils.responses import created, no_content, ok, paginated
from utils.security import current_user, require_authentication
from utils.validation import (
    require_fields,
    validate_choice,
    validate_text,
    validate_uuid,
)

SEARCH_MAX_LENGTH = 80


def _arg(name: str) -> str | None:
    """A non-empty, trimmed query parameter, or ``None``."""
    value = request.args.get(name)
    return value.strip() if value and value.strip() else None


def _uuid_arg(name: str):
    """An optional UUID query parameter, validated when present."""
    value = _arg(name)
    return validate_uuid(value, field=name) if value else None


def create_vehicles_blueprint() -> Blueprint:
    """Build a fresh vehicles blueprint (a new instance per app)."""
    blueprint = Blueprint("vehicles", __name__)

    @blueprint.get("")
    @require_authentication
    def list_vehicles():
        """The vehicles the caller may see, newest first."""
        actor = current_user()
        params = parse_pagination()

        status_value = _arg("status")
        rows, total = vehicle_service.list_vehicles(
            actor,
            owner_id=_uuid_arg("owner_id"),
            status=(
                validate_choice(
                    status_value, [member.value for member in VehicleStatus], field="status"
                )
                if status_value
                else None
            ),
            search=validate_text(
                _arg("search"), field="search", max_length=SEARCH_MAX_LENGTH, required=False
            ),
            include_archived=query_flag("include_archived"),
            page=params.page,
            page_size=params.page_size,
        )
        return paginated(
            [
                vehicle_service.vehicle_dict(row, include_owner=vehicle_service.is_staff(actor))
                for row in rows
            ],
            page=params.page,
            page_size=params.page_size,
            total=total,
        )

    @blueprint.post("")
    @require_authentication
    def create_vehicle():
        """Register a vehicle.

        A customer registers to themselves; valet staff may register on a
        customer's behalf during check-in by naming ``owner_id``.
        """
        body = require_fields(json_payload(), "plate_number", "make")
        actor = current_user()

        vehicle = vehicle_service.create_vehicle(actor, body)
        return created(
            {
                "vehicle": vehicle_service.vehicle_dict(vehicle, include_owner=True),
                "message": f"{vehicle.plate_number} registered.",
            }
        )

    @blueprint.get("/<vehicle_id>")
    @require_authentication
    def get_vehicle(vehicle_id: str):
        """One vehicle, including whether it is in custody right now."""
        actor = current_user()
        vehicle = vehicle_service.get_vehicle(actor, vehicle_id)
        return ok(
            {
                "vehicle": vehicle_service.vehicle_dict(
                    vehicle, include_owner=vehicle_service.is_staff(actor)
                )
            }
        )

    @blueprint.patch("/<vehicle_id>")
    @require_authentication
    def update_vehicle(vehicle_id: str):
        """Edit a vehicle's details."""
        actor = current_user()
        vehicle = vehicle_service.update_vehicle(actor, vehicle_id, json_payload())
        return ok(
            {
                "vehicle": vehicle_service.vehicle_dict(vehicle, include_owner=True),
                "message": "Vehicle updated.",
            }
        )

    @blueprint.delete("/<vehicle_id>")
    @require_authentication
    def archive_vehicle(vehicle_id: str):
        """Archive a vehicle.

        Returns 204 like a delete, but the row survives: a vehicle with custody
        history must stay in the audit trail. Refused while custody is open.
        """
        vehicle_service.archive_vehicle(current_user(), vehicle_id)
        return no_content()

    @blueprint.get("/<vehicle_id>/sessions")
    @require_authentication
    def vehicle_sessions(vehicle_id: str):
        """Previous valet sessions for this vehicle, each with its ticket."""
        actor = current_user()
        vehicle = vehicle_service.get_vehicle(actor, vehicle_id)
        sessions = vehicle_service.list_vehicle_sessions(actor, vehicle_id)
        return ok(
            {
                "vehicle": vehicle_service.vehicle_dict(vehicle),
                "sessions": sessions,
                "total": len(sessions),
            }
        )

    return blueprint


__all__ = ["create_vehicles_blueprint"]
