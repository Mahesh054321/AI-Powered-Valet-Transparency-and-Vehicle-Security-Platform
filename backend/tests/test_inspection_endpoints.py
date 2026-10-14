"""Endpoint tests for vehicle condition assessment.

These run against real PostgreSQL and the real vision pipeline: a photo set is
uploaded, stored, compared with OpenCV, and the findings are persisted and read
back. Nothing is stubbed, so a break in the wiring between the route, the
service and the pipeline shows up here.

The cases the module is required to cover:

* **valid image** - accepted, stored, referenced;
* **invalid image** - a rejected type or undecodable bytes, as 415/422;
* **missing image** - an absent file, and an absent custody session;
* **same image** - no findings;
* **different image** - findings, with evidence and a comparison image;
* **multiple angles** - each angle stored, matched and compared separately.

Plus the authorisation rules that make the verification workflow meaningful:
customers may read their own report but never capture one, and only a manager or
administrator may confirm or reject a finding.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from ai.synthetic import SyntheticDefect, generate_pair
from app.extensions import db
from models.damage_detection import DamageDetection
from models.enums import InspectionOutcome
from models.vehicle_inspection import VehicleInspection
from tests.workflow_helpers import bearer, make_user, make_vehicle, make_zone
from utils.security import Role

pytestmark = pytest.mark.postgres

TICKETS = "/api/v1/tickets"
INSPECTIONS = "/api/v1/inspections"
#: The unversioned alias must behave identically to the canonical path.
ALIAS = "/api/inspections"

#: Small enough to keep the suite quick, large enough to clear the pipeline's
#: minimum dimension.
TEST_WIDTH = 480
TEST_HEIGHT = 320


@pytest.fixture(autouse=True)
def _clean(pg_app):
    yield
    db.session.rollback()


def _photo(blob: bytes, name: str = "photo.png"):
    return (io.BytesIO(blob), name)


def _clean_pair(**kwargs):
    """A synthetic drop-off/pickup pair at test resolution."""
    return generate_pair(
        width=TEST_WIDTH, height=TEST_HEIGHT, camera_jitter=False, **kwargs
    )


def _scratched_pair():
    return generate_pair(
        defects=(
            SyntheticDefect(
                kind="scratch", centre=(0.62, 0.62), extent=(0.24, 0.035)
            ),
        ),
        width=TEST_WIDTH,
        height=TEST_HEIGHT,
        label="scratched",
    )


def _open_custody(client):
    """Issue a ticket and check the vehicle in, returning the custody session id."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    issued = client.post(
        TICKETS,
        json={"vehicle_id": str(vehicle.id)},
        headers=bearer(operator),
    )
    assert issued.status_code == 201, issued.get_json()
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    checked_in = client.post(
        f"{TICKETS}/{ticket_id}/check-in",
        json={"zone_id": str(zone.id)},
        headers=bearer(operator),
    )
    assert checked_in.status_code == 200, checked_in.get_json()

    fetched = client.get(f"{TICKETS}/{ticket_id}", headers=bearer(operator))
    session_id = fetched.get_json()["data"]["ticket"]["custody_session"]["id"]

    return {
        "operator": operator,
        "customer": customer,
        "vehicle": vehicle,
        "session_id": session_id,
        "ticket_id": ticket_id,
    }


