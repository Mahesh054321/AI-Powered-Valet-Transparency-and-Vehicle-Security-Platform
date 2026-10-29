"""Tests for the customer dashboard and the pickup-request workflow.

The dashboard is a composition of other services, so the interesting failures are
not "does it return JSON" but:

* **does an absent thing read as absent?** A customer with no car in custody is a
  normal condition. A section that silently dropped its key would render as
  blank, and blank reads as "nothing wrong" rather than "nothing known", so every
  section is asserted to carry a ``state``.
* **does ownership still hold through the composition?** The dashboard calls into
  ``tracking_view``, ``get_inspection_report`` and ``list_alerts``, each of which
  enforces access on its own. Reusing them is only safe if a customer still
  cannot see another customer's vehicle, which is asserted directly.
* **does the request workflow actually do all five things?** The status change,
  the audit entry, the prediction and the queue entry are each checked
  separately, because a workflow that did three of the four would still return
  a 201.

Runs against real PostgreSQL. Nothing is stubbed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from models.audit_log import AuditAction, AuditLog
from models.custody_session import CustodySession
from models.enums import CustodyStatus, PredictionStatus
from models.retrieval_prediction import RetrievalPrediction
from models.valet_ticket import ValetTicket
from services.retrieval import retrieval_model
from tests.workflow_helpers import bearer, make_user, make_vehicle, make_zone
from utils.security import Role

DASHBOARD = "/api/v1/customers/me/dashboard"
REQUEST = "/api/v1/retrieval/request"
QUEUE = "/api/v1/retrieval/queue"
CUSTODY = "/api/v1/custody-sessions"

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def trained_artifact(pg_app, tmp_path_factory):
    """Train a small retrieval model so these tests run on any checkout.

    The artifact is not committed, so without this the estimate half of the
    workflow would be untestable on a fresh clone.
    """
    from ai.retrieval.dataset import generate_history
    from ai.retrieval.model import RetrievalModel

    path = tmp_path_factory.mktemp("retrieval") / "retrieval_model.json"
    RetrievalModel(alpha=1.0).fit(generate_history(episodes=1200, seed=20260909)).save(path)

    previous = pg_app.config.get("RETRIEVAL_MODEL_PATH")
    pg_app.config["RETRIEVAL_MODEL_PATH"] = path
    retrieval_model.reset()
    yield path
    pg_app.config["RETRIEVAL_MODEL_PATH"] = previous
    retrieval_model.reset()


def _checked_in(client, *, customer=None, zone=None):
    """A real vehicle in custody, created through the ticket API."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = customer or make_user()
    vehicle = make_vehicle(customer.id)
    zone = zone or make_zone()
    db.session.commit()

    issued = client.post(
        "/api/v1/tickets",
        json={"vehicle_id": str(vehicle.id)},
        headers=bearer(operator),
    )
    assert issued.status_code == 201, issued.get_json()
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    # dropped_off_at defaults to now; setting it explicitly keeps the schema's
    # "retrieval cannot precede drop-off" check satisfiable later.
    dropped_off_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    ticket = db.session.get(ValetTicket, uuid.UUID(ticket_id))
    session = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        status=str(CustodyStatus.PARKED),
        dropped_off_at=dropped_off_at,
    )
    db.session.add(session)
    db.session.commit()
    return {
        "operator": operator,
        "customer": customer,
        "vehicle": vehicle,
        "zone": zone,
        "custody_id": str(session.id),
    }


