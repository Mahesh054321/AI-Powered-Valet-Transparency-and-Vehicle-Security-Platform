"""End-to-end tests for the valet ticket and custody workflow.

These walk the real check-in path against PostgreSQL: issue a ticket, hand the
vehicle over into a parking zone, move the custody session through its states,
and complete the ticket. The interesting assertions are the ones about
*side-effects* - zone occupancy, vehicle status and the projected ticket status
- because those are the states that go wrong silently.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from models.custody_session import CustodySession
from models.enums import CustodyStatus, TicketStatus, VehicleStatus, ZoneStatus
from models.vehicle_inspection import VehicleInspection
from services.media import storage_root
from tests.workflow_helpers import (
    bearer,
    file_part,
    make_user,
    make_vehicle,
    make_zone,
)
from utils.security import Role

pytestmark = pytest.mark.postgres

TICKETS = "/api/v1/tickets"
SESSIONS = "/api/v1/custody-sessions"


@pytest.fixture(autouse=True)
def _clean(pg_app):
    yield
    db.session.rollback()


def _issue(client, headers, vehicle_id, **extra):
    return client.post(TICKETS, json={"vehicle_id": str(vehicle_id), **extra}, headers=headers)


def _check_in(client, headers, ticket_id, zone_id, **extra):
    return client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone_id), **extra},
        headers=headers,
    )


def _session_id(client, headers, ticket_id):
    return client.get(f"{TICKETS}/{ticket_id}", headers=headers).get_json()["data"]["ticket"][
        "custody_session"
    ]["id"]


# ---------------------------------------------------------------------------
# Issuing a ticket
# ---------------------------------------------------------------------------
def test_issuing_a_ticket_generates_a_number_and_a_qr(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    response = _issue(client, bearer(operator), vehicle.id)

    assert response.status_code == 201, response.get_json()
    ticket = response.get_json()["data"]["ticket"]

    # Step 8: a unique ticket number.
    assert ticket["ticket_code"].startswith("VT-")
    assert len(ticket["ticket_code"]) == 16
    # Step 9: a QR code, rendered to storage and referenced, not inlined.
    assert ticket["qr_payload"] == f"VALET:{ticket['ticket_code']}"
    assert ticket["qr_code_path"].startswith("qr/")
    assert ticket["qr_url"] == f"/media/{ticket['qr_code_path']}"
    assert (storage_root() / ticket["qr_code_path"]).is_file()
    # The ticket carries everything the printed card needs.
    assert ticket["vehicle"]["plate_number"] == vehicle.plate_number
    assert ticket["customer"]["email"] == customer.email
    assert ticket["status"] == "CREATED"
    assert ticket["stored_status"] == TicketStatus.ISSUED.value
    assert ticket["custody_session"] is None, "custody has not started yet"


def test_ticket_numbers_are_unique_across_issues(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    codes = set()
    for _ in range(5):
        vehicle = make_vehicle(customer.id)
        db.session.commit()
        ticket = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]
        codes.add(ticket["ticket_code"])

    assert len(codes) == 5


def test_a_customer_cannot_issue_a_ticket(client, pg_app):
    """Check-in is a valet action; a customer must not be able to start one."""
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    response = _issue(client, bearer(customer), vehicle.id)

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"


def test_issuing_requires_a_vehicle(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(TICKETS, json={}, headers=bearer(operator))

    assert response.status_code == 422
    assert "vehicle_id" in response.get_json()["error"]["details"]["missing_fields"]


def test_a_vehicle_already_in_custody_cannot_be_ticketed_again(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id, status=VehicleStatus.IN_CUSTODY)
    zone = make_zone()
    db.session.commit()

    first = _issue(client, bearer(operator), vehicle.id)
    assert first.status_code == 201
    _check_in(client, bearer(operator), first.get_json()["data"]["ticket"]["id"], zone.id)

    second = _issue(client, bearer(operator), vehicle.id)

    assert second.status_code == 409
    assert "already in custody" in second.get_json()["error"]["message"]


def test_an_unstarted_ticket_is_not_issued_twice(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    assert _issue(client, bearer(operator), vehicle.id).status_code == 201
    again = _issue(client, bearer(operator), vehicle.id)

    assert again.status_code == 409
    assert "unstarted ticket" in again.get_json()["error"]["message"]


# ---------------------------------------------------------------------------
# Check-in: custody start, zone assignment, condition capture
# ---------------------------------------------------------------------------
def test_check_in_opens_custody_and_assigns_the_zone(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=4)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(
        client,
        bearer(operator),
        ticket_id,
        zone.id,
        odometer_at_drop_off=45_210,
        fuel_level_percent=60,
        pre_existing_damage_notes="Hairline scratch on the rear bumper.",
    )

    assert response.status_code == 200, response.get_json()
    ticket = response.get_json()["data"]["ticket"]

    assert ticket["status"] == "IN_CUSTODY"
    assert ticket["stored_status"] == TicketStatus.ACTIVE.value
    assert ticket["actual_drop_off_at"] is not None
    assert ticket["parking_zone"]["code"] == zone.code

    session = db.session.get(CustodySession, ticket["custody_session"]["id"])
    assert session.status == CustodyStatus.PARKED.value
    assert session.odometer_at_drop_off == 45_210
    assert session.fuel_level_percent == 60

    db.session.refresh(zone)
    assert zone.current_occupancy == 1, "the slot must be taken"

    db.session.refresh(vehicle)
    assert vehicle.status == VehicleStatus.IN_CUSTODY.value


def test_check_in_stores_the_drop_off_photo_references(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    upload = client.post(
        "/api/v1/uploads/images",
        # No explicit content_type: the test client generates the multipart
        # boundary itself, and overriding it makes werkzeug skip the body.
        data={"image": file_part("front.png"), "label": "front_left"},
        headers=bearer(operator),
    )
    assert upload.status_code == 201, upload.get_json()
    image = upload.get_json()["data"]["images"][0]
    assert image["mime_type"] == "image/png"
    assert image["width"] == 96 and image["height"] == 72
    assert len(image["sha256"]) == 64

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(client, bearer(operator), ticket_id, zone.id, photo_refs=[image])

    assert response.status_code == 200, response.get_json()
    session_id = response.get_json()["data"]["ticket"]["custody_session"]["id"]

    inspection = db.session.get(VehicleInspection, _inspection_id(session_id))
    assert inspection.photo_count == 1
    assert inspection.photo_refs[0]["sha256"] == image["sha256"]
    assert inspection.stage == "DROP_OFF"


def _inspection_id(session_id):
    from sqlalchemy import select

    return db.session.execute(
        select(VehicleInspection.id).where(VehicleInspection.custody_session_id == session_id)
    ).scalar_one()


def test_check_in_requires_a_zone(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = client.post(
        f"{TICKETS}/{ticket_id}/check-in", json={}, headers=bearer(operator)
    )

    assert response.status_code == 422
    assert "zone_id" in response.get_json()["error"]["details"]["missing_fields"]


def test_a_full_zone_is_refused(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=1)
    zone.current_occupancy = 1
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(client, bearer(operator), ticket_id, zone.id)

    assert response.status_code == 409
    assert "at capacity" in response.get_json()["error"]["message"]


def test_a_zone_under_maintenance_is_refused(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone(status=ZoneStatus.MAINTENANCE)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(client, bearer(operator), ticket_id, zone.id)

    assert response.status_code == 409
    assert "not accepting vehicles" in response.get_json()["error"]["message"]


def test_a_fuel_level_over_100_is_rejected(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(client, bearer(operator), ticket_id, zone.id, fuel_level_percent=140)

    assert response.status_code == 422


def test_check_in_cannot_run_twice(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    assert _check_in(client, bearer(operator), ticket_id, zone.id).status_code == 200

    again = _check_in(client, bearer(operator), ticket_id, zone.id)

    assert again.status_code == 409
    assert "cannot be checked in again" in again.get_json()["error"]["message"]


def test_a_customer_cannot_check_a_vehicle_in(client, pg_app):
    customer = make_user()
    operator = make_user(Role.VALET_OPERATOR)
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = _check_in(client, bearer(customer), ticket_id, zone.id)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Custody status
# ---------------------------------------------------------------------------
def test_the_custody_session_can_be_moved_through_valid_states(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, bearer(operator), ticket_id, zone.id)
    session_id = _session_id(client, bearer(operator), ticket_id)
    headers = bearer(operator)

    for target in ("RETRIEVAL_REQUESTED", "RETRIEVAL_IN_PROGRESS", "READY_FOR_PICKUP"):
        response = client.patch(
            f"{SESSIONS}/{session_id}", json={"status": target}, headers=headers
        )
        assert response.status_code == 200, (target, response.get_json())
        assert response.get_json()["data"]["custody_session"]["status"] == target

    # The ticket's projected status follows the session.
    ticket = client.get(f"{TICKETS}/{ticket_id}", headers=headers).get_json()["data"]["ticket"]
    assert ticket["status"] == "READY_FOR_PICKUP"
    assert ticket["stored_status"] == TicketStatus.ACTIVE.value


def test_an_illegal_transition_is_refused_with_the_allowed_set(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, bearer(operator), ticket_id, zone.id)
    session_id = _session_id(client, bearer(operator), ticket_id)

    # PARKED cannot jump straight back to CHECKED_IN.
    response = client.patch(
        f"{SESSIONS}/{session_id}", json={"status": "CHECKED_IN"}, headers=bearer(operator)
    )

    assert response.status_code == 409
    details = response.get_json()["error"]["details"]
    assert details["from"] == CustodyStatus.PARKED.value
    assert CustodyStatus.IN_TRANSIT.value in details["allowed"]


def test_closing_custody_through_the_status_endpoint_is_refused(client, pg_app):
    """Completion must settle the ticket, the zone slot and the vehicle too."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, bearer(operator), ticket_id, zone.id)
    session_id = _session_id(client, bearer(operator), ticket_id)

    response = client.patch(
        f"{SESSIONS}/{session_id}", json={"status": "CHECKED_OUT"}, headers=bearer(operator)
    )

    assert response.status_code == 422
    assert "completion endpoint" in response.get_json()["error"]["message"]


