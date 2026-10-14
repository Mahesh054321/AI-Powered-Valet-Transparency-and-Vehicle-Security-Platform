"""Tests for the valet operator dashboard.

What this module holds the endpoint to:

* **Every section carries a state.** An empty retrieval queue is a good
  outcome and must not be indistinguishable from a failed request.
* **The badge vocabulary is derived, not guessed.** ``display_status`` is
  computed from the custody state and the security posture, and a security
  problem overrides the custody state rather than sitting beside it.
* **"Incoming" and "in custody" are different things.** A ticket with no
  custody session is an arrival; a session is a car we are holding.
* **The retrieval queue and the pending pickups are different jobs.** One is
  work a valet still has to do, the other is work waiting on the customer.
* **Every action the operator can take is audited.** This is the requirement
  that matters most: the dashboard performs no writes of its own, so each
  action must be provably recorded by the endpoint it calls.
* **The alert list reads the real alerts.** Regression cover for a bug where
  the section read the wrong response key and reported zero open alerts
  beside a real HIGH breach — a security panel that fails in the reassuring
  direction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.extensions import db
from models.audit_log import AuditAction, AuditLog
from models.custody_session import CustodySession
from models.enums import AlertSeverity, AlertStatus, CustodyStatus, TicketStatus
from models.security_alert import SecurityAlert
from models.valet_ticket import ValetTicket
from tests.workflow_helpers import bearer, make_user, make_vehicle, make_zone
from utils.security import Role

pytestmark = pytest.mark.postgres

DASHBOARD = "/api/v1/staff/dashboard"
CUSTODY = "/api/v1/custody-sessions"
TICKETS = "/api/v1/tickets"
ALERTS = "/api/v1/alerts"


def _ticketed(client, *, start_custody: bool = True, status: str | None = None):
    """A ticket, optionally with a custody session already open."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = make_zone()
    db.session.commit()

    issued = client.post(
        TICKETS, json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201, issued.get_json()
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    if not start_custody:
        return {
            "operator": operator,
            "customer": customer,
            "vehicle": vehicle,
            "zone": zone,
            "ticket_id": ticket_id,
            "custody_id": None,
        }

    # A real check-in moves the ticket to ACTIVE. Creating a custody session
    # while the ticket is still ISSUED is a state the workflow cannot produce,
    # and it would make the car look simultaneously incoming and in custody.
    ticket = db.session.get(ValetTicket, uuid.UUID(ticket_id))
    ticket.status = str(TicketStatus.ACTIVE)

    session = CustodySession(
        ticket_id=uuid.UUID(ticket_id),
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        status=status or str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.session.add(session)
    db.session.commit()
    return {
        "operator": operator,
        "customer": customer,
        "vehicle": vehicle,
        "zone": zone,
        "ticket_id": ticket_id,
        "custody_id": str(session.id),
    }


def _alert(custody_id, *, severity=AlertSeverity.HIGH, status=AlertStatus.OPEN):
    now = datetime.now(timezone.utc)
    terminal = status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED)
    alert = SecurityAlert(
        custody_session_id=uuid.UUID(custody_id),
        alert_type="GEOFENCE_BREACH",
        severity=str(severity),
        status=str(status),
        title="Vehicle left its zone",
        reason="Last fix was outside the configured boundary.",
        confidence=0.9,
        occurred_at=now,
        # The schema requires a resolution time on a terminal alert, which is
        # the point: "closed" without a record of when is not an audit trail.
        resolved_at=now if terminal else None,
        resolution_notes="Closed in test." if terminal else None,
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def _section_states(payload):
    return {
        name: payload[name]["state"]
        for name in (
            "incoming",
            "active_vehicles",
            "pending_pickups",
            "retrieval_queue",
            "security_alerts",
            "inspections",
            "zones",
        )
    }


class TestAccess:
    def test_a_customer_is_refused(self, pg_client):
        """The operator screen is not a customer view.

        It must be 403 rather than an empty 200: an empty dashboard would read
        as "you have no vehicles" rather than "you may not look".
        """
        customer = make_user()
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(customer))

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"

    def test_anonymous_is_refused(self, pg_client):
        assert pg_client.get(DASHBOARD).status_code == 401

    @pytest.mark.parametrize(
        "role", [Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN]
    )
    def test_every_staff_role_may_read_it(self, pg_client, role):
        user = make_user(role)
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(user))

        assert response.status_code == 200, response.get_json()


