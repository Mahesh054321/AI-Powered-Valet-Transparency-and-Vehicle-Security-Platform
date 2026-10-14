"""Integration: Customer -> Check-in -> Custody -> Pickup.

The end-to-end journey the project exists to make transparent, driven entirely
through the HTTP API with real tokens against real PostgreSQL. No service is
called directly, because the point of this file is that the *seams* work: the
route layer's validation, the service layer's state machine, the audit writer and
the zone occupancy counter all have to agree.

Two properties are worth calling out because they are the ones a unit test
cannot see:

* **The ticket's projected status and its stored status diverge on purpose.**
  The database says ``ISSUED``/``ACTIVE``/``COMPLETED``; the API projects
  ``CREATED``/``IN_CUSTODY``/``READY_FOR_PICKUP``. Both are asserted here so a
  change to the vocabulary breaks a test rather than a client.
* **Zone occupancy is derived, not asserted once.** It goes 0 -> 1 on check-in
  and back to 0 on pickup, which is the invariant a double check-in or a missed
  completion would violate.

Every test commits after writing. The session-scoped app fixture shares one
``g`` across requests and the ORM session across tests; leaving a half-written
transaction behind turns the *next* request into a confusing 500 rather than a
clean failure.
"""

from __future__ import annotations

import uuid

import pytest

from app.extensions import db
from models.enums import CustodyStatus
from models.parking_zone import ParkingZone
from tests.workflow_helpers import bearer, file_part, make_user, make_vehicle, make_zone
from utils.security import Role

TICKETS = "/api/v1/tickets"
SESSIONS = "/api/v1/custody-sessions"
RETRIEVAL = "/api/v1/retrieval/request"

# Every test here drives the real HTTP API against a real PostgreSQL database,
# so the whole file is an integration journey rather than a unit test. The
# marker lets the suite be split (`pytest -m integration`) without a path
# filter that would silently stop matching if the folder were reorganised.
pytestmark = pytest.mark.integration


@pytest.fixture()
def yard(pg_client):
    """A customer, a valet, a manager, a vehicle and a zone -- the minimum cast."""
    customer = make_user(Role.CUSTOMER, full_name="Aisha Verma")
    operator = make_user(Role.VALET_OPERATOR, full_name="Suresh Kumar")
    manager = make_user(Role.VALET_MANAGER, full_name="Karan Malhotra")
    db.session.commit()

    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=5)
    db.session.commit()

    return {
        "client": pg_client,
        "customer": customer,
        "operator": operator,
        "manager": manager,
        "vehicle": vehicle,
        "zone": zone,
        "as_customer": bearer(customer),
        "as_operator": bearer(operator),
        "as_manager": bearer(manager),
    }


def _issue(yard) -> dict:
    """Issue a ticket and return its payload."""
    response = yard["client"].post(
        TICKETS,
        json={"vehicle_id": str(yard["vehicle"].id)},
        headers=yard["as_operator"],
    )
    assert response.status_code == 201, response.get_json()
    db.session.commit()
    return response.get_json()["data"]["ticket"]


def _check_in(yard, ticket_id: str, *, photo: bool = True) -> dict:
    """Check a vehicle in and return the ticket payload with its custody session."""
    payload: dict = {"zone_id": str(yard["zone"].id)}
    if photo:
        upload = yard["client"].post(
            "/api/v1/uploads/images",
            headers=yard["as_operator"],
            data={"image": file_part("drop-off.png")},
            content_type="multipart/form-data",
        )
        assert upload.status_code == 201, upload.get_json()
        payload["photo_refs"] = upload.get_json()["data"]["images"]

    response = yard["client"].post(
        f"{TICKETS}/{ticket_id}/check-in", json=payload, headers=yard["as_operator"]
    )
    assert response.status_code == 200, response.get_json()
    db.session.commit()
    return response.get_json()["data"]["ticket"]


def _occupancy(yard) -> int:
    db.session.expire_all()
    return int(db.session.get(ParkingZone, yard["zone"].id).current_occupancy)


