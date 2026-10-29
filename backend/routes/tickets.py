"""Ticket and custody endpoints - the valet check-in workflow.

    GET   /tickets                        auth  - tickets the caller may see
    POST  /tickets                        valet - issue a ticket + QR (steps 5, 8, 9)
    GET   /tickets/<id>                   auth  - the digital ticket (step 10)
    POST  /tickets/<id>/check-in          valet - open custody, assign zone (steps 6, 7)
    POST  /tickets/<id>/complete          valet - return the vehicle, close the ticket
    POST  /tickets/<id>/cancel            valet - void a ticket
    GET   /custody-sessions/<id>          auth  - custody status
    PATCH /custody-sessions/<id>          valet - move the session to a new state

Two blueprints live here because the resources have different lifecycles: a
ticket is issued and completed, while a custody session is opened, moved
through states and closed. They are kept in one module because every rule
connecting them - one session per ticket, the status projection - is easier to
review side by side.

The ``status`` field in a ticket response is the **projected** vocabulary
(``CREATED``/``IN_CUSTODY``/``READY_FOR_PICKUP``/``COMPLETED``/``CANCELLED``),
not the stored one. ``stored_status`` is included alongside it so a reviewer can
see the mapping instead of taking it on trust. See ``services/tickets``.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import tickets as ticket_service
from utils.pagination import parse_pagination
from utils.requests import json_payload
from utils.responses import created, ok, paginated
from utils.security import current_user, require_authentication
from utils.validation import require_fields, validate_text

REASON_MAX_LENGTH = 500


def create_tickets_blueprint() -> Blueprint:
    """Build a fresh tickets blueprint (a new instance per app)."""
    blueprint = Blueprint("tickets", __name__)

    @blueprint.get("")
    @require_authentication
    def list_tickets():
        """Tickets the caller may see, newest first.

        ``?status=`` filters on the projected status, because that is the
        vocabulary the client was given.
        """
        actor = current_user()
        params = parse_pagination()
        status = request.args.get("status")

        items, total = ticket_service.list_tickets(
            actor,
            status=status.strip() if status else None,
            page=params.page,
            page_size=params.page_size,
        )
        return paginated(items, page=params.page, page_size=params.page_size, total=total)

    @blueprint.post("")
    @require_authentication
    def issue_ticket():
        """Issue a ticket for an already-registered vehicle.

        Generates the unique ticket number and renders the QR code in the same
        request, so the caller can display the digital ticket immediately.
        Custody is *not* started here - see ``/check-in``.
        """
        body = require_fields(json_payload(), "vehicle_id")
        ticket = ticket_service.issue_ticket(current_user(), body)
        return created(
            {
                "ticket": ticket_service.ticket_dict(ticket),
                "message": f"Ticket {ticket.ticket_code} issued.",
            }
        )

    @blueprint.get("/<ticket_id>")
    @require_authentication
    def get_ticket(ticket_id: str):
        """The digital ticket: id, vehicle, customer, times, zone, status, QR."""
        ticket = ticket_service.get_ticket(current_user(), ticket_id)
        return ok({"ticket": ticket_service.ticket_dict(ticket)})

    @blueprint.post("/<ticket_id>/check-in")
    @require_authentication
    def check_in(ticket_id: str):
        """Hand the vehicle over: open custody, assign a zone, record condition.

        Expects ``zone_id`` and optionally the drop-off photo references
        returned by ``POST /uploads/images``, the odometer, the fuel level and
        any pre-existing damage notes.
        """
        body = require_fields(json_payload(), "zone_id")
        ticket = ticket_service.check_in(current_user(), ticket_id, body)
        return ok(
            {
                "ticket": ticket_service.ticket_dict(ticket),
                "message": "Vehicle checked in. Custody session started.",
            }
        )

    @blueprint.post("/<ticket_id>/complete")
    @require_authentication
    def complete_ticket(ticket_id: str):
        """Return the vehicle to the customer and complete the ticket.

        Closes the custody session, frees the parking slot and records the
        condition the customer accepted.
        """
        body = json_payload(required=False)
        ticket = ticket_service.complete_ticket(current_user(), ticket_id, body)
        return ok(
            {
                "ticket": ticket_service.ticket_dict(ticket),
                "message": f"Ticket {ticket.ticket_code} completed.",
            }
        )

    @blueprint.post("/<ticket_id>/cancel")
    @require_authentication
    def cancel_ticket(ticket_id: str):
        """Void a ticket, closing any open custody session."""
        body = json_payload(required=False)
        if body.get("reason") is not None:
            validate_text(
                body["reason"], field="reason", max_length=REASON_MAX_LENGTH, required=False
            )
        ticket = ticket_service.cancel_ticket(current_user(), ticket_id, body)
        return ok(
            {
                "ticket": ticket_service.ticket_dict(ticket),
                "message": f"Ticket {ticket.ticket_code} cancelled.",
            }
        )

    return blueprint


def create_custody_blueprint() -> Blueprint:
    """Build a fresh custody-session blueprint."""
    blueprint = Blueprint("custody", __name__)

    @blueprint.get("/<session_id>")
    @require_authentication
    def get_session(session_id: str):
        """The custody status and timeline for one session."""
        session = ticket_service.get_custody_session(current_user(), session_id)
        return ok({"custody_session": session.to_dict()})

    @blueprint.patch("/<session_id>")
    @require_authentication
    def update_session(session_id: str):
        """Move the session to a new state.

        Illegal transitions are refused with the list of permitted ones, so a
        client can recover instead of guessing. Closing custody is not done
        here - use ``POST /tickets/<id>/complete``.
        """
        body = require_fields(json_payload(), "status")
        session = ticket_service.get_custody_session(current_user(), session_id)
        target = str(body["status"] or "").strip().upper()
        if session.status == target:
            return ok(
                {
                    "custody_session": session.to_dict(),
                    "message": f"Custody session is already {session.status}.",
                }
            )
        session = ticket_service.update_custody_status(
            current_user(), session_id, body["status"]
        )
        return ok(
            {
                "custody_session": session.to_dict(),
                "message": f"Custody session is now {session.status}.",
            }
        )

    return blueprint


__all__ = ["create_custody_blueprint", "create_tickets_blueprint"]