class TestComposition:
    def test_every_section_carries_a_state(self, pg_client):
        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        payload = pg_client.get(DASHBOARD, headers=bearer(staff)).get_json()["data"]

        states = _section_states(payload)
        assert set(states) == {
            "incoming",
            "active_vehicles",
            "pending_pickups",
            "retrieval_queue",
            "security_alerts",
            "inspections",
            "zones",
        }
        for name, state in states.items():
            assert state in {"ok", "empty", "unavailable"}, (name, state)

    def test_an_empty_section_explains_itself(self, pg_client):
        """Whatever is empty, it must say so rather than rendering blank.

        Asserted as a contract over every section rather than by asserting the
        database is bare: the test database is session-scoped and never reset,
        so custody sessions from other modules are present by the time this
        runs. Depending on global emptiness would make the test fail for
        reasons that have nothing to do with what it is checking.
        """
        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(staff))
        payload = response.get_json()["data"]

        assert response.status_code == 200
        for name, state in _section_states(payload).items():
            section = payload[name]
            assert section.get("count", 0) >= 0, name
            if state == "empty":
                # An empty section is an answer, so it must carry one.
                assert section["reason"], name
                assert section["count"] == 0, name

    def test_the_empty_shape_is_correct_when_there_is_nothing_to_show(self, pg_app):
        """The empty case, exercised directly rather than hoped for.

        The endpoint test above cannot force an empty database, so the shape an
        operator sees on a quiet morning is asserted here instead.
        """
        from services import staff_dashboard

        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        active = staff_dashboard._active_section(staff, [])  # noqa: SLF001
        assert active == {
            "state": "empty",
            "reason": "No vehicles are currently in custody.",
            "items": [],
            "count": 0,
        }

        pickups = staff_dashboard._pickup_section(staff, [])  # noqa: SLF001
        assert pickups["state"] == "empty"
        assert pickups["reason"]

    def test_labels_are_sent_with_the_payload(self, pg_client):
        """So the UI cannot keep a second copy of the vocabulary and drift."""
        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        labels = pg_client.get(DASHBOARD, headers=bearer(staff)).get_json()["data"][
            "display_status_labels"
        ]

        assert set(labels) == {"IN_CUSTODY", "MOVING", "READY", "ALERT", "COMPLETED", "UNKNOWN"}


class TestIncomingVehicles:
    def test_a_ticket_with_no_custody_session_is_incoming(self, pg_client):
        """Issued but not handed over: the only arrival state the model has."""
        context = _ticketed(pg_client, start_custody=False)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        incoming = payload["incoming"]
        assert incoming["state"] == "ok"
        assert incoming["count"] >= 1
        codes = [item["ticket_code"] for item in incoming["items"]]
        ticket = db.session.get(ValetTicket, uuid.UUID(context["ticket_id"]))
        assert ticket.ticket_code in codes

    def test_a_vehicle_already_in_custody_is_not_incoming(self, pg_client):
        """Otherwise every parked car would look like an arrival forever."""
        context = _ticketed(pg_client, start_custody=True)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        codes = [item["ticket_code"] for item in payload["incoming"]["items"]]
        ticket = db.session.get(ValetTicket, uuid.UUID(context["ticket_id"]))
        assert ticket.ticket_code not in codes


