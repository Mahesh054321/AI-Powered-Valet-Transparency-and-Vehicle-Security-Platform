"""The centralized alert feed.

    GET  /alerts                     list the alerts this actor may see
    POST /alerts                     raise one (staff)
    GET  /alerts/<id>                one alert
    POST /alerts/<id>/read           mark it read for the calling user
    POST /alerts/read-all            mark everything visible as read
    POST /alerts/<id>/resolve        close it (manager/admin)
    GET  /alerts/summary             unread counts for the notification badge

**Two different questions are kept apart here.** ``status`` is the alert's
lifecycle — has it been dealt with. ``read`` is whether *this user* has seen it.
The endpoints are separate because the answers are per-person: a customer
marking their alert read must not clear it from the manager's queue.

``GET /alerts/summary`` exists so the notification badge is one cheap count
rather than a fetch of the whole feed on every poll.
"""

from __future__ import annotations

from flask import Blueprint, request

from services.alerts import (
    alert_dict,
    create_alert,
    get_alert,
    is_manager,
    list_alerts,
    mark_all_read,
    mark_read,
    resolve_alert,
    unread_count,
)
from services.notifications import describe_channels
from utils.exceptions import AuthorizationError, ValidationError
from utils.pagination import parse_pagination
from utils.responses import created, ok
from utils.security import current_user, require_authentication


def _bool_param(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _optional(name: str) -> str | None:
    raw = request.args.get(name)
    return raw.strip() if raw and raw.strip() else None


def create_alert_center_blueprint() -> Blueprint:
    """Build a fresh alert-centre blueprint."""
    blueprint = Blueprint("alert_center", __name__)

    @blueprint.get("")
    @blueprint.get("/")
    @require_authentication
    def index():
        """The alerts this actor may see, newest and worst first.

        Filters: ``status``, ``type``, ``severity``, ``vehicle_id``,
        ``unread_only``. Role scoping is applied server-side to the query, so a
        customer cannot widen their view by omitting a parameter.
        """
        paging = parse_pagination(default_page_size=25)
        payload = list_alerts(
            current_user(),
            status=_optional("status"),
            category=_optional("type"),
            severity=_optional("severity"),
            vehicle_id=_optional("vehicle_id"),
            unread_only=_bool_param("unread_only"),
            page=paging.page,
            page_size=paging.page_size,
        )
        return ok(
            {
                "alerts": payload["items"],
                # Total is part of the body, not just meta: a client cannot
                # render "page 2 of 9" or know when to stop paging without it.
                "total": payload["total"],
                "unread": unread_count(current_user()),
                "delivery": "polled",
            },
            meta={
                "page": paging.page,
                "page_size": paging.page_size,
                "total": payload["total"],
            },
        )

    @blueprint.get("/summary")
    @blueprint.get("/summary/")
    @require_authentication
    def summary():
        """Unread counts for the notification badge.

        A separate endpoint so the badge costs one indexed count instead of
        fetching the feed on every poll. It also reports which transports are
        actually live, so a reviewer is not left guessing whether email exists.
        """
        actor = current_user()
        payload = list_alerts(actor, unread_only=True, page=1, page_size=1)
        return ok(
            {
                "unread": unread_count(actor),
                "total": payload["total"],
                "channels": describe_channels(),
                "can_resolve": is_manager(actor),
            }
        )

    @blueprint.post("")
    @blueprint.post("/")
    @require_authentication
    def create():
        """Raise an alert. Staff only.

        Customers cannot raise alerts: the feed describes what the system
        observed about their vehicle, and letting the subject of an observation
        write observations would make the feed untrustworthy.
        """
        from services.alerts import is_staff

        actor = current_user()
        if not is_staff(actor):
            raise AuthorizationError(
                "Only staff may raise an alert.", details={"role": actor.role}
            )

        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValidationError("Request body must be a JSON object.")

        audiences = body.get("audiences")
        if isinstance(audiences, str):
            audiences = [audiences]

        alert, was_created = create_alert(
            category=body.get("type"),
            message=body.get("message") or "",
            severity=body.get("severity") or "MEDIUM",
            custody_session_id=body.get("custody_id") or body.get("custody_session_id"),
            vehicle_id=body.get("vehicle_id"),
            audiences=audiences,
            source_entity_type=body.get("source_entity_type"),
            source_id=body.get("source_id"),
            source_type=body.get("source_type"),
            actor=actor,
        )
        payload = alert_dict(alert, actor=actor)
        # 201 on a new alert, 200 when the source already produced one. Saying
        # 201 for a de-duplicated hit would claim a row that does not exist.
        return (created(payload) if was_created else ok(payload))

    @blueprint.get("/<alert_id>")
    @blueprint.get("/<alert_id>/")
    @require_authentication
    def detail(alert_id: str):
        return ok(get_alert(current_user(), alert_id))

    @blueprint.post("/read-all")
    @blueprint.post("/read-all/")
    @require_authentication
    def read_all():
        """Mark everything currently visible to this actor as read.

        Scoped by the same predicate as the list, so it cannot clear alerts the
        actor was never shown.
        """
        return ok(mark_all_read(current_user()))

    @blueprint.post("/<alert_id>/read")
    @blueprint.post("/<alert_id>/read/")
    @require_authentication
    def read(alert_id: str):
        """Mark one alert read *for the calling user*.

        Deliberately not a status change on the alert: read state belongs to the
        reader, and clearing the alert itself would remove it from everyone
        else's queue.
        """
        return ok(mark_read(current_user(), alert_id))

    @blueprint.post("/<alert_id>/resolve")
    @blueprint.post("/<alert_id>/resolve/")
    @require_authentication
    def resolve(alert_id: str):
        """Close an alert. Manager and admin only.

        ``dismiss=true`` records it as dismissed rather than resolved — "we
        judged this needed no action" is a different claim from "we dealt with
        it", and the audit trail should not blur them.
        """
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValidationError("Request body must be a JSON object.")

        return ok(
            resolve_alert(
                current_user(),
                alert_id,
                notes=body.get("notes"),
                dismiss=bool(body.get("dismiss", False)),
            )
        )

    return blueprint


__all__ = ["create_alert_center_blueprint"]