def _capture(client, headers, session_id, stage, blobs, **form):
    data = {
        "custody_id": str(session_id),
        "images": [_photo(blob, f"photo-{index}.png") for index, blob in enumerate(blobs)],
    }
    data.update(form)
    return client.post(
        f"{INSPECTIONS}/{stage}",
        data=data,
        content_type="multipart/form-data",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Valid image
# ---------------------------------------------------------------------------
def test_a_valid_drop_off_photo_is_stored_and_referenced(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png],
        angle="left_side",
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()["data"]
    assert body["stage"] == "DROP_OFF"
    assert body["photo_count"] == 1
    assert body["photos"][0]["url"].startswith("/media/inspections/drop_off/")
    assert body["photos"][0]["angle"] == "LEFT_SIDE"
    assert len(body["photos"][0]["sha256"]) == 64


def test_the_stored_file_is_a_real_png_on_disk(client, pg_app):
    """The reference must point at bytes that actually exist."""
    from flask import current_app

    from services import media

    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png],
        angle="front",
    )

    photos = response.get_json()["data"]["photos"]
    with current_app.test_request_context():
        path = media.resolve_media_path(photos[0]["url"].removeprefix("/media/"))
    assert path.is_file()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_several_photos_can_be_uploaded_in_one_request(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png, pair.drop_off_png, pair.drop_off_png],
        angles=["front", "left_side", "rear"],
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()["data"]
    assert body["photo_count"] == 3
    assert [photo["angle"] for photo in body["photos"]] == [
        "FRONT",
        "LEFT_SIDE",
        "REAR",
    ]


def test_a_single_angle_may_be_applied_to_every_photo(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png, pair.drop_off_png],
        angle="rear",
    )

    assert response.status_code == 201
    assert [p["angle"] for p in response.get_json()["data"]["photos"]] == ["REAR", "REAR"]


def test_a_mismatched_angle_count_is_rejected_rather_than_guessed(client, pg_app):
    """Silently applying one angle to a mixed set would corrupt the comparison."""
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png, pair.drop_off_png],
        angles=["front"],
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["images"] == 2
    assert response.get_json()["error"]["details"]["angles"] == 1


def test_capturing_twice_appends_to_the_same_stage(client, pg_app):
    """Adding one more angle must not require re-uploading the rest."""
    context = _open_custody(client)
    pair = _clean_pair()
    headers = bearer(context["operator"])

    first = _capture(client, headers, context["session_id"], "dropoff",
                     [pair.drop_off_png], angle="front")
    second = _capture(client, headers, context["session_id"], "dropoff",
                      [pair.drop_off_png], angle="rear")

    assert first.status_code == second.status_code == 201
    assert first.get_json()["data"]["id"] == second.get_json()["data"]["id"]
    assert second.get_json()["data"]["photo_count"] == 2


# ---------------------------------------------------------------------------
# Invalid and missing image
# ---------------------------------------------------------------------------
def test_an_unsupported_file_type_is_rejected(client, pg_app):
    context = _open_custody(client)

    response = client.post(
        f"{INSPECTIONS}/dropoff",
        data={
            "custody_id": str(context["session_id"]),
            "images": [(io.BytesIO(b"MZ\x90\x00not an image"), "payload.exe")],
        },
        content_type="multipart/form-data",
        headers=bearer(context["operator"]),
    )

    assert response.status_code == 415
    assert "exe" in response.get_json()["error"]["message"]


def test_bytes_that_are_not_an_image_are_rejected(client, pg_app):
    """A .png name over non-image content must not be stored."""
    context = _open_custody(client)

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [b"definitely not png bytes at all, just text"],
        angle="front",
    )

    # The declared extension is png but the bytes are not, so the signature
    # check rejects it as an unsupported type before anything is decoded.
    assert response.status_code == 415


def test_a_crafted_header_cannot_exhaust_memory(client, pg_app):
    """28 bytes that make OpenCV attempt a multi-gigabyte allocation.

    Without the header probe in ``utils.image_probe`` this request kills the
    worker process rather than returning an error.
    """
    context = _open_custody(client)

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "dropoff",
        [b"\x89PNG\r\n\x1a\nbut not really a png"],
        angle="front",
    )

    assert response.status_code == 422
    assert response.get_json()["success"] is False