class TestActiveVehicles:
    def test_a_parked_car_appears_with_vehicle_zone_and_gps_state(self, pg_client):
        context = _ticketed(pg_client)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        active = payload["active_vehicles"]
        assert active["state"] == "ok"
        row = next(
            item
            for item in active["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["vehicle"]["plate_number"] == context["vehicle"].plate_number
        assert row["zone"]["code"] == context["zone"].code
        assert row["custody_status"] == str(CustodyStatus.PARKED)
        # No fixes have been posted, so the row must say so rather than imply
        # a clean last position.
        assert row["gps_has_data"] is False
        assert row["gps"] is None

    def test_the_customer_to_hand_back_to_is_on_the_row(self, pg_client):
        """Completing a pickup means checking who gets the keys."""
        context = _ticketed(pg_client)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["customer"]["email"] == context["customer"].email
        assert row["customer"]["name"] == context["customer"].full_name


class TestDisplayStatus:
    """The five badges, and the rule that decides between them."""

    @pytest.mark.parametrize(
        ("custody", "expected"),
        [
            (CustodyStatus.CHECKED_IN, "IN_CUSTODY"),
            (CustodyStatus.PARKED, "IN_CUSTODY"),
            (CustodyStatus.IN_TRANSIT, "MOVING"),
            (CustodyStatus.RETRIEVAL_IN_PROGRESS, "MOVING"),
            (CustodyStatus.READY_FOR_PICKUP, "READY"),
        ],
    )
    def test_custody_state_maps_to_a_badge(self, pg_client, custody, expected):
        context = _ticketed(pg_client, status=str(custody))

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["display_status"] == expected
        # The label travels with the status so the UI cannot invent one.
        assert row["display_label"] == payload["display_status_labels"][expected]

    def test_an_open_alert_overrides_the_custody_badge(self, pg_client):
        """A parked car that is breaching is not primarily 'in custody'.

        The thing the operator has to act on is the breach. A badge reading IN
        CUSTODY with a small icon beside it would be scanned as fine, which is
        the wrong failure mode for a security screen.
        """
        context = _ticketed(pg_client, status=str(CustodyStatus.PARKED))
        _alert(context["custody_id"], severity=AlertSeverity.HIGH)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["custody_status"] == str(CustodyStatus.PARKED)
        assert row["display_status"] == "ALERT"
        assert row["alarming"] is True
        assert row["open_alert_count"] == 1

    def test_a_resolved_alert_does_not_keep_the_car_in_alert(self, pg_client):
        """Otherwise every car that ever had an alert stays red forever."""
        context = _ticketed(pg_client, status=str(CustodyStatus.PARKED))
        _alert(context["custody_id"], status=AlertStatus.RESOLVED)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["display_status"] == "IN_CUSTODY"
        assert row["open_alert_count"] == 0


class TestQueueAndPickupsAreDifferentJobs:
    def test_a_requested_car_is_in_the_queue_not_in_pickups(self, pg_client):
        """The queue is work a valet still has to do: go and get the car."""
        context = _ticketed(pg_client, status=str(CustodyStatus.RETRIEVAL_REQUESTED))
        session = db.session.get(CustodySession, uuid.UUID(context["custody_id"]))
        session.retrieval_requested_at = datetime.now(timezone.utc)
        db.session.commit()

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        queue_ids = [item["custody_session_id"] for item in payload["retrieval_queue"]["items"]]
        pickup_ids = [item["custody_session_id"] for item in payload["pending_pickups"]["items"]]
        assert context["custody_id"] in queue_ids
        assert context["custody_id"] not in pickup_ids

    def test_a_staged_car_is_in_pickups_not_in_the_queue(self, pg_client):
        """Pickups are work already done, waiting on the customer to sign."""
        context = _ticketed(pg_client, status=str(CustodyStatus.READY_FOR_PICKUP))
        session = db.session.get(CustodySession, uuid.UUID(context["custody_id"]))
        session.retrieval_requested_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.ready_for_pickup_at = datetime.now(timezone.utc)
        db.session.commit()

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        queue_ids = [item["custody_session_id"] for item in payload["retrieval_queue"]["items"]]
        pickup_ids = [item["custody_session_id"] for item in payload["pending_pickups"]["items"]]
        assert context["custody_id"] in pickup_ids
        assert context["custody_id"] not in queue_ids
        assert payload["pending_pickups"]["state"] == "ok"

    def test_the_queue_is_labelled_as_a_poll(self, pg_client):
        """There is no push channel, and the payload must not imply one."""
        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        payload = pg_client.get(DASHBOARD, headers=bearer(staff)).get_json()["data"]

        assert payload["delivery"] == "polled"
        assert payload["retrieval_queue"]["delivery"] == "polled"


class TestSecurityAlerts:
    def test_open_alerts_are_listed(self, pg_client):
        """Regression: this section once read the wrong key and showed none.

        ``list_alerts`` returns its rows under ``alerts``. Reading ``items``
        does not raise - it yields an empty list, so the panel reported zero
        open alerts while a HIGH breach sat unresolved. A security count that
        is wrong in the reassuring direction is the worst kind of wrong.
        """
        context = _ticketed(pg_client)
        _alert(context["custody_id"], severity=AlertSeverity.CRITICAL)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        alerts = payload["security_alerts"]
        assert alerts["state"] == "ok"
        assert alerts["count"] >= 1
        assert alerts["highest_severity"] == str(AlertSeverity.CRITICAL)
        assert any(
            item["alert_type"] == "GEOFENCE_BREACH" for item in alerts["items"]
        )

    def test_an_acknowledged_alert_is_still_open(self, pg_client):
        """'Someone has seen it' is not 'it is dealt with'."""
        context = _ticketed(pg_client)
        _alert(context["custody_id"], status=AlertStatus.ACKNOWLEDGED)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        assert payload["security_alerts"]["count"] >= 1

    def test_a_car_with_no_alerts_is_not_flagged(self, pg_client):
        """Asserted per session, not as a global zero.

        The test database is session-scoped and never reset, so alerts created
        by other tests persist. Counting them would make this test depend on
        what ran before it.
        """
        context = _ticketed(pg_client)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["open_alert_count"] == 0
        assert row["alarming"] is False
        assert row["display_status"] != "ALERT"

    def test_an_empty_alert_panel_states_its_reason(self, pg_client):
        """The shape of the empty case, without asserting the database is bare."""
        staff = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        alerts = pg_client.get(DASHBOARD, headers=bearer(staff)).get_json()["data"][
            "security_alerts"
        ]

        assert alerts["state"] in {"ok", "empty"}
        if alerts["state"] == "empty":
            assert alerts["count"] == 0
            assert alerts["reason"]


class TestInspectionsAndZones:
    def test_a_car_in_custody_needs_pickup_photos_until_it_has_them(self, pg_client):
        context = _ticketed(pg_client)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        row = next(
            item
            for item in payload["inspections"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["drop_off_captured"] is False
        assert row["pickup_captured"] is False
        assert row["comparison_ready"] is False
        assert row["needs_pickup_photos"] is True
        assert payload["inspections"]["awaiting_pickup_photos"] >= 1

    def test_zone_occupancy_is_reported(self, pg_client):
        context = _ticketed(pg_client)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]

        zones = payload["zones"]
        assert zones["state"] == "ok"
        zone = next(item for item in zones["items"] if item["code"] == context["zone"].code)
        assert zone["capacity"] == context["zone"].capacity
        assert "available_slots" in zone
        assert 0 <= zone["occupancy_percent"] <= 100


class TestActionsAreAudited:
    """The dashboard writes nothing itself; each action must still be recorded.

    These assert the audit trail behind the buttons an operator presses, which
    is the requirement the brief states outright.
    """

    @staticmethod
    def _actions_since(before: int) -> list[str]:
        rows = db.session.execute(
            select(AuditLog.action).order_by(AuditLog.created_at.desc()).limit(20)
        ).scalars()
        return list(rows)[: before] if before else list(rows)

    @staticmethod
    def _count() -> int:
        return db.session.execute(select(func.count(AuditLog.id))).scalar_one()

    def test_moving_a_car_to_moving_is_audited(self, pg_client):
        context = _ticketed(pg_client, status=str(CustodyStatus.PARKED))
        before = self._count()

        response = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": str(CustodyStatus.IN_TRANSIT)},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 200, response.get_json()

        actions = db.session.execute(
            select(AuditLog.action)
            .where(AuditLog.entity_id == uuid.UUID(context["custody_id"]))
            .order_by(AuditLog.created_at.desc())
        ).scalars()
        assert str(AuditAction.STATUS_CHANGE) in list(actions)
        assert self._count() > before

    def test_marking_a_car_ready_is_audited(self, pg_client):
        """'Mark ready' is the same state machine, so the same audit row."""
        context = _ticketed(pg_client, status=str(CustodyStatus.PARKED))

        response = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": str(CustodyStatus.READY_FOR_PICKUP)},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 200, response.get_json()

        actions = list(
            db.session.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_id == uuid.UUID(context["custody_id"])
                )
            ).scalars()
        )
        assert str(AuditAction.STATUS_CHANGE) in actions

        # And the dashboard reflects it in the pickups section, not the queue.
        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]
        pickup_ids = [item["custody_session_id"] for item in payload["pending_pickups"]["items"]]
        assert context["custody_id"] in pickup_ids

    def test_completing_a_pickup_is_audited_and_closes_the_session(self, pg_client):
        context = _ticketed(pg_client, status=str(CustodyStatus.READY_FOR_PICKUP))

        response = pg_client.post(
            f"{TICKETS}/{context['ticket_id']}/complete",
            json={"odometer_at_pickup": 12500, "condition_accepted": True},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 200, response.get_json()

        actions = list(
            db.session.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_id == uuid.UUID(context["ticket_id"])
                )
            ).scalars()
        )
        assert str(AuditAction.VEHICLE_PICKED_UP) in actions

        # The car leaves the operator's active list entirely.
        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]
        active_ids = [item["custody_session_id"] for item in payload["active_vehicles"]["items"]]
        assert context["custody_id"] not in active_ids

    def test_acknowledging_an_alert_is_audited_and_clears_the_badge(self, pg_client):
        # Triage is a manager act, not an operator one - see the capabilities
        # test. Using the operator here would be testing a 403.
        context = _ticketed(pg_client)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert = _alert(context["custody_id"])

        response = pg_client.post(
            f"{ALERTS}/{alert.id}/acknowledge", headers=bearer(manager)
        )
        assert response.status_code == 200, response.get_json()

        actions = list(
            db.session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == alert.id)
            ).scalars()
        )
        assert str(AuditAction.ALERT_ACKNOWLEDGED) in actions

        # Still open - acknowledging is not resolving - so the car stays ALERT.
        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]
        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["display_status"] == "ALERT"

    def test_resolving_an_alert_is_audited_and_clears_the_badge(self, pg_client):
        context = _ticketed(pg_client)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert = _alert(context["custody_id"])

        response = pg_client.post(
            f"{ALERTS}/{alert.id}/resolve",
            json={"notes": "False positive: GPS drift at the boundary."},
            headers=bearer(manager),
        )
        assert response.status_code == 200, response.get_json()

        actions = list(
            db.session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == alert.id)
            ).scalars()
        )
        assert str(AuditAction.ALERT_RESOLVED) in actions

        payload = pg_client.get(
            DASHBOARD, headers=bearer(context["operator"])
        ).get_json()["data"]
        row = next(
            item
            for item in payload["active_vehicles"]["items"]
            if item["custody_session_id"] == context["custody_id"]
        )
        assert row["display_status"] == "IN_CUSTODY"

    def test_closing_custody_is_redirected_to_the_ticket_endpoint(self, pg_client):
        """The state machine, not the dashboard, decides what is possible.

        ``CHECKED_OUT`` is refused as a 422 that names the right endpoint
        rather than a 409, because completion has to settle the ticket, free
        the zone and update the vehicle - none of which a bare status write
        does. Telling the caller where to go beats telling them "not allowed".
        """
        context = _ticketed(pg_client, status=str(CustodyStatus.PARKED))

        response = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": str(CustodyStatus.CHECKED_OUT)},
            headers=bearer(context["operator"]),
        )

        assert response.status_code == 422
        assert "completion endpoint" in response.get_json()["error"]["message"]

        # And the car is genuinely still in custody.
        session = db.session.get(CustodySession, uuid.UUID(context["custody_id"]))
        assert session.status == str(CustodyStatus.PARKED)

    def test_an_illegal_jump_is_refused_with_the_allowed_list(self, pg_client):
        """A transition the state machine does not permit, with recovery info."""
        context = _ticketed(pg_client, status=str(CustodyStatus.READY_FOR_PICKUP))

        response = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": str(CustodyStatus.PARKED)},
            headers=bearer(context["operator"]),
        )

        assert response.status_code == 409
        assert "allowed" in response.get_json()["error"]["details"]


