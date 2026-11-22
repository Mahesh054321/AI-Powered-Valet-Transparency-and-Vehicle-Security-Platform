"""Tests for the centralized alert feed, its scoping, and the channel layer.

The rule these tests exist to protect is **read state is per recipient**. A
single READ flag on the alert would mean whoever opened the app first cleared it
for everyone — a manager could miss a CRITICAL breach because a customer glanced
at their phone. So the tests assert that one person's read does not change
another's, and that a scoped "read all" cannot reach alerts the actor was never
shown.

Also covered:

* **scoping happens in the query** — a customer cannot widen their view by
  omitting a parameter, and cannot reach an alert by guessing its id;
* **resolution is manager-only** and cannot be repeated;
* **the channel abstraction holds** — business logic produces notifications
  without naming a transport, disabled channels are skipped rather than
  recording fake failures, and adding a channel changes nothing upstream.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.extensions import db
from models.alert import Alert, AlertAudience, AlertCategory, AlertRead, category_for
from models.notification import Notification, NotificationChannel, NotificationStatus
from tests.workflow_helpers import bearer, make_user, make_vehicle, make_zone
from utils.security import Role

pytestmark = pytest.mark.postgres

FEED = "/api/v1/feed"


def _auth(user):
    return bearer(user)


def _raise(
    pg_client,
    actor,
    *,
    category="OVERSPEED",
    severity="MEDIUM",
    message="Vehicle exceeded the zone speed limit.",
    audiences=("VALET_OPERATOR", "VALET_MANAGER"),
    vehicle_id=None,
    custody_id=None,
    source_entity_type=None,
    source_id=None,
):
    body = {
        "type": category,
        "severity": severity,
        "message": message,
        "audiences": list(audiences),
    }
    if vehicle_id:
        body["vehicle_id"] = str(vehicle_id)
    if custody_id:
        body["custody_id"] = str(custody_id)
    if source_entity_type:
        body["source_entity_type"] = source_entity_type
        body["source_id"] = str(source_id)
    return pg_client.post(FEED, json=body, headers=_auth(actor))


def _make_custody(owner=None, vehicle=None):
    """A ticket plus an open custody session.

    ``security_alerts`` and ``vehicle_inspections`` both require a custody
    session, so the publisher tests cannot create those rows standalone.
    """
    from models.custody_session import CustodySession
    from models.enums import CustodyStatus, TicketStatus
    from models.valet_ticket import ValetTicket

    owner = owner or make_user()
    vehicle = vehicle or make_vehicle(owner.id)
    zone = make_zone()
    operator = make_user(Role.VALET_OPERATOR)
    db.session.flush()

    ticket = ValetTicket(
        ticket_code=f"VT-ALERT{uuid.uuid4().hex[:6].upper()}",
        customer_id=owner.id,
        vehicle_id=vehicle.id,
        status=str(TicketStatus.ACTIVE),
        issued_at=datetime.now(timezone.utc),
    )
    db.session.add(ticket)
    db.session.flush()

    session = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        status=str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc),
        handover_point="Main Lobby - Bay 1",
    )
    db.session.add(session)
    db.session.commit()
    return session


def _list(pg_client, actor, **params):
    response = pg_client.get(FEED, headers=_auth(actor), query_string=params)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


class TestVocabulary:
    def test_the_five_categories_are_exactly_as_specified(self):
        assert {member.value for member in AlertCategory} == {
            "GEOFENCE_BREACH",
            "SUSPICIOUS_MOVEMENT",
            "OVERSPEED",
            "POTENTIAL_DAMAGE",
            "RETRIEVAL_READY",
        }

    def test_every_geofence_type_maps_to_a_category(self):
        """No detector type may fall through unmapped.

        ``category_for`` raises rather than defaulting, so this test is what
        catches a new ``AlertType`` being added without deciding where it
        belongs in the feed.
        """
        from models.enums import AlertType

        for member in AlertType:
            assert category_for(member.value) in set(AlertCategory)

    def test_an_unknown_source_type_raises_rather_than_guessing(self):
        with pytest.raises(ValueError, match="CATEGORY_BY_SOURCE_TYPE"):
            category_for("NOT_A_REAL_TYPE")

    def test_the_projection_is_lossy_but_recoverable(self, pg_client):
        """The five categories fold seven detector types together, so the
        original must survive on the row or the detail is lost for good."""
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        source_id = uuid.uuid4()

        response = _raise(
            pg_client,
            manager,
            category="SUSPICIOUS_MOVEMENT",
            source_entity_type="security_alerts",
            source_id=source_id,
        )
        assert response.status_code == 201

        body = response.get_json()["data"]
        assert body["type"] == "SUSPICIOUS_MOVEMENT"

        alert = db.session.get(Alert, uuid.UUID(body["alert_id"]))
        assert alert.source_entity_type == "security_alerts"
        assert alert.source_id == source_id


class TestCreate:
    def test_staff_may_raise_an_alert(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        response = _raise(pg_client, operator)

        assert response.status_code == 201
        body = response.get_json()["data"]
        assert set(body) >= {
            "alert_id",
            "custody_id",
            "vehicle_id",
            "type",
            "severity",
            "message",
            "created_at",
            "status",
        }
        assert body["status"] == "OPEN"

    def test_a_customer_may_not_raise_an_alert(self, pg_client):
        """The feed describes what the system observed about a customer's car.
        Letting the subject of an observation write observations would make the
        feed untrustworthy."""
        customer = make_user()
        db.session.commit()

        response = _raise(pg_client, customer)

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "body",
        [
            {"type": "NOT_A_TYPE", "message": "x", "audiences": ["VALET_MANAGER"]},
            {"type": "OVERSPEED", "message": "x", "audiences": ["NOT_AN_AUDIENCE"]},
            {"type": "OVERSPEED", "message": "x", "audiences": []},
            {"type": "OVERSPEED", "message": "", "audiences": ["VALET_MANAGER"]},
            {"type": "OVERSPEED", "severity": "APOCALYPTIC", "message": "x",
             "audiences": ["VALET_MANAGER"]},
        ],
    )
    def test_a_malformed_alert_is_refused(self, pg_client, body):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.post(FEED, json=body, headers=_auth(manager))

        assert response.status_code == 422, response.get_json()

    def test_the_same_source_does_not_raise_twice(self, pg_client):
        """De-duplication is what keeps a persistent condition from burying the
        feed: a breaching car produces a fix every few seconds."""
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        source_id = uuid.uuid4()

        first = _raise(
            pg_client, manager, source_entity_type="security_alerts", source_id=source_id
        )
        second = _raise(
            pg_client, manager, source_entity_type="security_alerts", source_id=source_id
        )

        assert first.status_code == 201
        # 200, not 201: claiming a new row when one was not created would be a
        # lie in the status code.
        assert second.status_code == 200
        assert first.get_json()["data"]["alert_id"] == second.get_json()["data"]["alert_id"]

    def test_a_different_category_on_the_same_source_is_distinct(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        source_id = uuid.uuid4()

        first = _raise(
            pg_client, manager, category="OVERSPEED",
            source_entity_type="security_alerts", source_id=source_id,
        )
        second = _raise(
            pg_client, manager, category="SUSPICIOUS_MOVEMENT",
            source_entity_type="security_alerts", source_id=source_id,
        )

        assert first.status_code == 201 and second.status_code == 201
        assert (
            first.get_json()["data"]["alert_id"] != second.get_json()["data"]["alert_id"]
        )


class TestScoping:
    def test_a_customer_sees_only_their_own_vehicles(self, pg_client):
        owner = make_user()
        other = make_user()
        their_car = make_vehicle(owner.id)
        someone_elses_car = make_vehicle(other.id)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        _raise(
            pg_client, manager, vehicle_id=their_car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"),
            message="About your car.",
        )
        _raise(
            pg_client, manager, vehicle_id=someone_elses_car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"),
            message="About somebody else's car.",
        )

        listing = _list(pg_client, owner)

        messages = [item["message"] for item in listing["alerts"]]
        assert "About your car." in messages
        assert "About somebody else's car." not in messages

    def test_an_internal_alert_is_hidden_from_the_customer(self, pg_client):
        """Addressing matters as much as ownership: an operational alert about a
        customer's car is not the customer's business."""
        owner = make_user()
        car = make_vehicle(owner.id)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        _raise(
            pg_client, manager, vehicle_id=car.id,
            audiences=("VALET_OPERATOR", "VALET_MANAGER"),
            message="Internal only.",
        )

        assert _list(pg_client, owner)["total"] == 0

    def test_a_manager_sees_the_whole_feed(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        _raise(pg_client, manager, audiences=("VALET_OPERATOR",))
        _raise(pg_client, manager, audiences=("VALET_MANAGER",), message="Second.")

        # A manager who saw only manager-addressed alerts would miss incidents
        # raised for operators, which is the opposite of the point.
        assert _list(pg_client, manager)["total"] >= 2

    def test_an_alert_cannot_be_reached_by_guessing_its_id(self, pg_client):
        """Fetching by id is a separate path from listing, so it must apply the
        same scoping or it becomes a way around it."""
        owner = make_user()
        stranger = make_user()
        car = make_vehicle(stranger.id)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        raised = _raise(
            pg_client, manager, vehicle_id=car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"), message="Not yours.",
        )
        alert_id = raised.get_json()["data"]["alert_id"]

        response = pg_client.get(f"{FEED}/{alert_id}", headers=_auth(owner))

        # 404 rather than 403: confirming the alert exists but is hidden would
        # leak the existence of an incident about someone else's car.
        assert response.status_code == 404


class TestReadState:
    def test_one_person_reading_does_not_clear_it_for_another(self, pg_client):
        """The central rule of this module."""
        owner = make_user()
        car = make_vehicle(owner.id)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        raised = _raise(
            pg_client, manager, vehicle_id=car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"),
        )
        alert_id = raised.get_json()["data"]["alert_id"]

        response = pg_client.post(
            f"{FEED}/{alert_id}/read", headers=_auth(owner)
        )
        assert response.status_code == 200

        # Read the same alert through each actor's own eyes. The detail endpoint
        # reports `is_read` relative to the caller, which is the invariant under
        # test; scanning page one of the list would make this depend on how many
        # alerts earlier tests happened to leave behind.
        for_owner = pg_client.get(f"{FEED}/{alert_id}", headers=_auth(owner)).get_json()["data"]
        for_manager = pg_client.get(f"{FEED}/{alert_id}", headers=_auth(manager)).get_json()["data"]

        assert for_owner["is_read"] is True
        # The manager's copy is untouched. A shared flag would have cleared both.
        assert for_manager["is_read"] is False
        assert for_manager["read_at"] is None

        # And the badge agrees with the per-alert view.
        assert db.session.execute(
            select(func.count(AlertRead.id)).where(
                AlertRead.alert_id == uuid.UUID(alert_id)
            )
        ).scalar_one() == 1

    def test_marking_read_is_idempotent(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        first = pg_client.post(f"{FEED}/{alert_id}/read", headers=_auth(manager))
        second = pg_client.post(f"{FEED}/{alert_id}/read", headers=_auth(manager))

        assert first.get_json()["data"]["already_read"] is False
        assert second.get_json()["data"]["already_read"] is True
        # And it produced one marker, not two.
        assert db.session.execute(
            select(func.count(AlertRead.id)).where(
                AlertRead.alert_id == uuid.UUID(alert_id)
            )
        ).scalar_one() == 1

    def test_read_all_only_touches_what_the_actor_can_see(self, pg_client):
        owner = make_user()
        stranger = make_user()
        their_car = make_vehicle(owner.id)
        other_car = make_vehicle(stranger.id)
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        _raise(
            pg_client, manager, vehicle_id=their_car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"), message="Yours.",
        )
        other = _raise(
            pg_client, manager, vehicle_id=other_car.id,
            audiences=("CUSTOMER", "VALET_MANAGER"), message="Theirs.",
        )
        other_id = other.get_json()["data"]["alert_id"]

        response = pg_client.post(f"{FEED}/read-all", headers=_auth(owner))
        assert response.status_code == 200
        assert response.get_json()["data"]["marked_read"] >= 1

        # The other customer's alert must still be unread for them.
        assert any(
            item["alert_id"] == other_id
            for item in _list(pg_client, stranger, unread_only=True)["alerts"]
        )

    def test_the_badge_counts_only_open_unread_alerts(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        before = pg_client.get(f"{FEED}/summary", headers=_auth(manager)).get_json()["data"]
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        middle = pg_client.get(f"{FEED}/summary", headers=_auth(manager)).get_json()["data"]
        assert middle["unread"] == before["unread"] + 1

        pg_client.post(f"{FEED}/{alert_id}/read", headers=_auth(manager))
        after = pg_client.get(f"{FEED}/summary", headers=_auth(manager)).get_json()["data"]
        assert after["unread"] == before["unread"]


class TestResolve:
    def test_an_operator_may_not_close_an_alert(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        alert_id = _raise(pg_client, operator).get_json()["data"]["alert_id"]

        response = pg_client.post(
            f"{FEED}/{alert_id}/resolve", json={"notes": "done"}, headers=_auth(operator)
        )

        assert response.status_code == 403

    def test_a_customer_may_not_close_an_alert(self, pg_client):
        customer = make_user()
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        response = pg_client.post(
            f"{FEED}/{alert_id}/resolve", json={}, headers=_auth(customer)
        )

        assert response.status_code == 403

    def test_a_manager_resolves_and_is_named(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        response = pg_client.post(
            f"{FEED}/{alert_id}/resolve",
            json={"notes": "Investigated; vehicle returned to zone."},
            headers=_auth(manager),
        )

        assert response.status_code == 200
        body = response.get_json()["data"]
        assert body["status"] == "RESOLVED"
        assert body["resolved_by"] == manager.email
        assert body["resolved_at"]

    def test_dismissal_is_recorded_differently_from_resolution(self, pg_client):
        """'We dealt with it' and 'we judged it needed no action' are different
        claims, and the audit trail should not blur them."""
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        response = pg_client.post(
            f"{FEED}/{alert_id}/resolve",
            json={"dismiss": True, "notes": "Sensor artefact."},
            headers=_auth(manager),
        )

        assert response.get_json()["data"]["status"] == "DISMISSED"

    def test_an_alert_cannot_be_closed_twice(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        pg_client.post(f"{FEED}/{alert_id}/resolve", json={}, headers=_auth(manager))
        again = pg_client.post(f"{FEED}/{alert_id}/resolve", json={}, headers=_auth(manager))

        assert again.status_code == 409

    def test_resolution_is_audited(self, pg_client):
        from models.audit_log import AuditLog

        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        pg_client.post(
            f"{FEED}/{alert_id}/resolve", json={"notes": "closed"}, headers=_auth(manager)
        )

        row = db.session.execute(
            select(AuditLog)
            .where(AuditLog.entity_id == uuid.UUID(alert_id))
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        ).scalars().first()
        assert row is not None
        assert row.action == "ALERT_RESOLVED"
        assert row.actor_id == manager.id


class TestDeleteResolved:
    def test_a_manager_can_delete_a_resolved_alert_and_the_action_is_audited(self, pg_client):
        from models.audit_log import AuditLog

        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]
        pg_client.post(
            f"{FEED}/{alert_id}/resolve", json={}, headers=_auth(manager)
        )

        response = pg_client.delete(f"{FEED}/{alert_id}", headers=_auth(manager))

        assert response.status_code == 200
        assert response.get_json()["data"] == {"alert_id": alert_id, "deleted": True}
        assert db.session.get(Alert, uuid.UUID(alert_id)) is None
        audit = db.session.execute(
            select(AuditLog).where(AuditLog.entity_id == uuid.UUID(alert_id))
            .order_by(AuditLog.seq.desc())
        ).scalars().first()
        assert audit.action == "DELETE"
        assert audit.old_values["status"] == "RESOLVED"
        assert audit.actor_id == manager.id

    def test_an_open_alert_cannot_be_deleted(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]

        response = pg_client.delete(f"{FEED}/{alert_id}", headers=_auth(manager))

        assert response.status_code == 409
        assert db.session.get(Alert, uuid.UUID(alert_id)) is not None

    def test_an_operator_cannot_delete_a_resolved_alert(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]
        pg_client.post(
            f"{FEED}/{alert_id}/resolve", json={}, headers=_auth(manager)
        )

        response = pg_client.delete(f"{FEED}/{alert_id}", headers=_auth(operator))

        assert response.status_code == 403


class TestChannels:
    def test_raising_an_alert_produces_in_app_notifications(self, pg_client):
        """Business logic never names a transport, yet notifications appear —
        which is the point of the abstraction."""
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        before = db.session.execute(select(func.count(Notification.id))).scalar_one()

        _raise(pg_client, manager)

        after = db.session.execute(select(func.count(Notification.id))).scalar_one()
        assert after > before

        row = db.session.execute(
            select(Notification).order_by(Notification.created_at.desc()).limit(1)
        ).scalars().first()
        assert row.channel == NotificationChannel.IN_APP.value
        assert row.status == NotificationStatus.DELIVERED.value
        assert row.delivered_at is not None

    def test_disabled_channels_are_skipped_not_failed(self):
        """Email, SMS and push are not wired up. Skipping them must not write
        FAILED rows on every alert — 'not configured here' is not a delivery
        failure, and a ledger full of those would hide real ones."""
        from services.notifications import CHANNELS
        from services.notifications.base import NotificationPayload

        enabled = [channel for channel in CHANNELS if channel.enabled]
        assert [channel.name.value for channel in enabled] == ["IN_APP"]

        for channel in CHANNELS:
            if channel.enabled:
                continue
            result = channel.send(
                NotificationPayload(user_id=str(uuid.uuid4()), title="t", body="b")
            )
            assert result.delivered is False
            assert "not configured" in (result.failure_reason or "")

    def test_a_new_channel_is_picked_up_without_touching_callers(self, pg_client):
        """The abstraction's actual promise: adding a transport is a
        registration change, not a rewrite of the alert logic.

        A counting channel stands in for SMTP or a push gateway. Nothing about
        the alert service changes; it simply appears in the dispatch.
        """
        from services.alerts import create_alert
        from services.notifications.base import BaseChannel, DeliveryResult

        sent = []

        class CountingChannel(BaseChannel):
            name = NotificationChannel.EMAIL
            enabled = True

            def send(self, payload):
                sent.append(payload)
                return DeliveryResult.ok(self.name.value, "stub-1")

        db.session.commit()

        create_alert(
            category=AlertCategory.OVERSPEED,
            message="Routed through a channel the alert service knows nothing about.",
            audiences=[AlertAudience.VALET_MANAGER.value],
            actor=None,
            notify=True,
        )

        # Prove the dispatch honours an injected channel set.
        from services.notifications import dispatch as dispatch_alerts

        alert = db.session.execute(
            select(Alert).order_by(Alert.created_at.desc()).limit(1)
        ).scalars().first()
        summary = dispatch_alerts(alert, channels=(CountingChannel(),))

        assert sent, "the injected channel received nothing"
        assert summary.delivered >= 1
        assert sent[0].title and sent[0].body

    def test_recipients_exclude_deactivated_accounts(self, pg_client):
        """Notifying a suspended account is how a security alert lands in the
        inbox of someone who should no longer have access."""
        from models.enums import UserStatus
        from services.notifications import resolve_recipients

        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        alert_id = _raise(pg_client, manager).get_json()["data"]["alert_id"]
        alert = db.session.get(Alert, uuid.UUID(alert_id))

        assert manager in resolve_recipients(alert)

        manager.status = UserStatus.SUSPENDED.value
        db.session.commit()
        db.session.expire_all()
        alert = db.session.get(Alert, uuid.UUID(alert_id))

        assert manager not in resolve_recipients(alert)


class TestPublishers:
    def test_a_geofence_alert_is_projected_into_the_feed(self, pg_client):
        from models.enums import AlertSeverity, AlertStatus, AlertType
        from models.security_alert import SecurityAlert
        from services.alerts import raise_from_security_alert

        session = _make_custody()

        source = SecurityAlert(
            custody_session_id=session.id,
            vehicle_id=session.vehicle_id,
            alert_type=AlertType.GEOFENCE_BREACH.value,
            severity=AlertSeverity.HIGH.value,
            status=AlertStatus.OPEN.value,
            title="Vehicle left its zone",
            reason="Last fix was outside the boundary.",
            confidence=0.9,
        )
        db.session.add(source)
        db.session.commit()

        alert, created = raise_from_security_alert(source)

        assert created is True
        assert alert.type == AlertCategory.GEOFENCE_BREACH.value
        assert alert.severity == AlertSeverity.HIGH.value
        assert alert.source_entity_type == "security_alerts"
        assert alert.source_type == AlertType.GEOFENCE_BREACH.value

    def test_a_tamper_alert_becomes_suspicious_movement(self, pg_client):
        """The projection folds the detector's seven types into five; this is
        the mapping that decides where the awkward ones go."""
        from models.enums import AlertSeverity, AlertStatus, AlertType
        from models.security_alert import SecurityAlert
        from services.alerts import raise_from_security_alert

        session = _make_custody()
        source = SecurityAlert(
            custody_session_id=session.id,
            vehicle_id=session.vehicle_id,
            alert_type=AlertType.TAMPER_DETECTED.value,
            severity=AlertSeverity.CRITICAL.value,
            status=AlertStatus.OPEN.value,
            title="Camera obstructed",
            reason="Vision pipeline reports interference.",
            confidence=0.8,
        )
        db.session.add(source)
        db.session.commit()

        alert, _ = raise_from_security_alert(source)

        assert alert.type == AlertCategory.SUSPICIOUS_MOVEMENT.value
        # The precise type survives, so the fold is reversible.
        assert alert.source_type == AlertType.TAMPER_DETECTED.value

    def test_a_potential_damage_alert_says_potential(self, pg_client):
        """The detector flagged it; nobody has confirmed it. Calling it damage
        would overstate what the algorithm established."""
        from models.damage_detection import DamageDetection, DamageType, VehicleRegion
        from models.enums import DamageSeverity, InspectionStage, VerificationStatus
        from models.vehicle_inspection import VehicleInspection
        from services.alerts import raise_potential_damage
        from tests.workflow_helpers import make_user as _mk

        owner = _mk()
        vehicle = make_vehicle(owner.id)
        session = _make_custody(owner=owner, vehicle=vehicle)

        inspection = VehicleInspection(
            custody_session_id=session.id,
            stage=InspectionStage.PICKUP.value,
            photo_count=1,
            captured_at=datetime.now(timezone.utc),
        )
        db.session.add(inspection)
        db.session.flush()

        detection = DamageDetection(
            inspection_id=inspection.id,
            damage_type=DamageType.SCRATCH.value,
            region=VehicleRegion.FRONT_BUMPER.value,
            severity=DamageSeverity.MODERATE.value,
            confidence=0.77,
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1},
            detection_reasoning="Pixel-difference cluster above threshold.",
            verification_status=VerificationStatus.UNVERIFIED.value,
        )
        db.session.add(detection)
        db.session.commit()

        alert, created = raise_potential_damage(detection)

        assert created is True
        assert alert.type == AlertCategory.POTENTIAL_DAMAGE.value
        assert "potential" in alert.message.lower()
        # Staff only: an unconfirmed finding is a task for a manager, not news
        # for a customer.
        assert AlertAudience.CUSTOMER.value not in alert.audiences
