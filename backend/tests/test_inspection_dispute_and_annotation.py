"""Tests for dispute submission, recompare, and inspector annotations."""

from models.user import Role
from tests.test_inspection_endpoints import (
    _capture,
    _open_custody,
    _scratched_pair,
    bearer,
    make_user,
)


def test_customer_can_dispute_inspection(client, pg_app):
    context = _open_custody(client)
    pair = _scratched_pair()
    op_headers = bearer(context["operator"])
    cust_headers = bearer(context["customer"])

    _capture(
        client,
        op_headers,
        context["session_id"],
        "dropoff",
        [pair.drop_off_png],
        angle="left_side",
    )
    pickup = _capture(
        client,
        op_headers,
        context["session_id"],
        "pickup",
        [pair.pickup_png],
        angle="left_side",
    )
    assert pickup.status_code == 201

    det_id = pickup.get_json()["data"]["detections"][0]["id"]

    # Customer files dispute
    res = client.post(
        f"/api/v1/inspections/{context['session_id']}/dispute",
        headers=cust_headers,
        json={
            "reason": "This scratch was already present prior to valet drop-off.",
            "notes": "See attached timestamped photo from earlier today.",
            "disputed_detection_ids": [det_id],
        },
    )
    assert res.status_code == 200, res.get_json()
    duplicate = client.post(
        f"/api/v1/inspections/{context['session_id']}/dispute",
        headers=cust_headers,
        json={"reason": "Submitting a duplicate should be refused."},
    )
    assert duplicate.status_code == 409
    report = res.get_json()["data"]
    assert report["custody_session_id"] == context["session_id"]
    assert report["custody_status"] == "DISPUTED"

    # Verify detection has dispute note recorded
    pickup_dets = report["pickup"]["detections"]
    target_det = next(d for d in pickup_dets if d["id"] == det_id)
    assert "DISPUTED by customer" in (target_det.get("verification_notes") or "")


def test_only_manager_can_resolve_dispute_and_complete_custody(client, pg_app):
    from app.extensions import db
    from models.custody_session import CustodySession
    from models.valet_ticket import ValetTicket

    context = _open_custody(client)
    operator_headers = bearer(context["operator"])
    pair = _scratched_pair()
    _capture(
        client,
        operator_headers,
        context["session_id"],
        "dropoff",
        [pair.drop_off_png],
        angle="left_side",
    )
    _capture(
        client,
        operator_headers,
        context["session_id"],
        "pickup",
        [pair.pickup_png],
        angle="left_side",
    )

    dispute = client.post(
        f"/api/v1/inspections/{context['session_id']}/dispute",
        headers=bearer(context["customer"]),
        json={"reason": "Please review the pickup findings."},
    )
    assert dispute.status_code == 200, dispute.get_json()

    operator_attempt = client.post(
        f"/api/v1/inspections/{context['session_id']}/resolve-dispute",
        headers=operator_headers,
        json={"notes": "Reviewed."},
    )
    assert operator_attempt.status_code == 403
    operator_completion_attempt = client.post(
        f"/api/v1/tickets/{context['ticket_id']}/complete",
        headers=operator_headers,
        json={},
    )
    assert operator_completion_attempt.status_code == 403

    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    resolved = client.post(
        f"/api/v1/inspections/{context['session_id']}/resolve-dispute",
        headers=bearer(manager),
        json={"notes": "Reviewed with the customer and agreed the vehicle may be returned."},
    )

    assert resolved.status_code == 200, resolved.get_json()
    assert resolved.get_json()["data"]["custody_session"]["status"] == "CHECKED_OUT"
    session = db.session.get(CustodySession, context["session_id"])
    ticket = db.session.get(ValetTicket, context["ticket_id"])
    assert session.picked_up_at is not None
    assert session.condition_accepted is None
    assert session.zone.current_occupancy == 0
    assert ticket.status == "COMPLETED"


def test_completed_custody_cannot_be_disputed_again(client, pg_app):
    context = _open_custody(client)
    completed = client.post(
        f"/api/v1/tickets/{context['ticket_id']}/complete",
        headers=bearer(context["operator"]),
        json={},
    )
    assert completed.status_code == 200, completed.get_json()

    response = client.post(
        f"/api/v1/inspections/{context['session_id']}/dispute",
        headers=bearer(context["customer"]),
        json={"reason": "Please review the condition."},
    )

    assert response.status_code == 409
    assert "already complete" in response.get_json()["error"]["message"]


def test_manager_can_annotate_detection(client, pg_app):
    from app.extensions import db

    context = _open_custody(client)
    pair = _scratched_pair()
    op_headers = bearer(context["operator"])
    manager = make_user(Role.VALET_MANAGER)
    db.session.commit()
    mgr_headers = bearer(manager)

    _capture(client, op_headers, context["session_id"], "dropoff", [pair.drop_off_png], angle="left_side")
    pickup = _capture(client, op_headers, context["session_id"], "pickup", [pair.pickup_png], angle="left_side")
    det_id = pickup.get_json()["data"]["detections"][0]["id"]

    # Manager annotates notes and severity
    res = client.patch(
        f"/api/v1/inspections/detections/{det_id}/notes",
        headers=mgr_headers,
        json={
            "notes": "Reviewed with customer; cosmetic surface scratch only.",
            "severity": "COSMETIC",
        },
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["verification_notes"] == "Reviewed with customer; cosmetic surface scratch only."
    assert data["severity"] == "COSMETIC"


def test_operator_cannot_recompare_inspection(client, pg_app):
    context = _open_custody(client)
    op_headers = bearer(context["operator"])

    res = client.post(
        f"/api/v1/inspections/{context['session_id']}/recompare",
        headers=op_headers,
    )
    assert res.status_code == 403