class TestDashboardComposition:
    def test_every_section_carries_a_state(self, pg_client, pg_app):
        """A section without a state would render as blank, which reads as fine."""
        context = _checked_in(pg_client)
        response = pg_client.get(DASHBOARD, headers=bearer(context["customer"]))
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]

        assert data["has_active_custody"] is True
        for name in (
            "custody",
            "vehicle",
            "zone",
            "location",
            "security",
            "condition",
            "retrieval",
            "actions",
        ):
            assert name in data, f"missing section {name}"
            assert data[name].get("state") in ("ok", "empty", "unavailable"), (
                f"section {name} has no usable state: {data[name].get('state')!r}"
            )

    def test_the_cards_the_brief_names_are_populated(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        data = pg_client.get(
            DASHBOARD, headers=bearer(context["customer"])
        ).get_json()["data"]

        # Vehicle Status
        assert data["vehicle"]["plate_number"] == context["vehicle"].plate_number
        # Current Location + Parking Zone
        assert data["zone"]["code"] == context["zone"].code
        # Check-in time
        assert data["custody"]["checked_in_at"] is not None
        # Custody status
        assert data["custody"]["status"] == CustodyStatus.PARKED.value
        # Security Status
        assert data["security"]["state"] == "ok"
        assert data["security"]["status"] in ("SECURE", "ATTENTION", "BREACH", "UNKNOWN")

    def test_a_customer_with_nothing_in_custody_gets_empty_not_an_error(
        self, pg_client, pg_app
    ):
        customer = make_user()
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(customer))
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]

        assert data["has_active_custody"] is False
        # Empty is a real answer from the database, not a placeholder: the UI
        # must be able to tell "no vehicle" from "the request failed".
        assert data["custody"]["state"] == "empty"
        assert data["custody"]["reason"]

    def test_location_is_empty_when_no_fix_has_been_recorded(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        data = pg_client.get(
            DASHBOARD, headers=bearer(context["customer"])
        ).get_json()["data"]

        assert data["location"]["state"] == "empty"
        assert "No GPS fix" in data["location"]["reason"]

    def test_condition_is_empty_before_any_inspection(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        data = pg_client.get(
            DASHBOARD, headers=bearer(context["customer"])
        ).get_json()["data"]

        assert data["condition"]["state"] == "empty"

    def test_the_request_button_is_enabled_only_when_the_state_machine_allows_it(
        self, pg_client, pg_app
    ):
        context = _checked_in(pg_client)
        data = pg_client.get(
            DASHBOARD, headers=bearer(context["customer"])
        ).get_json()["data"]

        assert data["actions"]["can_request"] is True

        pg_client.post(
            REQUEST,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["customer"]),
        )
        after = pg_client.get(
            DASHBOARD, headers=bearer(context["customer"])
        ).get_json()["data"]

        # The button greys out for the same reason the backend would refuse, so
        # the customer is never shown an enabled button that 409s.
        assert after["actions"]["can_request"] is False
        assert after["actions"]["reason"]

    def test_anonymous_is_rejected(self, pg_client, pg_app):
        assert pg_client.get(DASHBOARD).status_code == 401


class TestDashboardOwnership:
    def test_a_customer_cannot_see_another_customers_dashboard(
        self, pg_client, pg_app
    ):
        """The composition reuses each service's access rules; this proves it.

        If any section queried the tables directly instead of going through
        ``tracking_view`` / ``get_inspection_report``, this is where it would
        leak.
        """
        mine = _checked_in(pg_client)
        theirs = _checked_in(pg_client)

        data = pg_client.get(DASHBOARD, headers=bearer(mine["customer"])).get_json()["data"]

        assert data["vehicle"]["plate_number"] == mine["vehicle"].plate_number
        assert data["custody_session_id"] == mine["custody_id"]
        assert data["custody_session_id"] != theirs["custody_id"]

    def test_a_customer_cannot_read_the_staff_queue(self, pg_client, pg_app):
        """The queue spans every customer's vehicles."""
        context = _checked_in(pg_client)
        response = pg_client.get(QUEUE, headers=bearer(context["customer"]))
        # 403 rather than 404: the route exists and the role guard is what
        # refused, which is the honest answer for a known endpoint.
        assert response.status_code == 403

    def test_staff_may_read_the_queue(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        response = pg_client.get(QUEUE, headers=bearer(operator))
        assert response.status_code == 200
        assert "items" in response.get_json()["data"]


@pytest.mark.usefixtures("trained_artifact")
class TestRequestVehicleWorkflow:
    def test_it_does_all_five_things(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        customer_headers = bearer(context["customer"])

        response = pg_client.post(
            REQUEST,
            json={"custody_session_id": context["custody_id"]},
            headers=customer_headers,
        )
        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]

        # 1. the request was recorded
        assert data["custody_session_id"] == context["custody_id"]
        # 2. the status changed
        assert data["previous_status"] == CustodyStatus.PARKED.value
        assert data["custody_status"] == CustodyStatus.RETRIEVAL_REQUESTED.value
        # 3. a prediction was generated
        assert data["estimate_available"] is True
        assert data["estimated_minutes"] >= 1
        assert data["estimated_retrieval_time"] is not None
        # 4. it reached the valet queue
        assert data["valet_notification"]["queued"] is True
        # 5. the estimate is shown
        assert data["recommended_dispatch"]

        db.session.expire_all()
        session = db.session.get(CustodySession, uuid.UUID(context["custody_id"]))
        assert session.status == CustodyStatus.RETRIEVAL_REQUESTED.value
        assert session.retrieval_requested_at is not None

    def test_it_audits_the_customers_act_of_asking(self, pg_client, pg_app):
        """Distinct from the status-change entry: this is who asked, and when."""
        context = _checked_in(pg_client)
        pg_client.post(
            REQUEST,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["customer"]),
        )
        db.session.expire_all()

        entries = db.session.execute(
            AuditLog.__table__.select().where(
                AuditLog.action == AuditAction.PICKUP_REQUESTED.value,
                AuditLog.entity_id == context["custody_id"],
            )
        ).all()
        assert len(entries) == 1
        assert str(entries[0].actor_id) == str(context["customer"].id)

    def test_it_appears_on_the_valet_queue(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        pg_client.post(
            REQUEST,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["customer"]),
        )

        queue = pg_client.get(QUEUE, headers=bearer(context["operator"])).get_json()["data"]
        match = [
            item
            for item in queue["items"]
            if item["custody_session_id"] == context["custody_id"]
        ]
        assert len(match) == 1
        assert match[0]["vehicle"]["plate_number"] == context["vehicle"].plate_number
        assert match[0]["estimated_minutes"] >= 1
        # Stated so the UI can label itself truthfully rather than implying push.
        assert queue["delivery"] == "polled"
        assert "poll" in queue["delivery_note"]

    def test_the_dashboard_shows_the_estimate_afterwards(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        headers = bearer(context["customer"])

        pg_client.post(
            REQUEST, json={"custody_session_id": context["custody_id"]}, headers=headers
        )
        data = pg_client.get(DASHBOARD, headers=headers).get_json()["data"]

        assert data["custody"]["status"] == CustodyStatus.RETRIEVAL_REQUESTED.value
        assert data["retrieval"]["state"] == "ok"
        assert data["retrieval"]["estimated_minutes"] >= 1
        # The synthetic-data disclosure has to survive into the dashboard, which
        # is the most screenshot-able surface in the product.
        assert data["retrieval"]["trained_on_synthetic_data"] is True
        assert data["retrieval"]["disclaimer"]

    def test_the_prediction_is_committed_and_readable(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        pg_client.post(
            REQUEST,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["customer"]),
        )
        from sqlalchemy import text

        with db.engine.connect() as connection:
            found = connection.execute(
                text(
                    "SELECT count(*) FROM retrieval_predictions "
                    "WHERE custody_session_id = :id"
                ),
                {"id": context["custody_id"]},
            ).scalar()
        assert found >= 1, "the estimate was flushed but never committed"

    def test_requesting_twice_is_refused_by_the_state_machine(self, pg_client, pg_app):
        context = _checked_in(pg_client)
        headers = bearer(context["customer"])
        body = {"custody_session_id": context["custody_id"]}

        assert pg_client.post(REQUEST, json=body, headers=headers).status_code == 201
        second = pg_client.post(REQUEST, json=body, headers=headers)
        assert second.status_code == 409

    def test_a_customer_cannot_reuse_it_for_a_valet_transition(self, pg_client, pg_app):
        """Relaxing the guard for retrieval must not relax it for anything else."""
        context = _checked_in(pg_client)
        response = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": CustodyStatus.READY_FOR_PICKUP.value},
            headers=bearer(context["customer"]),
        )
        assert response.status_code == 403

    def test_a_customer_cannot_request_someone_elses_vehicle(self, pg_client, pg_app):
        mine = _checked_in(pg_client)
        theirs = _checked_in(pg_client)

        response = pg_client.post(
            REQUEST,
            json={"custody_session_id": theirs["custody_id"]},
            headers=bearer(mine["customer"]),
        )
        # 404, not 403: do not confirm the session exists.
        assert response.status_code == 404

    def test_a_missing_custody_id_is_a_validation_error(self, pg_client, pg_app):
        customer = make_user()
        db.session.commit()
        response = pg_client.post(
            REQUEST, json={}, headers=bearer(customer)
        )
        assert response.status_code == 422
        assert response.get_json()["error"]["details"]["field"] == "custody_session_id"

    def test_an_unknown_custody_session_is_a_404(self, pg_client, pg_app):
        customer = make_user()
        db.session.commit()
        response = pg_client.post(
            REQUEST,
            json={"custody_session_id": str(uuid.uuid4())},
            headers=bearer(customer),
        )
        assert response.status_code == 404

    def test_anonymous_cannot_request(self, pg_client, pg_app):
        assert pg_client.post(REQUEST, json={"custody_session_id": "x"}).status_code == 401


class TestWhichPredictionIsCurrent:
    """Regression tests for the "latest prediction" ordering.

    These exist because of a real disagreement: a customer requested their car,
    was told 9 minutes, and the valet queue showed 14 for the same vehicle. The
    seed writes a future-dated prediction whose ``requested_at`` matched the new
    one *exactly*, so ``order_by(requested_at.desc()).limit(1)`` broke the tie
    arbitrarily and returned the stale row.
    """

    @staticmethod
    def _prediction(custody_id, *, status, wait, requested_at):
        return RetrievalPrediction(
            custody_session_id=uuid.UUID(custody_id),
            requested_at=requested_at,
            predicted_wait_minutes=wait,
            min_wait_minutes=wait,
            max_wait_minutes=wait,
            confidence=0.7,
            method="ridge-regression",
            status=status,
        )

    def test_pending_estimate_wins_a_requested_at_tie(
        self, pg_client, pg_app, trained_artifact
    ):
        """The live estimate is the PENDING one, not whichever row sorts first."""
        context = _checked_in(pg_client)
        # A future-dated value, exactly as the demo seed writes: requested_at is
        # a modelling input, not a record of when anything was stored.
        tie = datetime.now(timezone.utc) + timedelta(hours=2)

        db.session.add(
            self._prediction(
                context["custody_id"],
                status=str(PredictionStatus.DISPATCHED),
                wait=14,
                requested_at=tie,
            )
        )
        db.session.commit()
        db.session.add(
            self._prediction(
                context["custody_id"],
                status=str(PredictionStatus.PENDING),
                wait=9,
                requested_at=tie,
            )
        )
        db.session.commit()

        latest = pg_client.get(
            f"/api/v1/retrieval/{context['custody_id']}",
            headers=bearer(context["customer"]),
        )
        assert latest.status_code == 200, latest.get_json()
        # `GET /retrieval/<custody_id>` flattens the prediction into `data`.
        assert latest.get_json()["data"]["predicted_wait_minutes"] == 9

        # And the valet must be told the same number the customer was.
        session = db.session.get(CustodySession, uuid.UUID(context["custody_id"]))
        session.status = str(CustodyStatus.RETRIEVAL_REQUESTED)
        session.retrieval_requested_at = datetime.now(timezone.utc)
        db.session.commit()

        queue = pg_client.get(QUEUE, headers=bearer(context["operator"]))
        assert queue.status_code == 200, queue.get_json()
        row = next(
            item
            for item in queue.get_json()["data"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["estimated_minutes"] == 9
        assert row["prediction_status"] == str(PredictionStatus.PENDING)

    def test_write_order_decides_when_nothing_is_pending(
        self, pg_client, pg_app, trained_artifact
    ):
        """With no PENDING row, the newest *written* estimate is current.

        A backdated ``requested_at`` must not make an older row look newer.
        """
        context = _checked_in(pg_client)
        now = datetime.now(timezone.utc)

        db.session.add(
            self._prediction(
                context["custody_id"],
                status=str(PredictionStatus.FULFILLED),
                wait=30,
                # Written first, but stamped far in the future so a
                # requested_at ordering would wrongly prefer it.
                requested_at=now + timedelta(hours=5),
            )
        )
        db.session.commit()
        db.session.add(
            self._prediction(
                context["custody_id"],
                status=str(PredictionStatus.DISPATCHED),
                wait=7,
                requested_at=now - timedelta(hours=1),
            )
        )
        db.session.commit()

        latest = pg_client.get(
            f"/api/v1/retrieval/{context['custody_id']}",
            headers=bearer(context["customer"]),
        )
        assert latest.status_code == 200, latest.get_json()
        assert latest.get_json()["data"]["predicted_wait_minutes"] == 7

    def test_ordering_is_deterministic_across_reads(
        self, pg_client, pg_app, trained_artifact
    ):
        """Two reads of a tied state must not return different answers."""
        context = _checked_in(pg_client)
        tie = datetime.now(timezone.utc) + timedelta(hours=3)
        for wait, status in (
            (11, PredictionStatus.DISPATCHED),
            (9, PredictionStatus.PENDING),
            (13, PredictionStatus.SUPERSEDED),
        ):
            db.session.add(
                self._prediction(
                    context["custody_id"],
                    status=str(status),
                    wait=wait,
                    requested_at=tie,
                )
            )
            db.session.commit()

        url = f"/api/v1/retrieval/{context['custody_id']}"
        waits = {
            pg_client.get(url, headers=bearer(context["customer"]))
            .get_json()["data"]["predicted_wait_minutes"]
            for _ in range(3)
        }
        assert waits == {9}