# ---------------------------------------------------------------------------
# The journey, step by step
# ---------------------------------------------------------------------------
class TestTheJourney:
    def test_a_customer_can_register_and_sign_in(self, yard):
        """Step zero: the journey starts with a real account."""
        response = yard["client"].post(
            "/api/v1/auth/register",
            json={
                "email": f"journey-{uuid.uuid4().hex[:8]}@example.com",
                "password": "Str0ng#Passw0rd",
                "full_name": "Journey Customer",
            },
        )

        assert response.status_code == 201
        user = response.get_json()["data"]["user"]
        # Registration hands back a profile, not a token -- signing in is separate.
        assert user["role"] == Role.CUSTOMER.value
        assert "password" not in response.get_data(as_text=True)

    def test_an_issued_ticket_is_created_and_has_a_qr_code(self, yard):
        ticket = _issue(yard)

        assert ticket["status"] == "CREATED"
        assert ticket["stored_status"] == "ISSUED"
        assert ticket["ticket_code"]
        assert ticket["qr_payload"]
        assert ticket["qr_url"]
        assert ticket["custody_session"] is None, "no custody exists before check-in"

    def test_check_in_opens_custody_and_takes_a_zone_slot(self, yard):
        ticket = _issue(yard)
        assert _occupancy(yard) == 0

        checked_in = _check_in(yard, ticket["id"])

        session = checked_in["custody_session"]
        assert session["id"]
        assert session["status"] == CustodyStatus.PARKED.value
        assert session["is_open"] is True
        assert session["dropped_off_at"] is not None
        assert checked_in["status"] == "IN_CUSTODY"
        assert checked_in["stored_status"] == "ACTIVE"
        assert _occupancy(yard) == 1

    def test_the_customer_sees_their_own_ticket_progress(self, yard):
        ticket = _issue(yard)
        _check_in(yard, ticket["id"])

        listing = yard["client"].get(TICKETS, headers=yard["as_customer"])

        assert listing.status_code == 200
        items = listing.get_json()["data"]["items"]
        assert ticket["ticket_code"] in [row["ticket_code"] for row in items]

    def test_the_customer_requests_retrieval_and_gets_an_honest_estimate(self, yard):
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]

        response = yard["client"].post(
            RETRIEVAL, json={"custody_session_id": session_id}, headers=yard["as_customer"]
        )
        db.session.commit()

        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]
        assert data["previous_status"] == CustodyStatus.PARKED.value
        assert data["custody_status"] == CustodyStatus.RETRIEVAL_REQUESTED.value
        assert data["estimated_minutes"] > 0
        assert data["queue_position"] >= 1
        # The model is fitted on synthetic history and the payload says so.
        assert data["trained_on_synthetic_data"] is True
        assert data["disclaimer"]

    def test_pickup_closes_custody_and_frees_the_zone(self, yard):
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]
        assert _occupancy(yard) == 1

        yard["client"].post(
            RETRIEVAL, json={"custody_session_id": session_id}, headers=yard["as_customer"]
        )
        db.session.commit()

        moved = yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.READY_FOR_PICKUP.value},
            headers=yard["as_operator"],
        )
        assert moved.status_code == 200, moved.get_json()
        db.session.commit()

        completed = yard["client"].post(
            f"{TICKETS}/{ticket['id']}/complete", json={}, headers=yard["as_operator"]
        )
        db.session.commit()

        assert completed.status_code == 200, completed.get_json()
        final = completed.get_json()["data"]["ticket"]
        assert final["status"] == "COMPLETED"
        assert final["stored_status"] == "COMPLETED"
        assert final["custody_session"]["status"] == CustodyStatus.CHECKED_OUT.value
        assert final["custody_session"]["is_open"] is False
        assert final["actual_pickup_at"] is not None
        assert _occupancy(yard) == 0

    def test_the_whole_journey_is_recorded_in_order(self, yard):
        """The audit trail is the product; this asserts it survives the journey."""
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]
        yard["client"].post(
            RETRIEVAL, json={"custody_session_id": session_id}, headers=yard["as_customer"]
        )
        yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.READY_FOR_PICKUP.value},
            headers=yard["as_operator"],
        )
        yard["client"].post(
            f"{TICKETS}/{ticket['id']}/complete", json={}, headers=yard["as_operator"]
        )
        db.session.commit()

        timeline = yard["client"].get(
            f"/api/v1/audit/timeline/{session_id}", headers=yard["as_manager"]
        )

        assert timeline.status_code == 200, timeline.get_json()
        data = timeline.get_json()["data"]
        actions = [event["action"] for event in data["events"]]

        assert "TICKET_CREATED" in actions
        assert "VEHICLE_CHECKED_IN" in actions
        assert "CUSTODY_STARTED" in actions
        assert "PICKUP_REQUESTED" in actions
        assert "VEHICLE_PICKED_UP" in actions
        assert data["event_count"] == len(data["events"])
        # Events are ordered by the append-only sequence, not by wall-clock.
        seqs = [event["seq"] for event in data["events"]]
        assert seqs == sorted(seqs)