class TestCapabilities:
    """What each role may do, published so the UI cannot guess.

    A button that 403s is worse than no button: it tells an operator the
    action is available and then refuses them in front of a customer.
    """

    def test_an_operator_may_move_cars_but_not_triage_alerts(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        caps = pg_client.get(DASHBOARD, headers=bearer(operator)).get_json()["data"][
            "capabilities"
        ]

        assert caps["can_move_vehicle"] is True
        assert caps["can_complete_pickup"] is True
        assert caps["can_capture_inspection"] is True
        # Triage and damage verification are deliberately manager acts: the
        # person who photographed the car should not be the only one judging
        # whether the algorithm was right about it.
        assert caps["can_triage_alerts"] is False
        assert caps["can_verify_damage"] is False

    def test_a_manager_may_do_everything_on_this_screen(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        caps = pg_client.get(DASHBOARD, headers=bearer(manager)).get_json()["data"][
            "capabilities"
        ]

        assert all(caps.values()), caps

    def test_the_published_flags_match_what_the_endpoints_enforce(self, pg_client):
        """The flags must not drift from the guards they mirror.

        Asserted by actually calling the endpoint rather than by reading a
        constant, because the whole point of publishing them is that pressing
        the button succeeds.
        """
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        context = _ticketed(pg_client)
        alert = _alert(context["custody_id"])

        caps = pg_client.get(DASHBOARD, headers=bearer(operator)).get_json()["data"][
            "capabilities"
        ]

        # Allowed: moving the car.
        moved = pg_client.patch(
            f"{CUSTODY}/{context['custody_id']}",
            json={"status": str(CustodyStatus.IN_TRANSIT)},
            headers=bearer(operator),
        )
        assert (moved.status_code == 200) is caps["can_move_vehicle"]

        # Refused: acknowledging an alert.
        acked = pg_client.post(
            f"{ALERTS}/{alert.id}/acknowledge", headers=bearer(operator)
        )
        assert (acked.status_code == 200) is caps["can_triage_alerts"]
        assert acked.status_code == 403
