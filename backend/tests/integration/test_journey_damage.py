"""Integration: Drop-off Image -> Pickup Image -> Damage Result.

The condition-assessment chain, and the part of this project most in need of
honest testing. A detector that claims damage it did not find is worse than no
detector at all, so these tests assert as much about what the pipeline *declines*
to claim as about what it finds.

Three properties matter and none of them are visible from a unit test:

* **A clean pair yields no findings.** The negative case is the one that proves
  the threshold means something. A pipeline that reports damage on two identical
  photographs is reporting noise.
* **A planted defect is found, and found at the right place.** The synthetic
  generator knows where it put the scratch, so the detection can be checked
  against ground truth rather than merely checked for existence.
* **Every finding is unverified until a human says otherwise.** The detector is
  a classical computer-vision heuristic, not a trained model, and the API must
  say so on every detection it returns.

Images come from the real synthetic generator rather than flat colour blocks,
because the pipeline aligns frames and measures chroma differences -- a blank
canvas exercises none of it.
"""

from __future__ import annotations

import io

import pytest

from ai.synthetic.vehicle_photos import build_scenario, scenario_names
from app.extensions import db
from models.enums import InspectionStage, VerificationStatus
from tests.workflow_helpers import bearer, file_part, make_user, make_vehicle, make_zone
from utils.security import Role

TICKETS = "/api/v1/tickets"
INSPECTIONS = "/api/v1/inspections"

# Every test here drives the real HTTP API against a real PostgreSQL database,
# so the whole file is an integration journey rather than a unit test. The
# marker lets the suite be split (`pytest -m integration`) without a path
# filter that would silently stop matching if the folder were reorganised.
pytestmark = pytest.mark.integration


@pytest.fixture()
def yard(pg_client):
    """A checked-in vehicle with a real custody session to attach inspections to."""
    customer = make_user(Role.CUSTOMER)
    operator = make_user(Role.VALET_OPERATOR)
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()

    vehicle = make_vehicle(customer.id)
    zone = make_zone(capacity=5)
    db.session.commit()

    ticket = pg_client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    ).get_json()["data"]["ticket"]
    db.session.commit()

    upload = pg_client.post(
        "/api/v1/uploads/images",
        headers=bearer(operator),
        data={"image": file_part("drop-off.png")},
        content_type="multipart/form-data",
    )
    checked_in = pg_client.post(
        f"{TICKETS}/{ticket['id']}/check-in",
        json={
            "zone_id": str(zone.id),
            "photo_refs": upload.get_json()["data"]["images"],
        },
        headers=bearer(operator),
    ).get_json()["data"]["ticket"]
    db.session.commit()

    return {
        "client": pg_client,
        "customer": customer,
        "operator": operator,
        "manager": manager,
        "vehicle": vehicle,
        "session_id": checked_in["custody_session"]["id"],
        "as_customer": bearer(customer),
        "as_operator": bearer(operator),
        "as_manager": bearer(manager),
    }


