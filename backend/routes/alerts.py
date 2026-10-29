"""Security alert endpoints.

    GET  /alerts                        authenticated - staff see all, customers see their own
    POST /alerts/<id>/acknowledge       manager/admin - record that it was seen
    POST /alerts/<id>/investigate       manager/admin - mark as being investigated
    POST /alerts/<id>/resolve           manager/admin - close it, or dismiss it as a false positive
    POST /alerts/<id>/reopen            manager/admin - reopen a closed/dismissed alert

Mounted at both ``/api/v1/alerts`` and ``/api/alerts``.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import geofence
from utils.exceptions import AuthorizationError, ValidationError
from utils.pagination import parse_pagination
from utils.requests import json_payload
from utils.responses import ok
from utils.security import Role, current_user, require_authentication
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

    @blueprint.post("/<alert_id>/investigate")
    @require_authentication
    def investigate(alert_id: str):
        """Mark an alert as currently being investigated by staff."""
        validate_uuid(alert_id, field="alert_id")
        payload = json_payload(required=False)
        return ok(
            geofence.investigate_alert(
                alert_id,
                current_user(),
                notes=payload.get("notes"),
            )
        )

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

    @blueprint.post("/<alert_id>/reopen")
    @require_authentication
    def reopen(alert_id: str):
        """Reopen a previously resolved or dismissed alert."""
        validate_uuid(alert_id, field="alert_id")
        payload = json_payload(required=False)
        return ok(
            geofence.reopen_alert(
                alert_id,
                current_user(),
                reason=payload.get("reason"),
            )
        )

    @blueprint.get("/email-config")
    @require_authentication
    def get_email_config():
        """Check SMTP and notification channel status without leaking credentials."""
        actor = current_user()
        if actor.role not in (Role.VALET_MANAGER.value, Role.ADMIN.value):
            raise AuthorizationError("Manager or Admin access required.")
        from services.notifications.email import EmailChannel
        channel = EmailChannel()
        return ok(channel.describe())

    @blueprint.post("/test-email")
    @require_authentication
    def test_email():
        """Send a test email notification to verify SMTP delivery."""
        actor = current_user()
        if actor.role not in (Role.VALET_MANAGER.value, Role.ADMIN.value):
            raise AuthorizationError("Manager or Admin access required.")
        payload = json_payload()
        email_addr = payload.get("email") or actor.email
        if not email_addr or "@" not in email_addr:
            raise ValidationError("Valid email address required.", details={"field": "email"})
        from services.notifications.email import EmailChannel
        channel = EmailChannel()
        result = channel.send_test_email(email_addr, actor_id=actor.user_id)
        if not result.delivered:
            return ok({
                "delivered": False,
                "status": "FAILED",
                "channel": result.channel,
                "error": result.failure_reason,
            })
        return ok({
            "delivered": True,
            "status": "DELIVERED",
            "channel": result.channel,
            "provider_message_id": result.provider_message_id,
            "recipient": email_addr,
        })

    return blueprint


__all__ = ["create_alerts_blueprint"]