# ---------------------------------------------------------------------------
# What must NOT happen along the way
# ---------------------------------------------------------------------------
class TestTheJourneyGuards:
    def test_a_ticket_cannot_be_checked_in_twice(self, yard):
        ticket = _issue(yard)
        _check_in(yard, ticket["id"])

        second = yard["client"].post(
            f"{TICKETS}/{ticket['id']}/check-in",
            json={"zone_id": str(yard["zone"].id)},
            headers=yard["as_operator"],
        )

        assert second.status_code == 409
        # And the double check-in did not silently take a second slot.
        assert _occupancy(yard) == 1

    def test_an_illegal_transition_is_refused_with_the_allowed_set(self, yard):
        """A jump the state machine does not permit is a 409 carrying the legal set.

        409 rather than 422: the request is well formed and the caller is
        authorised, it just conflicts with where the session currently is. The
        payload names the permitted exits so a client can recover instead of
        guessing.
        """
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]

        # A freshly parked session cannot go back to CHECKED_IN.
        response = yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.CHECKED_IN.value},
            headers=yard["as_operator"],
        )

        assert response.status_code == 409
        details = response.get_json()["error"]["details"]
        assert details["from"] == CustodyStatus.PARKED.value
        assert CustodyStatus.CHECKED_IN.value not in details["allowed"]

    def test_checkout_is_not_reachable_through_the_transition_endpoint(self, yard):
        """Closing custody also settles ticket, occupancy and vehicle status.

        So it has its own endpoint, and the generic transition route refuses the
        shortcut with a 422 that says where to go instead.
        """
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]

        response = yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.CHECKED_OUT.value},
            headers=yard["as_operator"],
        )

        assert response.status_code == 422
        assert "completion" in response.get_json()["error"]["message"].lower()

    def test_a_customer_cannot_check_their_own_vehicle_in(self, yard):
        """Check-in is a staff act; a customer self-checking-in defeats custody."""
        ticket = _issue(yard)

        response = yard["client"].post(
            f"{TICKETS}/{ticket['id']}/check-in",
            json={"zone_id": str(yard["zone"].id)},
            headers=yard["as_customer"],
        )

        assert response.status_code == 403

    def test_a_customer_cannot_request_retrieval_for_someone_elses_session(self, yard):
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()

        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]

        response = yard["client"].post(
            RETRIEVAL, json={"custody_session_id": session_id}, headers=bearer(intruder)
        )

        assert response.status_code in (403, 404)

    def test_a_closed_custody_cannot_reopen(self, yard):
        ticket = _issue(yard)
        checked_in = _check_in(yard, ticket["id"])
        session_id = checked_in["custody_session"]["id"]
        yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.READY_FOR_PICKUP.value},
            headers=yard["as_operator"],
        )
        yard["client"].post(
            f"{TICKETS}/{ticket['id']}/complete", json={}, headers=yard["as_operator"]
        )
        db.session.commit()

        response = yard["client"].patch(
            f"{SESSIONS}/{session_id}",
            json={"status": CustodyStatus.PARKED.value},
            headers=yard["as_operator"],
        )

        # Closed custody is a conflict with the record's own state, not a
        # malformed request.
        assert response.status_code == 409
        assert "closed" in response.get_json()["error"]["message"].lower()

    def test_an_anonymous_caller_cannot_start_the_journey(self, yard):
        assert yard["client"].post(
            TICKETS, json={"vehicle_id": str(yard["vehicle"].id)}
        ).status_code == 401