def test_a_request_with_no_image_at_all_is_rejected(client, pg_app):
    """The 'missing image' case."""
    context = _open_custody(client)

    response = client.post(
        f"{INSPECTIONS}/dropoff",
        data={"custody_id": str(context["session_id"])},
        content_type="multipart/form-data",
        headers=bearer(context["operator"]),
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "images"


def test_a_request_with_no_custody_id_is_rejected(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = client.post(
        f"{INSPECTIONS}/dropoff",
        data={"images": [_photo(pair.drop_off_png)]},
        content_type="multipart/form-data",
        headers=bearer(context["operator"]),
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"]["field"] == "custody_id"


def test_a_malformed_custody_id_is_a_422_not_a_500(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        "not-a-uuid", "dropoff", [pair.drop_off_png], angle="front"
    )

    assert response.status_code == 422


def test_an_unknown_custody_session_is_a_404(client, pg_app):
    import uuid

    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        str(uuid.uuid4()),
        "dropoff",
        [pair.drop_off_png],
        angle="front",
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Same image vs different image
# ---------------------------------------------------------------------------
def test_an_unchanged_vehicle_produces_no_findings(client, pg_app):
    """The 'same image' case, end to end through the API."""
    context = _open_custody(client)
    pair = _clean_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    assert pickup.status_code == 201, pickup.get_json()
    body = pickup.get_json()["data"]
    assert body["damage_count"] == 0
    assert body["detections"] == []
    assert body["outcome"] == str(InspectionOutcome.CLEAN)


def test_a_changed_vehicle_produces_a_finding_with_evidence(client, pg_app):
    """The 'different image' case: findings, evidence and a comparison image."""
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    assert pickup.status_code == 201, pickup.get_json()
    body = pickup.get_json()["data"]

    assert body["damage_count"] >= 1
    finding = body["detections"][0]
    assert finding["damage_type"] == "SCRATCH"
    assert finding["region"] != ""
    assert 0.0 <= finding["confidence"] <= 1.0
    assert finding["bounding_box"].keys() == {"x", "y", "width", "height"}
    assert finding["evidence_url"].startswith("/media/evidence/")
    assert finding["comparison_url"].startswith("/media/comparisons/")
    assert finding["detection_reasoning"]


def test_the_comparison_image_is_served_and_is_a_png(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    url = pickup.get_json()["data"]["detections"][0]["comparison_url"]
    served = client.get(url)

    assert served.status_code == 200
    assert served.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(served.data) > 1000


def test_findings_are_stored_as_unverified_potential_damage(client, pg_app):
    """An unreviewed guess must never present itself as a fact."""
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    body = pickup.get_json()["data"]
    finding = body["detections"][0]
    assert finding["verification_status"] == "UNVERIFIED"
    assert finding["is_verified"] is False
    assert finding["presentation_label"] == "Potential Damage"
    assert body["unverified_count"] == body["damage_count"]


def test_findings_are_recorded_as_classical_cv_not_a_trained_model(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    finding = pickup.get_json()["data"]["detections"][0]
    assert finding["method"] == "CLASSICAL_CV"
    assert finding["model_version"] is None
    assert finding["algorithm_params"]["pipeline"]["version"]


def test_a_pickup_with_no_drop_off_reports_that_nothing_was_compared(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()

    response = _capture(
        client,
        bearer(context["operator"]),
        context["session_id"],
        "pickup",
        [pair.pickup_png],
        angle="left_side",
    )

    assert response.status_code == 201
    body = response.get_json()["data"]
    assert body["damage_count"] == 0
    assert body["unmatched_angles"]
    assert "no photographs" in body["unmatched_angles"][0]


# ---------------------------------------------------------------------------
# Multiple angles
# ---------------------------------------------------------------------------
def test_each_angle_is_compared_against_its_own_drop_off_counterpart(client, pg_app):
    context = _open_custody(client)
    headers = bearer(context["operator"])

    front = generate_pair(
        defects=(SyntheticDefect(kind="scratch", centre=(0.5, 0.8), extent=(0.3, 0.04)),),
        angle="front", width=TEST_WIDTH, height=TEST_HEIGHT, label="front",
    )
    side = generate_pair(
        defects=(), angle="left_side", width=TEST_WIDTH, height=TEST_HEIGHT,
        camera_jitter=False, label="side",
    )

    _capture(client, headers, context["session_id"], "dropoff",
             [front.drop_off_png, side.drop_off_png], angles=["front", "left_side"])
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [front.pickup_png, side.pickup_png], angles=["front", "left_side"])

    assert pickup.status_code == 201, pickup.get_json()
    body = pickup.get_json()["data"]

    angles = {detection["angle"] for detection in body["detections"]}
    assert angles == {"FRONT"}
    assert body["damage_count"] == 1


def test_an_angle_photographed_only_at_pickup_is_reported_as_unmatched(client, pg_app):
    """It must not be silently compared against some other viewpoint."""
    context = _open_custody(client)
    headers = bearer(context["operator"])
    pair = _clean_pair()

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="front")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="rear")

    body = pickup.get_json()["data"]
    assert body["unmatched_angles"]
    # The note names the angle that had no counterpart. Nothing is compared
    # against the drop-off FRONT photo, so no findings are produced from it.
    assert any("'REAR'" in note and "not at drop-off" in note
               for note in body["unmatched_angles"])
    assert body["damage_count"] == 0
    assert body["detections"] == []


def test_the_report_lists_both_stages_and_every_photo(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    _capture(client, headers, context["session_id"], "pickup",
             [pair.pickup_png], angle="left_side")

    response = client.get(
        f"{INSPECTIONS}/{context['session_id']}", headers=headers
    )

    assert response.status_code == 200
    body = response.get_json()["data"]
    assert body["drop_off"]["stage"] == "DROP_OFF"
    assert body["pickup"]["stage"] == "PICKUP"
    assert body["comparison_ready"] is True
    assert body["vehicle"]["plate_number"]
    assert body["pickup"]["comparisons"]
    assert "trained damage classifier" in body["detector_disclaimer"]


def test_the_report_rebuilds_comparisons_on_a_fresh_request(client, pg_app):
    """The report must be complete on reload, not only in the creating response."""
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    _capture(client, headers, context["session_id"], "pickup",
             [pair.pickup_png], angle="left_side")

    body = client.get(
        f"{INSPECTIONS}/{context['session_id']}", headers=headers
    ).get_json()["data"]

    comparisons = body["pickup"]["comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["angle"] == "LEFT_SIDE"
    assert comparisons[0]["comparison_url"].startswith("/media/comparisons/")


def test_re_capturing_pickup_does_not_duplicate_findings(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    first = _capture(client, headers, context["session_id"], "pickup",
                     [pair.pickup_png], angle="left_side")
    second = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    assert first.get_json()["data"]["damage_count"] == second.get_json()["data"]["damage_count"]


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def test_a_customer_cannot_capture_an_inspection(client, pg_app):
    context = _open_custody(client)
    pair = _clean_pair()

    response = _capture(
        client,
        bearer(context["customer"]),
        context["session_id"],
        "dropoff",
        [pair.drop_off_png],
        angle="front",
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["details"]["role"] == "CUSTOMER"


def test_a_customer_can_read_their_own_condition_report(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")

    response = client.get(
        f"{INSPECTIONS}/{context['session_id']}", headers=bearer(context["customer"])
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["pickup"] is not None


def test_a_customer_cannot_read_someone_elses_report(client, pg_app):
    context = _open_custody(client)
    intruder = make_user()
    db.session.commit()

    response = client.get(
        f"{INSPECTIONS}/{context['session_id']}", headers=bearer(intruder)
    )

    # 404, not 403: do not confirm the session exists.
    assert response.status_code == 404


def test_an_anonymous_request_is_rejected(client, pg_app):
    context = _open_custody(client)

    assert client.get(f"{INSPECTIONS}/{context['session_id']}").status_code == 401


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def _make_finding(client, context):
    pair = _scratched_pair()
    headers = bearer(context["operator"])
    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, headers, context["session_id"], "pickup",
                      [pair.pickup_png], angle="left_side")
    return pickup.get_json()["data"]["detections"][0]


def test_a_manager_can_confirm_a_finding(client, pg_app):
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED", "notes": "Visible scuff, customer notified."},
        headers=bearer(manager),
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()["data"]
    assert body["verification_status"] == "CONFIRMED"
    assert body["is_verified"] is True
    assert body["presentation_label"] == "Confirmed Damage"
    assert body["verified_at"] is not None
    assert body["verification_notes"] == "Visible scuff, customer notified."


def test_a_manager_can_reject_a_finding(client, pg_app):
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "REJECTED", "notes": "Shadow from the door handle."},
        headers=bearer(manager),
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["presentation_label"] == "Dismissed - Not Damage"


def test_an_admin_can_verify_because_roles_inherit(client, pg_app):
    context = _open_custody(client)
    admin = make_user(Role.ADMIN)
    db.session.commit()
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(admin),
    )

    assert response.status_code == 200


def test_the_operator_who_photographed_the_car_cannot_verify(client, pg_app):
    """Verification is deliberately narrower than capture."""
    context = _open_custody(client)
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(context["operator"]),
    )

    assert response.status_code == 403
    assert "VALET_OPERATOR" in response.get_json()["error"]["message"]


def test_a_customer_cannot_verify_a_finding(client, pg_app):
    context = _open_custody(client)
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(context["customer"]),
    )

    assert response.status_code == 403


def test_an_unknown_finding_is_a_404(client, pg_app):
    import uuid

    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()

    response = client.patch(
        f"{INSPECTIONS}/detections/{uuid.uuid4()}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(manager),
    )

    assert response.status_code == 404


def test_an_invalid_verification_status_is_rejected(client, pg_app):
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "MAYBE"},
        headers=bearer(manager),
    )

    assert response.status_code == 422
    assert "allowed" in response.get_json()["error"]["details"]


def test_overlong_notes_are_rejected(client, pg_app):
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    response = client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED", "notes": "x" * 501},
        headers=bearer(manager),
    )

    assert response.status_code == 422


def test_a_finding_can_be_reopened(client, pg_app):
    """Reopening must clear the reviewer, or the CHECK constraint breaks."""
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)
    url = f"{INSPECTIONS}/detections/{finding['id']}/verification"

    assert client.patch(url, json={"status": "CONFIRMED"},
                        headers=bearer(manager)).status_code == 200

    reopened = client.patch(url, json={"status": "UNVERIFIED"}, headers=bearer(manager))

    assert reopened.status_code == 200
    body = reopened.get_json()["data"]
    assert body["verification_status"] == "UNVERIFIED"
    assert body["verified_by_id"] is None
    assert body["verified_at"] is None


