"""Lookup endpoints the check-in workflow needs.

    GET /customers        valet - the customer directory (workflow step 1)
    GET /parking-zones    auth  - zones that can be assigned (workflow step 6)

Both are read-only and deliberately narrow. The customer directory is
staff-only and returns only the fields needed to pick a person - no password
hash, no notes, no login history - because it is effectively a phone book over
the customer table.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import tickets as ticket_service
from utils.requests import query_flag
from utils.responses import ok
from utils.security import Role, current_user, require_authentication, require_role
from utils.validation import validate_text

SEARCH_MAX_LENGTH = 80


def create_directory_blueprint() -> Blueprint:
    """Build a fresh directory blueprint (a new instance per app)."""
    blueprint = Blueprint("directory", __name__)

    @blueprint.get("/customers")
    @require_role(Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN)
    def list_customers():
        """Customers a valet may check in, searchable by name or email."""
        search = request.args.get("search")
        customers = ticket_service.list_customers(
            current_user(),
            search=validate_text(
                search.strip() if search else None,
                field="search",
                max_length=SEARCH_MAX_LENGTH,
                required=False,
            ),
        )
        return ok({"items": customers, "total": len(customers)})

    @blueprint.get("/parking-zones")
    @require_authentication
    def list_zones():
        """Parking zones. ``?parkable_only=true`` hides zones that cannot take a car."""
        zones = ticket_service.list_zones(
            current_user(), parkable_only=query_flag("parkable_only")
        )
        return ok({"items": zones, "total": len(zones)})

    return blueprint


__all__ = ["create_directory_blueprint"]
