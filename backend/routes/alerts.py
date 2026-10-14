"""Security alert endpoints.

    GET  /alerts                    authenticated - staff see all, customers see their own
    POST /alerts/<id>/acknowledge   manager/admin - record that it was seen
    POST /alerts/<id>/resolve       manager/admin - close it, or dismiss it as a false positive

Alerts are *created* by the geofence engine, never by a client: there is
deliberately no ``POST /alerts``. A security finding is a machine observation
that a human then judges, so letting a caller write one directly would put
unverified claims into a table that exists to be trusted.

Triage is restricted to managers and administrators, the same deliberate
narrowness as damage verification: the person who drove the vehicle is not the
one who dismisses the alert about it.

Mounted at both ``/api/v1/alerts`` and ``/api/alerts``.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import geofence
from utils.pagination import parse_pagination
from utils.requests import json_payload
from utils.responses import ok
from utils.security import current_user, require_authentication
from utils.validation import validate_uuid


def create_alerts_blueprint() -> Blueprint:
    """Build a fresh alerts blueprint (mounted at two prefixes)."""
    blueprint = Blueprint("alerts", __name__)

    @blueprint.get("")
    @blueprint.get("/")
    @require_authentication
    def index():
        """List alerts, newest first, with optional status and severity filters."""
        paging = parse_pagination(default_page_size=50)
        result = geofence.list_alerts(
            current_user(),
            custody_session_id=request.args.get("custody_session_id"),
            status=request.args.get("status"),
            severity=request.args.get("severity"),
            page=paging.page,
            page_size=paging.page_size,
        )
        return ok(result, meta={"page": paging.page, "page_size": paging.page_size})

    @blueprint.post("/<alert_id>/acknowledge")
    @require_authentication
    def acknowledge(alert_id: str):
        """Record that a manager has seen the alert. Does not close it."""
        validate_uuid(alert_id, field="alert_id")
        return ok(geofence.acknowledge_alert(alert_id, current_user()))

    @blueprint.post("/<alert_id>/resolve")
    @require_authentication
    def resolve(alert_id: str):
        """Close the alert, or dismiss it as a false positive with ``dismiss``."""
        validate_uuid(alert_id, field="alert_id")
        payload = json_payload(required=False)
        return ok(
            geofence.resolve_alert(
                alert_id,
                current_user(),
                dismiss=bool(payload.get("dismiss")),
                notes=payload.get("notes"),
            )
        )

    return blueprint


__all__ = ["create_alerts_blueprint"]
