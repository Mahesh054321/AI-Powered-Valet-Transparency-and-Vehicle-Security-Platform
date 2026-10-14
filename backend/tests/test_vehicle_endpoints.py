"""End-to-end tests for the vehicle API.

Run against real PostgreSQL: plate uniqueness is a partial unique index, so a
fake in-memory database would not exercise the rule that matters most here.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from models.enums import VehicleStatus
from models.vehicle import Vehicle
from tests.workflow_helpers import bearer, make_user, make_vehicle
from utils.security import Role

pytestmark = pytest.mark.postgres

BASE = "/api/v1/vehicles"


@pytest.fixture(autouse=True)
def _clean(pg_app):
    yield
    db.session.rollback()


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def test_a_customer_can_register_a_vehicle(client, pg_app):
    customer = make_user()
    db.session.commit()

    response = client.post(
        BASE,
        json={"plate_number": "UP 85 CA 4321", "make": "Tata", "model": "Nexon", "color": "Silver"},
        headers=bearer(customer),
    )

    assert response.status_code == 201
    body = response.get_json()["data"]["vehicle"]
    # The two columns serve different purposes: one is shown to a human, the
    # other is what uniqueness and lookup are decided on.
    assert body["plate_number"] == "UP 85 CA 4321"
    assert body["normalized_plate"] == "UP85CA4321"
    assert body["status"] == VehicleStatus.REGISTERED.value
    assert body["owner"]["email"] == customer.email


def test_registration_requires_a_plate_and_a_make(client, pg_app):
    customer = make_user()
    db.session.commit()

    response = client.post(BASE, json={"make": "Tata"}, headers=bearer(customer))

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "plate",
    ["", "ABC", "12345678", "UP85CA12", "!!@@##$$", "UP85CA12345678"],
)
def test_an_invalid_plate_is_rejected(client, pg_app, plate):
    """A plate is the identifier the whole platform keys on."""
    customer = make_user()
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": plate, "make": "Tata"}, headers=bearer(customer)
    )

    assert response.status_code == 422
    details = response.get_json()["error"]["details"]
    # An empty plate is caught as a missing field before format validation runs,
    # which reports `missing_fields` rather than `field`. Both are correct.
    assert details.get("field") == "plate_number" or "plate_number" in details.get(
        "missing_fields", []
    )


@pytest.mark.parametrize("plate", ["UP85CA1234", "DL01AB0001", "25BH0987AB", "MH12DE1234"])
def test_valid_plate_formats_are_accepted(client, pg_app, plate):
    """Standard and Bharat series, one- and two-letter RTO codes."""
    customer = make_user()
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": plate, "make": "Tata"}, headers=bearer(customer)
    )

    assert response.status_code == 201, response.get_json()


def test_the_same_plate_cannot_be_registered_twice(client, pg_app):
    customer = make_user()
    make_vehicle(customer.id, plate="UP85CA9999")
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": "UP85CA9999", "make": "Tata"}, headers=bearer(customer)
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "CONFLICT"


def test_plate_equivalence_ignores_spacing_and_case(client, pg_app):
    """``UP 85 CA 9998`` and ``up85ca9998`` are the same vehicle."""
    customer = make_user()
    make_vehicle(customer.id, plate="UP85CA9998")
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": "up 85 ca 9998", "make": "Tata"}, headers=bearer(customer)
    )

    assert response.status_code == 409


def test_an_archived_plate_can_be_re_registered(client, pg_app):
    """The unique index is partial, so archiving frees the plate."""
    customer = make_user()
    old = make_vehicle(customer.id, plate="UP85CA7777", status=VehicleStatus.ARCHIVED)
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": "UP85CA7777", "make": "Tata"}, headers=bearer(customer)
    )

    assert response.status_code == 201
    assert str(old.id) != response.get_json()["data"]["vehicle"]["id"]


def test_a_customer_cannot_register_a_vehicle_to_someone_else(client, pg_app):
    customer = make_user()
    other = make_user()
    db.session.commit()

    response = client.post(
        BASE,
        json={"plate_number": "UP85CA1111", "make": "Tata", "owner_id": str(other.id)},
        headers=bearer(customer),
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "owner_id"


def test_staff_may_register_on_a_customers_behalf(client, pg_app):
    """This is workflow step 2: the valet picks a customer, then their vehicle."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    db.session.commit()

    response = client.post(
        BASE,
        json={
            "plate_number": "UP85CA2222",
            "make": "Hyundai",
            "owner_id": str(customer.id),
        },
        headers=bearer(operator),
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["vehicle"]["owner"]["id"] == str(customer.id)


def test_staff_must_name_an_owner(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(
        BASE, json={"plate_number": "UP85CA3333", "make": "Tata"}, headers=bearer(operator)
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "owner_id"


# ---------------------------------------------------------------------------
# Ownership scoping
# ---------------------------------------------------------------------------
def test_a_customer_sees_only_their_own_vehicles(client, pg_app):
    mine, theirs = make_user(), make_user()
    make_vehicle(mine.id, plate="UP85CA4001")
    make_vehicle(theirs.id, plate="UP85CA4002")
    db.session.commit()

    body = client.get(BASE, headers=bearer(mine)).get_json()["data"]

    assert body["pagination"]["total_items"] == 1
    assert body["items"][0]["plate_number"] == "UP85CA4001"


def test_a_customer_cannot_read_someone_elses_vehicle(client, pg_app):
    mine, theirs = make_user(), make_user()
    foreign = make_vehicle(theirs.id, plate="UP85CA4003")
    db.session.commit()

    response = client.get(f"{BASE}/{foreign.id}", headers=bearer(mine))

    # 404 rather than 403: confirming the vehicle exists would leak that the
    # plate is registered to another customer.
    assert response.status_code == 404


def test_staff_can_read_any_vehicle(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA4004")
    db.session.commit()

    response = client.get(f"{BASE}/{vehicle.id}", headers=bearer(operator))

    assert response.status_code == 200
    assert response.get_json()["data"]["vehicle"]["owner"]["email"] == customer.email


def test_an_unknown_vehicle_id_is_a_404(client, pg_app):
    customer = make_user()
    db.session.commit()

    response = client.get(f"{BASE}/00000000-0000-4000-8000-000000000000", headers=bearer(customer))

    assert response.status_code == 404


def test_a_malformed_vehicle_id_is_a_422_not_a_500(client, pg_app):
    """Regression: an unvalidated `uuid.UUID()` here used to raise and 500."""
    customer = make_user()
    db.session.commit()

    response = client.get(f"{BASE}/not-a-uuid", headers=bearer(customer))

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.get_json()["error"]["details"]["field"] == "vehicle_id"


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def test_a_customer_can_edit_their_vehicle(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA5001")
    db.session.commit()

    response = client.patch(
        f"{BASE}/{vehicle.id}",
        json={"color": "Midnight Blue", "notes": "Roof rack fitted"},
        headers=bearer(customer),
    )

    assert response.status_code == 200
    updated = response.get_json()["data"]["vehicle"]
    assert updated["color"] == "Midnight Blue"
    assert updated["notes"] == "Roof rack fitted"


def test_editing_the_plate_renormalises_and_rechecks_uniqueness(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA5002")
    make_vehicle(customer.id, plate="UP85CA5003")
    db.session.commit()

    taken = client.patch(
        f"{BASE}/{vehicle.id}", json={"plate_number": "UP85CA5003"}, headers=bearer(customer)
    )
    assert taken.status_code == 409

    free = client.patch(
        f"{BASE}/{vehicle.id}", json={"plate_number": "UP 85 CA 5004"}, headers=bearer(customer)
    )
    assert free.status_code == 200
    updated = free.get_json()["data"]["vehicle"]
    assert updated["normalized_plate"] == "UP85CA5004"
    assert updated["plate_number"] == "UP 85 CA 5004"


def test_a_customer_cannot_edit_someone_elses_vehicle(client, pg_app):
    mine, theirs = make_user(), make_user()
    foreign = make_vehicle(theirs.id, plate="UP85CA5005")
    db.session.commit()

    response = client.patch(
        f"{BASE}/{foreign.id}", json={"color": "Red"}, headers=bearer(mine)
    )

    assert response.status_code == 404
    db.session.refresh(foreign)
    assert foreign.color == "Silver"


def test_the_ownership_of_a_vehicle_cannot_be_reassigned(client, pg_app):
    """Moving a vehicle would rewrite whose custody history it belongs to."""
    customer, other = make_user(), make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA5006")
    db.session.commit()

    response = client.patch(
        f"{BASE}/{vehicle.id}", json={"owner_id": str(other.id)}, headers=bearer(customer)
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "owner_id"


def test_in_custody_status_cannot_be_set_by_hand(client, pg_app):
    """That state is derived from an open custody session."""
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA5007")
    db.session.commit()

    response = client.patch(
        f"{BASE}/{vehicle.id}", json={"status": "IN_CUSTODY"}, headers=bearer(customer)
    )

    assert response.status_code == 422
    assert "check-in workflow" in response.get_json()["error"]["message"]


def test_an_empty_edit_is_rejected(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA5008")
    db.session.commit()

    response = client.patch(f"{BASE}/{vehicle.id}", json={}, headers=bearer(customer))

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------
def test_deleting_archives_rather_than_removing(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA6001")
    vehicle_id = vehicle.id
    db.session.commit()

    response = client.delete(f"{BASE}/{vehicle_id}", headers=bearer(customer))
    assert response.status_code == 204

    still_there = db.session.get(Vehicle, vehicle_id)
    assert still_there is not None, "the row must survive for the audit trail"
    assert still_there.status == VehicleStatus.ARCHIVED.value

    listing = client.get(BASE, headers=bearer(customer)).get_json()["data"]
    assert listing["pagination"]["total_items"] == 0

    including = client.get(f"{BASE}?include_archived=true", headers=bearer(customer)).get_json()
    assert including["data"]["pagination"]["total_items"] == 1


def test_archiving_twice_is_a_conflict(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA6002", status=VehicleStatus.ARCHIVED)
    db.session.commit()

    response = client.delete(f"{BASE}/{vehicle.id}", headers=bearer(customer))

    assert response.status_code == 409


def test_a_customer_cannot_archive_someone_elses_vehicle(client, pg_app):
    mine, theirs = make_user(), make_user()
    foreign = make_vehicle(theirs.id, plate="UP85CA6003")
    db.session.commit()

    response = client.delete(f"{BASE}/{foreign.id}", headers=bearer(mine))

    assert response.status_code == 404
    db.session.refresh(foreign)
    assert foreign.status == VehicleStatus.REGISTERED.value


# ---------------------------------------------------------------------------
# Listing and sessions
# ---------------------------------------------------------------------------
def test_listing_paginates(client, pg_app):
    customer = make_user()
    for index in range(5):
        make_vehicle(customer.id, plate=f"UP85CA70{index:02d}")
    db.session.commit()

    body = client.get(f"{BASE}?page=1&page_size=2", headers=bearer(customer)).get_json()["data"]

    assert len(body["items"]) == 2
    assert body["pagination"]["total_items"] == 5
    assert body["pagination"]["total_pages"] == 3
    assert body["pagination"]["has_next"] is True


def test_listing_searches_by_plate_and_make(client, pg_app):
    customer = make_user()
    make_vehicle(customer.id, plate="UP85CA8001")
    second = make_vehicle(customer.id, plate="UP85CA8002")
    second.make = "Mahindra"
    db.session.commit()

    by_plate = client.get(f"{BASE}?search=8001", headers=bearer(customer)).get_json()["data"]
    assert by_plate["pagination"]["total_items"] == 1

    by_make = client.get(f"{BASE}?search=mahindra", headers=bearer(customer)).get_json()["data"]
    assert by_make["pagination"]["total_items"] == 1
    assert by_make["items"][0]["plate_number"] == "UP85CA8002"


def test_the_sessions_endpoint_starts_empty(client, pg_app):
    customer = make_user()
    vehicle = make_vehicle(customer.id, plate="UP85CA8003")
    db.session.commit()

    body = client.get(f"{BASE}/{vehicle.id}/sessions", headers=bearer(customer)).get_json()["data"]

    assert body["sessions"] == []
    assert body["total"] == 0
    assert body["vehicle"]["plate_number"] == "UP85CA8003"


def test_vehicles_require_authentication(client, pg_app):
    for method, url in (
        ("get", BASE),
        ("post", BASE),
        ("get", f"{BASE}/00000000-0000-4000-8000-000000000000"),
    ):
        response = getattr(client, method)(url)
        assert response.status_code == 401, url
        assert response.get_json()["error"]["code"] == "AUTHENTICATION_ERROR"
