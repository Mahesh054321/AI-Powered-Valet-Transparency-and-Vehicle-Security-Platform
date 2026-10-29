"""Audit trail endpoints: the log viewer and the custody timeline.

    GET /audit                          paginated log, newest first, filtered
    GET /audit/filters                  distinct values present, for the dropdowns
    GET /audit/timeline/<custody_id>    one custody, oldest first
    GET /audit/vehicles/<vehicle_id>    every custody timeline for one vehicle

**Read-only, and there is no way to make it otherwise.** No ``POST``, ``PATCH``,
``PUT`` or ``DELETE`` is registered here, and the table itself rejects
``UPDATE`` and ``DELETE`` at the database level (migration 0008). That is a
deliberate pair of locks rather than a belt-and-braces flourish: the UI cannot
offer a control for an operation that has no route, and a caller who reaches
the database by some other means still cannot rewrite history.

**Manager and administrator only.** The log contains email addresses, IP
addresses and the internal state of other people's vehicles. An operator has no
oversight role and a customer must never see another customer's custody, so the
service refuses both with 403 -- not 404, because "you may not read the audit
trail" is a fact about the caller's role that is useful to state, unlike the
existence of somebody else's custody session.

**Timelines run oldest-first; the log runs newest-first.** A timeline is a
narrative and is read in the order things happened. A log is a worklist and is
read from the most recent entry backwards. Serving both in the same order would
make one of the two awkward to use.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import audit_trail
from utils.pagination import parse_pagination
from utils.responses import ok
from utils.security import current_user, require_authentication
from utils.validation import validate_uuid


def create_audit_blueprint() -> Blueprint:
    """Build a fresh audit blueprint (mounted at two prefixes)."""
    blueprint = Blueprint("audit", __name__)

    @blueprint.get("")
    @blueprint.get("/")
    @require_authentication
    def index():
        """A page of audit entries, newest first.

        Filters are all optional and combine with AND:

        ``action``            a single action name, e.g. ``VEHICLE_CHECKED_IN``
        ``entity_type``       ``vehicles``, ``valet_tickets``, ...
        ``entity_id``         one specific row
        ``actor_id``          everything one user did
        ``custody_session_id`` everything that happened during one custody
        ``outcome``           ``SUCCESS`` / ``FAILURE`` / ``DENIED``
        ``date_from``/``date_to``  ISO-8601 bounds on ``occurred_at``
        ``lifecycle_only``    the fourteen lifecycle events only
        """
        return ok(
            audit_trail.list_events(
                current_user(),
                pagination=parse_pagination(default_page_size=50),
                action=request.args.get("action"),
                entity_type=request.args.get("entity_type"),
                entity_id=request.args.get("entity_id"),
                actor_id=request.args.get("actor_id"),
                custody_session_id=request.args.get("custody_session_id"),
                outcome=request.args.get("outcome"),
                date_from=request.args.get("date_from"),
                date_to=request.args.get("date_to"),
                lifecycle_only=(
                    str(request.args.get("lifecycle_only", "")).lower()
                    in {"1", "true", "yes"}
                ),
            )
        )

    @blueprint.get("/filters")
    @require_authentication
    def filters():
        """The distinct values actually present in the log.

        Read from the data rather than hardcoded, so a dropdown never offers an
        action that has not occurred or omits one that has.
        """
        return ok(audit_trail.filter_options(current_user()))

    @blueprint.get("/timeline/<custody_id>")
    @require_authentication
    def timeline(custody_id: str):
        """One custody's events, oldest first, with the lifecycle gaps marked."""
        validate_uuid(custody_id, field="custody_id")
        return ok(audit_trail.custody_timeline(current_user(), custody_id))

    @blueprint.get("/vehicles/<vehicle_id>")
    @require_authentication
    def vehicle(vehicle_id: str):
        """Every custody timeline for one vehicle, most recent custody first."""
        validate_uuid(vehicle_id, field="vehicle_id")
        return ok(audit_trail.vehicle_timelines(current_user(), vehicle_id))

    return blueprint


__all__ = ["create_audit_blueprint"]