# ---------------------------------------------------------------------------
# Outcome follows verification
# ---------------------------------------------------------------------------
def test_an_unverified_finding_leaves_the_inspection_pending_review(client, pg_app):
    """The column that a billing dispute would turn on must not overstate."""
    context = _open_custody(client)
    _make_finding(client, context)

    inspection = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == context["session_id"],
            VehicleInspection.stage == "PICKUP",
        )
    ).scalar_one()

    assert inspection.damage_count >= 1
    assert inspection.outcome == str(InspectionOutcome.PENDING_REVIEW)
    assert inspection.is_new_damage is False


def test_confirming_a_finding_marks_new_damage(client, pg_app):
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(manager),
    )

    inspection = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == context["session_id"],
            VehicleInspection.stage == "PICKUP",
        )
    ).scalar_one()

    assert inspection.outcome == str(InspectionOutcome.DAMAGE_FOUND)
    assert inspection.is_new_damage is True


def test_rejecting_every_finding_marks_the_inspection_clean(client, pg_app):
    """A human looked and disagreed: that is a clean result, not a pending one."""
    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "REJECTED", "notes": "Shadow."},
        headers=bearer(manager),
    )

    inspection = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == context["session_id"],
            VehicleInspection.stage == "PICKUP",
        )
    ).scalar_one()

    assert inspection.damage_count >= 1
    assert inspection.outcome == str(InspectionOutcome.CLEAN)
    assert inspection.is_new_damage is False