def test_an_unknown_custody_status_is_a_422(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, bearer(operator), ticket_id, zone.id)
    session_id = _session_id(client, bearer(operator), ticket_id)

    response = client.patch(
        f"{SESSIONS}/{session_id}", json={"status": "TELEPORTED"}, headers=bearer(operator)
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------
def test_completing_a_ticket_closes_custody_and_frees_the_slot(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=3)
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    # One kilometre is within the shunting tolerance, so this stays unflagged;
    # the flagging threshold has its own test below.
    _check_in(client, headers, ticket_id, zone.id, odometer_at_drop_off=10_000)
    session_id = _session_id(client, headers, ticket_id)

    response = client.post(
        f"{TICKETS}/{ticket_id}/complete",
        json={"odometer_at_pickup": 10_001, "condition_accepted": True},
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    ticket = response.get_json()["data"]["ticket"]
    assert ticket["status"] == "COMPLETED"
    assert ticket["actual_pickup_at"] is not None

    session = db.session.get(CustodySession, session_id)
    assert session.status == CustodyStatus.CHECKED_OUT.value
    assert session.picked_up_at is not None
    assert session.mileage_driven == 1
    assert session.condition_accepted is True

    db.session.refresh(zone)
    assert zone.current_occupancy == 0, "the slot must be released"

    db.session.refresh(vehicle)
    assert vehicle.status == VehicleStatus.REGISTERED.value


@pytest.mark.parametrize(
    ("kilometres", "expected"),
    [
        (0, VehicleStatus.REGISTERED),  # untouched
        (1, VehicleStatus.REGISTERED),  # shunting between bays
        (2, VehicleStatus.FLAGGED),  # just over the tolerance
        (37, VehicleStatus.FLAGGED),  # driven
    ],
)
def test_the_mileage_flagging_threshold(client, pg_app, kilometres, expected):
    """A car that moved while parked is a finding - but only past the tolerance.

    Flagging every kilometre would raise a finding for routine re-parking and
    the flag would stop meaning anything.
    """
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id, odometer_at_drop_off=20_000)
    client.post(
        f"{TICKETS}/{ticket_id}/complete",
        json={"odometer_at_pickup": 20_000 + kilometres},
        headers=headers,
    )

    db.session.refresh(vehicle)
    assert vehicle.status == expected.value


def test_mileage_driven_in_custody_flags_the_vehicle(client, pg_app):
    """A car driven a long way while parked is surfaced, not buried."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id, odometer_at_drop_off=20_000)
    client.post(
        f"{TICKETS}/{ticket_id}/complete", json={"odometer_at_pickup": 20_037}, headers=headers
    )

    db.session.refresh(vehicle)
    assert vehicle.status == VehicleStatus.FLAGGED.value


def test_a_pickup_odometer_below_the_drop_off_reading_is_rejected(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id, odometer_at_drop_off=50_000)

    response = client.post(
        f"{TICKETS}/{ticket_id}/complete", json={"odometer_at_pickup": 49_000}, headers=headers
    )

    assert response.status_code == 422
    assert "lower than the drop-off reading" in response.get_json()["error"]["message"]


def test_completing_without_checking_in_is_refused(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = client.post(f"{TICKETS}/{ticket_id}/complete", json={}, headers=bearer(operator))

    assert response.status_code == 409
    assert "no open custody session" in response.get_json()["error"]["message"]


def test_completing_twice_is_a_conflict(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id)
    first = client.post(f"{TICKETS}/{ticket_id}/complete", json={}, headers=headers)
    assert first.status_code == 200

    again = client.post(f"{TICKETS}/{ticket_id}/complete", json={}, headers=headers)

    assert again.status_code == 409


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
def test_cancelling_an_unstarted_ticket(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = client.post(
        f"{TICKETS}/{ticket_id}/cancel",
        json={"reason": "Customer changed their mind."},
        headers=bearer(operator),
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["ticket"]["status"] == "CANCELLED"

    # The vehicle can be ticketed again now.
    assert _issue(client, bearer(operator), vehicle.id).status_code == 201


def test_cancelling_an_open_custody_frees_the_slot(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=2)
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id)
    db.session.refresh(zone)
    assert zone.current_occupancy == 1

    response = client.post(
        f"{TICKETS}/{ticket_id}/cancel", json={"reason": "Wrong vehicle"}, headers=headers
    )

    assert response.status_code == 200
    db.session.refresh(zone)
    assert zone.current_occupancy == 0
    db.session.refresh(vehicle)
    assert vehicle.status == VehicleStatus.REGISTERED.value


def test_a_completed_ticket_cannot_be_cancelled(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id)
    client.post(f"{TICKETS}/{ticket_id}/complete", json={}, headers=headers)

    response = client.post(f"{TICKETS}/{ticket_id}/cancel", json={}, headers=headers)

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Retrieval and visibility
# ---------------------------------------------------------------------------
def test_a_customer_sees_only_their_own_tickets(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    mine, theirs = make_user(), make_user()
    my_vehicle = make_vehicle(mine.id)
    their_vehicle = make_vehicle(theirs.id)
    db.session.commit()

    mine_id = _issue(client, bearer(operator), my_vehicle.id).get_json()["data"]["ticket"]["id"]
    _issue(client, bearer(operator), their_vehicle.id)

    listing = client.get(TICKETS, headers=bearer(mine)).get_json()["data"]
    assert listing["pagination"]["total_items"] == 1
    assert listing["items"][0]["id"] == mine_id


def test_a_customer_cannot_read_someone_elses_ticket(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    mine, theirs = make_user(), make_user()
    their_vehicle = make_vehicle(theirs.id)
    db.session.commit()

    issued = _issue(client, bearer(operator), their_vehicle.id).get_json()["data"]["ticket"]
    response = client.get(f"{TICKETS}/{issued['id']}", headers=bearer(mine))

    # 404 rather than 403, so a ticket code cannot be probed for existence.
    assert response.status_code == 404


def test_a_customer_can_read_their_own_ticket(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    ticket_id = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]["id"]
    response = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(customer))

    assert response.status_code == 200
    assert response.get_json()["data"]["ticket"]["vehicle"]["plate_number"] == vehicle.plate_number


def test_listing_filters_on_the_projected_status(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    started = make_vehicle(customer.id)
    unstarted = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    _check_in(
        client,
        headers,
        _issue(client, headers, started.id).get_json()["data"]["ticket"]["id"],
        zone.id,
    )
    _issue(client, headers, unstarted.id)

    # Queried as the customer, not the operator: staff see every ticket in the
    # database, including ones other tests committed, so only an owner-scoped
    # listing has a predictable count.
    customer_headers = bearer(customer)
    in_custody = client.get(
        f"{TICKETS}?status=IN_CUSTODY", headers=customer_headers
    ).get_json()["data"]
    assert in_custody["pagination"]["total_items"] == 1
    assert in_custody["items"][0]["vehicle"]["plate_number"] == started.plate_number

    created = client.get(f"{TICKETS}?status=CREATED", headers=customer_headers).get_json()["data"]
    assert created["pagination"]["total_items"] == 1
    assert created["items"][0]["vehicle"]["plate_number"] == unstarted.plate_number


def test_an_unknown_status_filter_is_a_422(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.get(f"{TICKETS}?status=ISSUED", headers=bearer(operator))

    # `ISSUED` is the *stored* value; the API vocabulary is `CREATED`.
    assert response.status_code == 422
    assert "CREATED" in response.get_json()["error"]["details"]["allowed"]


def test_the_previous_sessions_endpoint_reports_a_completed_visit(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    headers = bearer(operator)
    ticket_id = _issue(client, headers, vehicle.id).get_json()["data"]["ticket"]["id"]
    _check_in(client, headers, ticket_id, zone.id)
    client.post(f"{TICKETS}/{ticket_id}/complete", json={}, headers=headers)

    body = client.get(
        f"/api/v1/vehicles/{vehicle.id}/sessions", headers=bearer(customer)
    ).get_json()["data"]

    assert body["total"] == 1
    entry = body["sessions"][0]
    assert entry["ticket"]["ticket_code"].startswith("VT-")
    assert entry["custody_session"]["status"] == CustodyStatus.CHECKED_OUT.value


# ---------------------------------------------------------------------------
# Media and lookups
# ---------------------------------------------------------------------------
def test_the_qr_image_is_served_over_media(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    ticket = _issue(client, bearer(operator), vehicle.id).get_json()["data"]["ticket"]
    response = client.get(ticket["qr_url"])

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    # A real PNG starts with its eight-byte signature.
    assert response.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_media_path_that_escapes_the_storage_root_is_refused(client, pg_app):
    """Traversal is refused, and no response ever carries file contents."""
    for path in ("../../etc/passwd", "qr/../../alembic.ini", "etc/passwd"):
        response = client.get(f"/media/{path}", follow_redirects=True)
        assert response.status_code == 404, path
        assert b"root:" not in response.data, path


def test_a_missing_media_file_is_a_404(client, pg_app):
    response = client.get("/media/qr/does-not-exist.png")

    assert response.status_code == 404


def test_the_customer_directory_is_staff_only(client, pg_app):
    customer = make_user()
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    assert client.get("/api/v1/customers", headers=bearer(customer)).status_code == 403

    body = client.get("/api/v1/customers", headers=bearer(operator)).get_json()["data"]
    assert any(entry["id"] == str(customer.id) for entry in body["items"])
    assert "password_hash" not in str(body)


def test_the_customer_directory_is_searchable(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    make_user(email="findable.customer@example.com", full_name="Findable Person")
    db.session.commit()

    body = client.get(
        "/api/v1/customers?search=findable", headers=bearer(operator)
    ).get_json()["data"]

    assert body["total"] >= 1
    assert any(entry["email"] == "findable.customer@example.com" for entry in body["items"])


def test_parking_zones_can_be_listed_and_filtered(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    make_zone()
    make_zone(status=ZoneStatus.RETIRED)
    db.session.commit()

    headers = bearer(operator)
    everything = client.get("/api/v1/parking-zones", headers=headers).get_json()["data"]
    parkable = client.get(
        "/api/v1/parking-zones?parkable_only=true", headers=headers
    ).get_json()["data"]

    assert parkable["total"] < everything["total"]
    assert all(zone["status"] in ("ACTIVE", "FULL") for zone in parkable["items"])


def test_a_non_image_upload_is_rejected(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(
        "/api/v1/uploads/images",
        data={"image": file_part("payload.jpg", b"#!/bin/sh\necho hi\n")},
        headers=bearer(operator),
    )

    # The extension says JPEG; the bytes say otherwise.
    assert response.status_code == 415
    assert response.get_json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_an_upload_without_a_file_is_a_422(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(
        "/api/v1/uploads/images", data={}, headers=bearer(operator),
    )

    assert response.status_code == 422


def test_ticket_endpoints_require_authentication(client, pg_app):
    for method, url in (
        ("get", TICKETS),
        ("post", TICKETS),
        ("get", f"{TICKETS}/00000000-0000-4000-8000-000000000000"),
        ("get", "/api/v1/parking-zones"),
    ):
        response = getattr(client, method)(url)
        assert response.status_code == 401, url
