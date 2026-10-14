"""Customer dashboard endpoint.

    GET /customers/me/dashboard   one composed snapshot for the signed-in customer

A single call rather than ten, so the cards cannot disagree with each other:
parallel requests are read at different moments, and a dashboard showing a
custody status from before a valet moved the car alongside a queue position from
after is worse than either alone.

**Nothing here is static.** Every section reports its own ``state`` -- ``ok``,
``empty`` or ``unavailable`` -- because a customer with no car in custody is a
normal condition and must not look like a failure. An ``empty`` section is a real
answer from the database, not a placeholder.

Ownership is enforced by the services this composes (``tracking_view``,
``get_inspection_report``, ``list_alerts``), each of which already decides what
this actor may see. This route does not re-derive those rules.
"""

from __future__ import annotations

from flask import Blueprint

from services.customer_dashboard import build_dashboard
from utils.responses import ok
from utils.security import current_user, require_authentication


def create_dashboard_blueprint() -> Blueprint:
    """Build a fresh dashboard blueprint."""
    blueprint = Blueprint("dashboard", __name__)

    @blueprint.get("/me/dashboard")
    @blueprint.get("/me/dashboard/")
    @require_authentication
    def my_dashboard():
        """Everything the customer dashboard renders, in one snapshot.

        Scoped to the signed-in customer by ``build_dashboard``: a customer sees
        their own open custody session and nothing else. There is no
        ``customer_id`` parameter to tamper with, which is the point of putting
        it under ``/me``.
        """
        return ok(build_dashboard(current_user()))

    return blueprint


__all__ = ["create_dashboard_blueprint"]