def test_the_report_flags_outstanding_review_work(client, pg_app):
    context = _open_custody(client)
    _make_finding(client, context)

    body = client.get(
        f"{INSPECTIONS}/{context['session_id']}",
        headers=bearer(context["operator"]),
    ).get_json()["data"]

    assert body["requires_review"] is True
    assert body["unverified_count"] >= 1
    assert body["confirmed_count"] == 0


def test_verification_is_written_to_the_audit_log(client, pg_app):
    from models.audit_log import AuditLog

    context = _open_custody(client)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    finding = _make_finding(client, context)

    client.patch(
        f"{INSPECTIONS}/detections/{finding['id']}/verification",
        json={"status": "CONFIRMED"},
        headers=bearer(manager),
    )

    entries = db.session.execute(
        select(AuditLog).where(AuditLog.action == "DAMAGE_VERIFIED")
    ).scalars().all()
    assert entries
    assert any(str(entry.entity_id) == finding["id"] for entry in entries)


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------
def test_the_demo_endpoint_runs_the_real_pipeline_on_synthetic_photos(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()  # a flushed-but-uncommitted user is rolled back by teardown

    response = client.post(
        f"{INSPECTIONS}/demo", json={"scenario": "multiple"}, headers=bearer(operator)
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()["data"]

    assert body["synthetic"] is True
    assert body["detected_count"] == body["planted_count"] == 3
    assert body["drop_off_url"].startswith("/media/demo/")
    assert "synthetic" in body["disclaimer"].lower()
    for finding in body["detections"]:
        assert finding["verification_status"] == "UNVERIFIED"
        assert finding["presentation_label"] == "Potential Damage"


def test_the_demo_clean_scenario_finds_nothing(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(
        f"{INSPECTIONS}/demo", json={"scenario": "clean"}, headers=bearer(operator)
    )

    assert response.status_code == 200
    body = response.get_json()["data"]
    assert body["detected_count"] == 0
    assert body["damage_detected"] is False


def test_the_demo_writes_no_inspection_rows(client, pg_app):
    """A demo must not pollute real custody history.

    Counted as a delta: the test database keeps committed rows from earlier
    tests, so global emptiness is not a meaningful assertion here.
    """
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()
    headers = bearer(operator)

    def counts():
        return (
            db.session.execute(select(VehicleInspection)).scalars().all(),
            db.session.execute(select(DamageDetection)).scalars().all(),
        )

    before_inspections, before_detections = counts()
    client.post(f"{INSPECTIONS}/demo", json={"scenario": "scratch"}, headers=headers)
    after_inspections, after_detections = counts()

    assert len(after_inspections) == len(before_inspections)
    assert len(after_detections) == len(before_detections)


def test_an_unknown_demo_scenario_lists_the_valid_ones(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.post(
        f"{INSPECTIONS}/demo",
        json={"scenario": "not-a-scenario"},
        headers=bearer(operator),
    )

    assert response.status_code == 422
    assert "clean" in response.get_json()["error"]["details"]["allowed"]


def test_the_demo_catalogue_is_listable(client, pg_app):
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    response = client.get(f"{INSPECTIONS}/demo/scenarios", headers=bearer(operator))

    assert response.status_code == 200
    names = [item["name"] for item in response.get_json()["data"]["scenarios"]]
    assert {"clean", "lighting_only", "scratch", "dent", "rust", "multiple"} <= set(names)


# ---------------------------------------------------------------------------
# The unversioned alias
# ---------------------------------------------------------------------------
def test_the_unversioned_alias_serves_the_same_report(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    headers = bearer(context["operator"])

    _capture(client, headers, context["session_id"], "dropoff",
             [pair.drop_off_png], angle="left_side")
    _capture(client, headers, context["session_id"], "pickup",
             [pair.pickup_png], angle="left_side")

    canonical = client.get(f"{INSPECTIONS}/{context['session_id']}", headers=headers)
    alias = client.get(f"{ALIAS}/{context['session_id']}", headers=headers)

    assert canonical.status_code == alias.status_code == 200
    assert (
        canonical.get_json()["data"]["pickup"]["damage_count"]
        == alias.get_json()["data"]["pickup"]["damage_count"]
    )


def test_the_alias_enforces_the_same_authorisation(client, pg_app):
    context = _open_custody(client)

    response = client.get(
        f"{ALIAS}/{context['session_id']}", headers=bearer(context["customer"])
    )

    # The customer owns this session, so the alias must allow it exactly as the
    # canonical path does.
    assert response.status_code == 200
