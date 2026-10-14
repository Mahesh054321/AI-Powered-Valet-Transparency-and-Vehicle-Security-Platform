"""Tests for the ticket status projection and the custody state machine.

These are pure functions - no database, no Flask - so the whole mapping table
from ``services/tickets`` is checked exhaustively and cheaply. That matters
because the projection is the contract clients code against: if it drifted,
every dashboard would misreport a car's whereabouts.
"""

from __future__ import annotations

import pytest

from models.enums import CustodyStatus, TicketStatus
from services.tickets import (
    ALLOWED_CUSTODY_TRANSITIONS,
    API_TICKET_STATUSES,
    generate_ticket_code,
    project_ticket_status,
)


# ---------------------------------------------------------------------------
# The projection table
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("stored", "custody", "expected"),
    [
        # Issued but not handed over.
        (TicketStatus.ISSUED, None, "CREATED"),
        (TicketStatus.ISSUED, CustodyStatus.CHECKED_IN, "CREATED"),
        # Active: custody open, so which sub-state depends on the session.
        (TicketStatus.ACTIVE, CustodyStatus.CHECKED_IN, "IN_CUSTODY"),
        (TicketStatus.ACTIVE, CustodyStatus.PARKED, "IN_CUSTODY"),
        (TicketStatus.ACTIVE, CustodyStatus.IN_TRANSIT, "IN_CUSTODY"),
        (TicketStatus.ACTIVE, CustodyStatus.RETRIEVAL_REQUESTED, "IN_CUSTODY"),
        (TicketStatus.ACTIVE, CustodyStatus.RETRIEVAL_IN_PROGRESS, "IN_CUSTODY"),
        (TicketStatus.ACTIVE, CustodyStatus.DISPUTED, "IN_CUSTODY"),
        # The one state that exists only in the projected vocabulary.
        (TicketStatus.ACTIVE, CustodyStatus.READY_FOR_PICKUP, "READY_FOR_PICKUP"),
        # Terminal states pass straight through.
        (TicketStatus.COMPLETED, CustodyStatus.CHECKED_OUT, "COMPLETED"),
        (TicketStatus.COMPLETED, None, "COMPLETED"),
        (TicketStatus.CANCELLED, None, "CANCELLED"),
        (TicketStatus.CANCELLED, CustodyStatus.CHECKED_OUT, "CANCELLED"),
        # Passed through rather than hidden - see the module docstring.
        (TicketStatus.EXPIRED, None, "EXPIRED"),
    ],
)
def test_projection_maps_every_stored_state(stored, custody, expected):
    assert project_ticket_status(str(stored), str(custody) if custody else None) == expected


def test_every_stored_ticket_status_projects_to_a_known_value():
    """No internal value may leak to a client."""
    for member in TicketStatus:
        assert project_ticket_status(str(member)) in API_TICKET_STATUSES


def test_every_projected_status_is_reachable():
    """No advertised status is dead code."""
    reachable = {
        project_ticket_status(str(ticket), str(custody))
        for ticket in TicketStatus
        for custody in [*CustodyStatus, None]
    }
    assert set(API_TICKET_STATUSES) == reachable


def test_an_unknown_stored_status_fails_loudly():
    """A future schema change must break here, not silently reach a client."""
    with pytest.raises(ValueError, match="Unknown ticket status"):
        project_ticket_status("SOMETHING_NEW")


# ---------------------------------------------------------------------------
# The custody state machine
# ---------------------------------------------------------------------------
def test_the_transition_table_covers_every_custody_status():
    """An unlisted status would accept no transitions at all, silently."""
    assert set(ALLOWED_CUSTODY_TRANSITIONS) == {str(member) for member in CustodyStatus}


def test_every_transition_target_is_a_real_status():
    for source, targets in ALLOWED_CUSTODY_TRANSITIONS.items():
        assert source in {str(member) for member in CustodyStatus}
        for target in targets:
            assert target in {str(member) for member in CustodyStatus}


def test_a_closed_session_has_no_outgoing_transitions():
    assert ALLOWED_CUSTODY_TRANSITIONS[str(CustodyStatus.CHECKED_OUT)] == ()


def test_the_transition_table_is_acyclic_towards_completion():
    """No path may walk back from a retrieval state to CHECKED_IN.

    CHECKED_IN is the entry state; a vehicle that has been parked and fetched
    must not be able to return to "just dropped off", which would erase the
    meaning of the timestamps recorded along the way.
    """
    for source, targets in ALLOWED_CUSTODY_TRANSITIONS.items():
        if source != str(CustodyStatus.CHECKED_IN):
            assert str(CustodyStatus.CHECKED_IN) not in targets


# ---------------------------------------------------------------------------
# Ticket codes
# ---------------------------------------------------------------------------
def test_ticket_code_shape_is_stable_and_readable():
    code = generate_ticket_code()

    assert code.startswith("VT-")
    assert len(code) == 16, code
    year_month, body = code.split("-")[1], code.split("-")[2]
    assert year_month.isdigit() and len(year_month) == 6
    assert body.isalnum() and body.isupper()


def test_ticket_code_avoids_ambiguous_characters():
    """Codes are read off screens and aloud, so 0/O and 1/I are excluded."""
    for _ in range(200):
        body = generate_ticket_code().split("-")[2]
        assert not (set(body) & set("0O1I")), body


def test_ticket_codes_are_unique():
    codes = {generate_ticket_code() for _ in range(500)}
    assert len(codes) == 500