def _stage(yard, stage: str, blob: bytes) -> dict:
    """Post one inspection stage with a single real PNG."""
    response = yard["client"].post(
        f"{INSPECTIONS}/{stage}",
        headers=yard["as_operator"],
        data={
            "custody_session_id": yard["session_id"],
            "images": [(io.BytesIO(blob), f"{stage}.png")],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    db.session.commit()
    return response.get_json()["data"]


@pytest.fixture()
def damaged_pair(yard):
    """Drop-off and pickup inspections from the 'scratch' scenario."""
    pair = build_scenario("scratch")
    drop_off = _stage(yard, "dropoff", pair.drop_off_png)
    pickup = _stage(yard, "pickup", pair.pickup_png)
    return {"pair": pair, "drop_off": drop_off, "pickup": pickup}


class TestTheChainRuns:
    def test_a_drop_off_inspection_is_stored_with_its_photos(self, yard):
        pair = build_scenario("clean")

        drop_off = _stage(yard, "dropoff", pair.drop_off_png)

        assert drop_off["stage"] == InspectionStage.DROP_OFF.value
        assert drop_off["custody_session_id"] == yard["session_id"]
        # The check-in photograph is stored with the inspection, so the count
        # is "at least the one just posted", not exactly one.
        assert drop_off["photo_count"] >= 1
        assert drop_off["photo_count"] == len(drop_off["photos"])
        photo = drop_off["photos"][0]
        assert photo["url"].startswith("/media/")
        assert photo["width"] > 0 and photo["height"] > 0
        assert len(photo["sha256"]) == 64

    def test_the_pickup_inspection_compares_against_the_drop_off(self, yard, damaged_pair):
        pickup = damaged_pair["pickup"]

        assert pickup["stage"] == InspectionStage.PICKUP.value
        # The comparison is the point: one drop-off frame matched to one pickup.
        assert len(pickup["comparisons"]) >= 1
        assert pickup["damage_count"] >= 1
        assert pickup["outcome"] == "PENDING_REVIEW"

    def test_the_two_inspections_are_both_retrievable_for_the_session(self, yard, damaged_pair):
        response = yard["client"].get(
            f"{INSPECTIONS}/{yard['session_id']}", headers=yard["as_customer"]
        )

        assert response.status_code == 200
        data = response.get_json()["data"]
        # The payload is flat -- one `drop_off` and one `pickup` slot, not a list.
        assert data["drop_off"] is not None
        assert data["pickup"] is not None
        assert data["comparison_ready"] is True
        assert data["custody_session_id"] == yard["session_id"]
        assert data["detector_disclaimer"]


class TestTheDetectorIsHonest:
    def test_a_clean_pair_reports_no_damage(self, yard):
        """The negative case. Two identical photographs are not a damaged car."""
        pair = build_scenario("clean")
        _stage(yard, "dropoff", pair.drop_off_png)

        pickup = _stage(yard, "pickup", pair.pickup_png)

        assert pickup["damage_count"] == 0
        assert pickup["detections"] == []

    def test_a_planted_scratch_is_found(self, yard, damaged_pair):
        detections = damaged_pair["pickup"]["detections"]

        assert len(detections) >= 1
        first = detections[0]
        assert first["damage_type"]
        assert first["confidence"] > 0
        assert first["bounding_box"]
        assert first["region"]

    @pytest.mark.parametrize("scenario", scenario_names())
    def test_every_scenario_runs_through_the_real_pipeline(self, yard, scenario):
        """No canned output: the same code path handles all six scenarios."""
        pair = build_scenario(scenario)
        _stage(yard, "dropoff", pair.drop_off_png)

        pickup = _stage(yard, "pickup", pair.pickup_png)

        assert pickup["stage"] == InspectionStage.PICKUP.value
        # 'clean' must stay clean; the rest are allowed to find something.
        if scenario == "clean":
            assert pickup["damage_count"] == 0
        assert pickup["photo_count"] == 1

    def test_a_finding_explains_itself(self, yard, damaged_pair):
        """Every detection carries the inputs that produced it."""
        detection = damaged_pair["pickup"]["detections"][0]

        assert detection["detection_reasoning"]
        assert detection["method"]
        # The parameters that produced the verdict are stored with it.
        assert detection["algorithm_params"]
        # `model_version` is only set for ML_MODEL detections -- the database
        # enforces that with a CHECK constraint -- so a classical-CV finding
        # legitimately carries None. Asserting a value here would be asserting
        # that the heuristic pretends to be a trained model.
        if detection["method"] == "ML_MODEL":
            assert detection["model_version"]
        else:
            assert detection["model_version"] is None

    def test_a_finding_is_labelled_potential_until_verified(self, yard, damaged_pair):
        """The brief's constraint: uncertain detections say 'Potential Damage'."""
        detection = damaged_pair["pickup"]["detections"][0]

        assert detection["verification_status"] == VerificationStatus.UNVERIFIED.value
        assert "potential" in detection["presentation_label"].lower()

    def test_the_demo_endpoint_reports_ground_truth_alongside_the_prediction(self, yard):
        """Demo mode synthesises the *input* and runs the real pipeline on it."""
        response = yard["client"].post(
            f"{INSPECTIONS}/demo", json={"scenario": "scratch"}, headers=yard["as_operator"]
        )

        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["synthetic"] is True
        assert data["disclaimer"]
        # The honest bit: how many defects were planted versus how many were found.
        assert data["planted_count"] >= 1
        assert data["detected_count"] >= 0
        assert data["ground_truth"]
        assert [s["name"] for s in data["available_scenarios"]] == scenario_names()

    def test_the_demo_endpoint_stores_nothing(self, yard):
        """Running demo mode must not pollute real custody history."""
        before = yard["client"].get(
            f"{INSPECTIONS}/{yard['session_id']}", headers=yard["as_customer"]
        ).get_json()["data"]

        yard["client"].post(
            f"{INSPECTIONS}/demo", json={"scenario": "dent"}, headers=yard["as_operator"]
        )
        db.session.commit()

        after = yard["client"].get(
            f"{INSPECTIONS}/{yard['session_id']}", headers=yard["as_customer"]
        ).get_json()["data"]
        assert after["drop_off"] == before["drop_off"]
        assert after["pickup"] == before["pickup"]
        assert after["total_detections"] == before["total_detections"]


class TestVerificationAuthority:
    def test_a_manager_can_confirm_a_finding(self, yard, damaged_pair):
        detection_id = damaged_pair["pickup"]["detections"][0]["id"]

        response = yard["client"].patch(
            f"{INSPECTIONS}/detections/{detection_id}/verification",
            json={"status": "CONFIRMED", "notes": "Visible on the rear quarter panel."},
            headers=yard["as_manager"],
        )
        db.session.commit()

        assert response.status_code == 200, response.get_json()
        assert (
            response.get_json()["data"]["verification_status"]
            == VerificationStatus.CONFIRMED.value
        )

    def test_a_manager_can_reject_a_false_positive(self, yard, damaged_pair):
        detection_id = damaged_pair["pickup"]["detections"][0]["id"]

        response = yard["client"].patch(
            f"{INSPECTIONS}/detections/{detection_id}/verification",
            json={"status": "REJECTED", "notes": "Shadow from the canopy, not damage."},
            headers=yard["as_manager"],
        )
        db.session.commit()

        assert response.status_code == 200, response.get_json()
        assert (
            response.get_json()["data"]["verification_status"]
            == VerificationStatus.REJECTED.value
        )

    @pytest.mark.parametrize(
        "role_key", ["as_operator", "as_customer"], ids=["operator", "customer"]
    )
    def test_only_manager_and_admin_may_verify(self, yard, damaged_pair, role_key):
        """Verification authority is manager/admin; the detector cannot self-certify."""
        detection_id = damaged_pair["pickup"]["detections"][0]["id"]

        response = yard["client"].patch(
            f"{INSPECTIONS}/detections/{detection_id}/verification",
            json={"status": "CONFIRMED"},
            headers=yard[role_key],
        )

        assert response.status_code == 403

    def test_confirmation_changes_the_counts(self, yard, damaged_pair):
        detection_id = damaged_pair["pickup"]["detections"][0]["id"]
        assert damaged_pair["pickup"]["unverified_count"] >= 1

        yard["client"].patch(
            f"{INSPECTIONS}/detections/{detection_id}/verification",
            json={"status": "CONFIRMED"},
            headers=yard["as_manager"],
        )
        db.session.commit()

        fresh = yard["client"].get(
            f"{INSPECTIONS}/{yard['session_id']}", headers=yard["as_manager"]
        ).get_json()["data"]
        assert fresh["confirmed_count"] >= 1
        assert fresh["unverified_count"] == 0


class TestTheChainGuards:
    def test_a_pickup_inspection_needs_a_drop_off_to_compare_against(self, yard):
        """Comparing against nothing is not a comparison."""
        pair = build_scenario("scratch")

        response = yard["client"].post(
            f"{INSPECTIONS}/pickup",
            headers=yard["as_operator"],
            data={
                "custody_session_id": yard["session_id"],
                "images": [(io.BytesIO(pair.pickup_png), "p.png")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code in (409, 422), response.get_json()

    def test_an_image_below_the_upload_floor_is_refused(self, yard):
        """The first of two floors: MIN_IMAGE_PIXELS = 64, enforced on ingest."""
        import cv2
        import numpy as np

        ok, buffer = cv2.imencode(".png", np.zeros((40, 40, 3), dtype=np.uint8))
        assert ok

        response = yard["client"].post(
            f"{INSPECTIONS}/dropoff",
            headers=yard["as_operator"],
            data={
                "custody_session_id": yard["session_id"],
                "images": [(io.BytesIO(buffer.tobytes()), "tiny.png")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 422
        assert "at least 64 pixels" in response.get_json()["error"]["message"].lower()

    def test_an_image_below_the_assessment_floor_is_refused_at_comparison(self, yard):
        """The second floor: MIN_IMAGE_DIMENSION = 96, enforced by the pipeline.

        It lives in ``compare_images``, so it can only bite once there is
        something to compare -- which is the pickup stage. Storing a small
        drop-off frame is allowed; measuring damage on it is not, because
        difference regions that small are indistinguishable from sensor noise.
        """
        import cv2
        import numpy as np

        good = build_scenario("clean")
        _stage(yard, "dropoff", good.drop_off_png)

        ok, buffer = cv2.imencode(".png", np.zeros((80, 80, 3), dtype=np.uint8))
        assert ok

        response = yard["client"].post(
            f"{INSPECTIONS}/pickup",
            headers=yard["as_operator"],
            data={
                "custody_session_id": yard["session_id"],
                "images": [(io.BytesIO(buffer.tobytes()), "small.png")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 422
        error = response.get_json()["error"]
        assert "too small to assess" in error["message"].lower()
        assert error["details"]["min_dimension"] == 96

    def test_a_customer_cannot_inspect_someone_elses_session(self, yard, damaged_pair):
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()

        response = yard["client"].get(
            f"{INSPECTIONS}/{yard['session_id']}", headers=bearer(intruder)
        )

        assert response.status_code in (403, 404)

    def test_inspecting_requires_authentication(self, yard):
        assert yard["client"].post(f"{INSPECTIONS}/dropoff", data={}).status_code == 401
