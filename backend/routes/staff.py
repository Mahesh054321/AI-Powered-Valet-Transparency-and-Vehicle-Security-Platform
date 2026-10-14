"""Valet operator dashboard endpoints.

    GET  /staff/dashboard              one composed snapshot for the operator screen
    GET  /staff/custody/<id>/condition the full before/after comparison for one car

A single composed call rather than seven, for the same reason as the customer
dashboard and with more force: this is the screen an operator *acts* on. If the
vehicle list and the alert list were fetched separately, a car could read calm
in one and breaching in the other, and the operator would act on whichever they
happened to read last.

**This route performs no writes.** Every action on the operator screen — status
change, mark ready, complete pickup, upload photos, acknowledge an alert —
calls the endpoint that already exists for it. One state machine, one audit
path, and no second copy of those rules living in a dashboard.
"""

from __future__ import annotations

from flask import Blueprint

from services.staff_dashboard import build_dashboard, condition_report
from utils.responses import ok
from utils.security import current_user, require_authentication


def create_staff_blueprint() -> Blueprint:
    """Build a fresh staff dashboard blueprint."""
    blueprint = Blueprint("staff", __name__)

    @blueprint.get("/dashboard")
    @blueprint.get("/dashboard/")
    @require_authentication
    def dashboard():
        """Everything the operator screen renders, in one snapshot.

        Staff-only, enforced by ``build_dashboard``: a customer asking for this
        gets 403 rather than an empty screen that implies they have no vehicles.
        """
        return ok(build_dashboard(current_user()))

    @blueprint.get("/custody/<custody_id>/condition")
    @require_authentication
    def custody_condition(custody_id: str):
        """The before/after comparison for one vehicle in custody.

        A pass-through so the operator screen has one place to call; the access
        decision stays inside the inspection service where it already lives.
        """
        return ok(condition_report(current_user(), custody_id))

    return blueprint


__all__ = ["create_staff_blueprint"]
