"""Tests for the audit trail: vocabulary, immutability, viewer and timeline.

The requirement these protect is that the log be *evidence*. Four properties
have a testable shape, and each has tests here:

* **the vocabulary is the one specified** -- the fourteen lifecycle events exist,
  and a complete custody actually produces them, rather than the names merely
  being declared somewhere;
* **the log cannot be rewritten** -- not through the API (no write route is
  registered) and not at the database (the trigger refuses), while the two
  writes the design depends on -- the actor tombstone and foreign-key
  de-referencing -- still work;
* **the viewer is restricted and its filters really filter** -- a filter that is
  parsed then ignored is worse than none, because the reader believes the count
  is scoped;
* **the timeline tells the truth about gaps** -- a missing pickup photograph set
  is reported as missing, not silently omitted.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError

from app.extensions import db
from models.audit_log import (
    LIFECYCLE_ACTIONS,
    RETIRED_ACTIONS,
    AuditAction,
    AuditLog,
)
from models.custody_session import CustodySession
from models.enums import CustodyStatus, TicketStatus
from models.valet_ticket import ValetTicket
from tests.workflow_helpers import bearer, make_user, make_vehicle, make_zone, unique
from utils.security import Role

pytestmark = pytest.mark.postgres

AUDIT = "/api/v1/audit"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _manager():
    user = make_user(Role.VALET_MANAGER)
    db.session.commit()
    return user


def _admin():
    user = make_user(Role.ADMIN)
    db.session.commit()
    return user


def _operator():
    user = make_user(Role.VALET_OPERATOR)
    db.session.commit()
    return user


def _record(
    *,
    action: str = AuditAction.VEHICLE_CHECKED_IN.value,
    actor=None,
    entity_type: str = "custody_sessions",
    entity_id=None,
    custody_session_id=None,
    metadata=None,
    occurred_at=None,
    outcome: str = "SUCCESS",
    description: str = "fixture entry",
) -> AuditLog:
    """Insert a log row directly. The test DB is shared, so nothing here asserts
    on absolute counts -- every assertion is scoped to rows this test made."""
    entry = AuditLog(
        actor_id=actor.id if actor is not None else None,
        actor_role=actor.role if actor is not None else None,
        actor_email=actor.email if actor is not None else None,
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id or uuid.uuid4(),
        description=description,
        metadata_=metadata,
        custody_session_id=custody_session_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _open_custody(*, customer=None, zone=None):
    """A ticket in ACTIVE with an open custody session, built through the ORM."""
    customer = customer or make_user(Role.CUSTOMER)
    vehicle = make_vehicle(customer.id)
    zone = zone or make_zone()
    operator = make_user(Role.VALET_OPERATOR)
    db.session.commit()

    ticket = ValetTicket(
        ticket_code=f"VT-TEST-{unique('t')}"[:32],
        customer_id=customer.id,
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
        status=str(CustodyStatus.PARKED),
        dropped_off_at=datetime.now(timezone.utc),
        received_by_id=operator.id,
    )
    db.session.add(session)
    db.session.commit()
    return ticket, session, vehicle


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class TestVocabulary:
    def test_all_fourteen_lifecycle_events_are_declared(self):
        assert len(LIFECYCLE_ACTIONS) == 14
        expected = {
            "USER_LOGIN",
            "VEHICLE_CREATED",
            "TICKET_CREATED",
            "VEHICLE_CHECKED_IN",
            "IMAGE_UPLOADED",
            "CUSTODY_STARTED",
            "GPS_BREACH",
            "SECURITY_ALERT_CREATED",
            "PICKUP_REQUESTED",
            "RETRIEVAL_PREDICTED",
            "PICKUP_IMAGE_CAPTURED",
            "DAMAGE_DETECTED",
            "VEHICLE_PICKED_UP",
            "CUSTODY_COMPLETED",
        }
        assert set(LIFECYCLE_ACTIONS) == expected

    def test_every_lifecycle_event_is_a_real_enum_member(self):
        for name in LIFECYCLE_ACTIONS:
            assert AuditAction(name).value == name

    def test_retired_names_are_gone_from_the_vocabulary(self):
        """A synonym left behind would let the same act be logged twice."""
        declared = {action.value for action in AuditAction}
        for old in RETIRED_ACTIONS:
            assert old not in declared, f"{old} should have been retired"

    def test_the_database_agrees_with_the_enum(self, pg_app):
        """The CHECK constraint is the real gate; if it drifts the enum lies."""
        definition = db.session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_audit_logs_action_valid'"
            )
        ).scalar_one()
        # Every permitted value appears as a single-quoted literal.
        allowed = set(re.findall(r"'([A-Z_]+)'", definition))
        declared = {action.value for action in AuditAction}
        # A lifecycle name the database would reject is a runtime failure waiting
        # to happen, so both directions are checked.
        assert set(LIFECYCLE_ACTIONS) <= allowed
        assert declared == allowed


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #
class TestImmutability:
    def test_no_write_route_is_registered(self):
        from app import create_app

        application = create_app(env_name="testing")
        methods = set()
        for rule in application.url_map.iter_rules():
            if str(rule).startswith("/api/v1/audit"):
                methods |= {m for m in rule.methods if m not in {"HEAD", "OPTIONS"}}
        assert methods == {"GET"}, f"audit routes must be read-only, found {methods}"

    def test_the_database_refuses_a_content_edit(self, pg_app):
        entry = _record()
        with pytest.raises(InternalError) as exc:
            db.session.execute(
                text("UPDATE audit_logs SET description = 'tampered' WHERE id = :id"),
                {"id": str(entry.id)},
            )
        db.session.rollback()
        assert "append-only" in str(exc.value)

    def test_the_database_refuses_backdating(self, pg_app):
        entry = _record()
        with pytest.raises(InternalError) as exc:
            db.session.execute(
                text("UPDATE audit_logs SET occurred_at = now() - interval '1 year' "
                     "WHERE id = :id"),
                {"id": str(entry.id)},
            )
        db.session.rollback()
        assert "append-only" in str(exc.value)

    def test_the_database_refuses_reattribution(self, pg_app):
        """Pointing an entry at a different actor is the useful forgery."""
        entry = _record(actor=_manager())
        with pytest.raises(InternalError):
            db.session.execute(
                text("UPDATE audit_logs SET actor_email = 'other@example.com' "
                     "WHERE id = :id"),
                {"id": str(entry.id)},
            )
        db.session.rollback()

    def test_the_database_refuses_delete(self, pg_app):
        entry = _record()
        with pytest.raises(InternalError) as exc:
            db.session.execute(
                text("DELETE FROM audit_logs WHERE id = :id"), {"id": str(entry.id)}
            )
        db.session.rollback()
        assert "append-only" in str(exc.value)

    def test_the_tombstone_write_still_works(self, pg_app):
        """Blocking this would break how the trail outlives a deleted account."""
        actor = _manager()
        entry = _record(actor=actor)
        db.session.execute(
            text("UPDATE audit_logs SET actor_deleted_at = now() "
                 "WHERE id = :id AND actor_deleted_at IS NULL"),
            {"id": str(entry.id)},
        )
        db.session.commit()
        refreshed = db.session.get(AuditLog, entry.id)
        assert refreshed.actor_deleted_at is not None

    def test_a_tombstone_cannot_be_cleared(self, pg_app):
        actor = _manager()
        entry = _record(actor=actor)
        db.session.execute(
            text("UPDATE audit_logs SET actor_deleted_at = now() WHERE id = :id"),
            {"id": str(entry.id)},
        )
        db.session.commit()
        with pytest.raises(InternalError):
            db.session.execute(
                text("UPDATE audit_logs SET actor_deleted_at = NULL WHERE id = :id"),
                {"id": str(entry.id)},
            )
        db.session.rollback()

    def test_releasing_a_foreign_key_still_works(self, pg_app):
        """ON DELETE SET NULL is implemented as an UPDATE and must not be blocked."""
        _, session, _ = _open_custody()
        entry = _record(custody_session_id=session.id)
        db.session.execute(
            text("UPDATE audit_logs SET custody_session_id = NULL WHERE id = :id"),
            {"id": str(entry.id)},
        )
        db.session.commit()
        refreshed = db.session.get(AuditLog, entry.id)
        assert refreshed.custody_session_id is None


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
class TestAccessControl:
    def test_a_manager_can_read_the_log(self, pg_client):
        response = pg_client.get(AUDIT, headers=bearer(_manager()))
        assert response.status_code == 200
        assert "items" in response.get_json()["data"]

    def test_an_admin_can_read_the_log(self, pg_client):
        response = pg_client.get(AUDIT, headers=bearer(_admin()))
        assert response.status_code == 200

    @pytest.mark.parametrize("role", [Role.CUSTOMER, Role.VALET_OPERATOR])
    def test_nobody_else_can(self, pg_client, role):
        response = pg_client.get(AUDIT, headers=bearer(make_user(role)))
        assert response.status_code == 403, response.get_json()

    def test_anonymous_is_rejected(self, pg_client):
        assert pg_client.get(AUDIT).status_code == 401

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/audit/filters",
            "/api/v1/audit/timeline/00000000-0000-0000-0000-000000000000",
            "/api/v1/audit/vehicles/00000000-0000-0000-0000-000000000000",
        ],
    )
    def test_every_audit_route_is_gated(self, pg_client, path):
        assert pg_client.get(path, headers=bearer(make_user(Role.CUSTOMER))).status_code == 403


# --------------------------------------------------------------------------- #
# Viewer
# --------------------------------------------------------------------------- #
class TestViewer:
    def test_entries_come_back_newest_first(self, pg_client):
        manager = _manager()
        now = datetime.now(timezone.utc)
        made = [
            _record(actor=manager, occurred_at=now - timedelta(minutes=10)),
            _record(actor=manager, occurred_at=now - timedelta(minutes=5)),
            _record(actor=manager, occurred_at=now - timedelta(minutes=1)),
        ]
        ids = {str(entry.id) for entry in made}

        response = pg_client.get(f"{AUDIT}?page_size=200", headers=bearer(manager))
        assert response.status_code == 200
        returned = [e for e in response.get_json()["data"]["items"] if e["id"] in ids]
        assert [e["id"] for e in returned] == [str(e.id) for e in reversed(made)]

    def test_an_entry_carries_every_field_the_brief_lists(self, pg_client):
        manager = _manager()
        entity_id = uuid.uuid4()
        _record(
            actor=manager,
            action=AuditAction.TICKET_CREATED.value,
            entity_type="valet_tickets",
            entity_id=entity_id,
            metadata={"ticket_code": "VT-TEST-1"},
        )
        response = pg_client.get(
            f"{AUDIT}?entity_id={entity_id}", headers=bearer(manager)
        )
        items = response.get_json()["data"]["items"]
        assert len(items) == 1
        entry = items[0]
        # user, action, entity, entity_id, timestamp, IP, metadata
        assert entry["actor_id"] == str(manager.id)
        assert entry["actor_email"] == manager.email
        assert entry["actor_role"] == manager.role
        assert entry["action"] == "TICKET_CREATED"
        assert entry["entity_type"] == "valet_tickets"
        assert entry["entity_id"] == str(entity_id)
        assert entry["occurred_at"]
        assert "ip_address" in entry
        assert entry["metadata"] == {"ticket_code": "VT-TEST-1"}

    def test_filtering_by_action_really_filters(self, pg_client):
        manager = _manager()
        _record(actor=manager, action=AuditAction.GPS_BREACH.value, entity_type="gps_events")
        _record(actor=manager, action=AuditAction.DAMAGE_DETECTED.value,
                entity_type="damage_detections")

        response = pg_client.get(f"{AUDIT}?action=GPS_BREACH", headers=bearer(manager))
        items = response.get_json()["data"]["items"]
        assert items, "expected at least the entry this test wrote"
        assert all(e["action"] == "GPS_BREACH" for e in items)
        assert response.get_json()["data"]["pagination"]["total"] == len(items)

    def test_filtering_by_actor_really_filters(self, pg_client):
        manager = _manager()
        other = _manager()
        _record(actor=manager)
        _record(actor=other)

        response = pg_client.get(f"{AUDIT}?actor_id={manager.id}", headers=bearer(manager))
        data = response.get_json()["data"]
        assert data["pagination"]["total"] >= 1
        assert all(e["actor_id"] == str(manager.id) for e in data["items"])

    def test_filtering_by_custody_session_really_filters(self, pg_client):
        manager = _manager()
        _, session_a, _ = _open_custody()
        _, session_b, _ = _open_custody()
        _record(actor=manager, custody_session_id=session_a.id)
        _record(actor=manager, custody_session_id=session_b.id)

        response = pg_client.get(
            f"{AUDIT}?custody_session_id={session_a.id}", headers=bearer(manager)
        )
        items = response.get_json()["data"]["items"]
        assert items
        assert all(e["custody_session_id"] == str(session_a.id) for e in items)

    def test_lifecycle_only_excludes_supporting_events(self, pg_client):
        manager = _manager()
        _record(actor=manager, action=AuditAction.CUSTODY_STARTED.value)
        _record(actor=manager, action=AuditAction.EXPORT.value)

        response = pg_client.get(f"{AUDIT}?lifecycle_only=true", headers=bearer(manager))
        actions = {e["action"] for e in response.get_json()["data"]["items"]}
        assert "EXPORT" not in actions
        assert actions <= set(LIFECYCLE_ACTIONS)

    def test_date_filters_bound_the_result(self, pg_client):
        manager = _manager()
        now = datetime.now(timezone.utc)
        _record(actor=manager, occurred_at=now - timedelta(days=30))
        recent = _record(actor=manager, occurred_at=now - timedelta(minutes=1))

        # Percent-encoded: a bare "+00:00" in a query string decodes as a
        # space and the parser would reject it.
        date_from = quote((now - timedelta(days=1)).isoformat())
        response = pg_client.get(
            f"{AUDIT}?date_from={date_from}&actor_id={manager.id}",
            headers=bearer(manager),
        )
        assert response.status_code == 200, response.get_json()
        ids = {e["id"] for e in response.get_json()["data"]["items"]}
        assert str(recent.id) in ids
        assert all(
            datetime.fromisoformat(e["occurred_at"]) > now - timedelta(days=1)
            for e in response.get_json()["data"]["items"]
        )

    def test_pagination_reports_the_filtered_total(self, pg_client):
        manager = _manager()
        marker = unique("marker")
        for index in range(5):
            _record(actor=manager, description=f"{marker} {index}")

        first = pg_client.get(
            f"{AUDIT}?page=1&page_size=2&actor_id={manager.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert len(first["items"]) == 2
        assert first["pagination"]["page"] == 1
        assert first["pagination"]["page_size"] == 2
        assert first["pagination"]["total"] >= 5
        assert first["pagination"]["pages"] >= 3

    def test_an_invalid_uuid_is_a_422_not_a_500(self, pg_client):
        response = pg_client.get(f"{AUDIT}?entity_id=not-a-uuid", headers=bearer(_manager()))
        assert response.status_code == 422, response.get_json()

    def test_an_invalid_timestamp_is_a_422_not_a_500(self, pg_client):
        response = pg_client.get(f"{AUDIT}?date_from=yesterday", headers=bearer(_manager()))
        assert response.status_code == 422, response.get_json()

    def test_the_response_declares_itself_immutable(self, pg_client):
        data = pg_client.get(AUDIT, headers=bearer(_manager())).get_json()["data"]
        assert data["mutable"] is False
        assert "append-only" in data["mutability_note"]

    def test_the_vocabulary_is_published_with_the_response(self, pg_client):
        data = pg_client.get(AUDIT, headers=bearer(_manager())).get_json()["data"]
        assert set(data["vocabulary"]["lifecycle"]) == set(LIFECYCLE_ACTIONS)

    def test_filter_options_lists_what_is_actually_present(self, pg_client):
        manager = _manager()
        _record(actor=manager, action=AuditAction.GPS_BREACH.value, entity_type="gps_events")
        data = pg_client.get(f"{AUDIT}/filters", headers=bearer(manager)).get_json()["data"]
        assert "GPS_BREACH" in data["actions"]
        assert "gps_events" in data["entity_types"]
        assert data["lifecycle_actions"] == list(LIFECYCLE_ACTIONS)


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #
class TestTimeline:
    def test_events_are_oldest_first(self, pg_client):
        manager = _manager()
        _, session, _ = _open_custody()
        now = datetime.now(timezone.utc)
        _record(actor=manager, custody_session_id=session.id,
                action=AuditAction.VEHICLE_CHECKED_IN.value,
                occurred_at=now - timedelta(minutes=30))
        _record(actor=manager, custody_session_id=session.id,
                action=AuditAction.PICKUP_REQUESTED.value,
                occurred_at=now - timedelta(minutes=5))

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        actions = [e["action"] for e in data["events"]]
        assert actions == ["VEHICLE_CHECKED_IN", "PICKUP_REQUESTED"]

    def test_missing_lifecycle_stages_are_reported_not_hidden(self, pg_client):
        manager = _manager()
        _, session, _ = _open_custody()
        _record(actor=manager, custody_session_id=session.id,
                action=AuditAction.CUSTODY_STARTED.value)

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert data["lifecycle_progress"]["CUSTODY_STARTED"] is True
        assert data["lifecycle_progress"]["VEHICLE_PICKED_UP"] is False
        assert "VEHICLE_PICKED_UP" in data["lifecycle_missing"]
        assert data["event_count"] == 1

    def test_a_breach_marks_the_custody_as_having_had_an_incident(self, pg_client):
        manager = _manager()
        _, session, _ = _open_custody()
        _record(actor=manager, custody_session_id=session.id,
                action=AuditAction.GPS_BREACH.value, entity_type="gps_events")

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert data["had_security_incident"] is True

    def test_the_timeline_names_the_vehicle_and_zone(self, pg_client):
        manager = _manager()
        _, session, vehicle = _open_custody()
        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert data["vehicle"]["plate_number"] == vehicle.plate_number
        assert data["zone"]["code"] == session.zone.code

    def test_an_unknown_custody_is_a_404(self, pg_client):
        response = pg_client.get(
            f"{AUDIT}/timeline/{uuid.uuid4()}", headers=bearer(_manager())
        )
        assert response.status_code == 404

    def test_a_malformed_custody_id_is_a_422(self, pg_client):
        response = pg_client.get(f"{AUDIT}/timeline/nope", headers=bearer(_manager()))
        assert response.status_code == 422

    def test_events_written_in_one_transaction_keep_insertion_order(self, pg_client):
        """Timestamps collide inside a transaction; ``seq`` must break the tie.

        This is the bug that made a real timeline read "custody started" before
        "vehicle checked in": both are written by the same request, share an
        ``occurred_at``, and an id tiebreak is a random UUID.
        """
        manager = _manager()
        _, session, _ = _open_custody()
        # Two rows, one commit -- identical occurred_at by construction.
        stamp = datetime.now(timezone.utc)
        first = _record(actor=manager, custody_session_id=session.id,
                        action=AuditAction.VEHICLE_CHECKED_IN.value, occurred_at=stamp)
        second = _record(actor=manager, custody_session_id=session.id,
                         action=AuditAction.CUSTODY_STARTED.value, occurred_at=stamp)

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        actions = [e["action"] for e in data["events"]
                   if e["action"] in {"VEHICLE_CHECKED_IN", "CUSTODY_STARTED"}]
        assert actions == ["VEHICLE_CHECKED_IN", "CUSTODY_STARTED"]
        assert first.seq < second.seq

    def test_the_viewer_pages_by_sequence_not_timestamp(self, pg_client):
        """A timestamp tiebreak on a random UUID can repeat or skip a row."""
        manager = _manager()
        stamp = datetime.now(timezone.utc)
        made = [_record(actor=manager, occurred_at=stamp) for _ in range(4)]
        seqs = sorted(e.seq for e in made)
        assert seqs == sorted(set(seqs)), "seq must be unique"

        seen = []
        for page in (1, 2):
            body = pg_client.get(
                f"{AUDIT}?actor_id={manager.id}&page={page}&page_size=2",
                headers=bearer(manager),
            ).get_json()["data"]
            seen.extend(e["seq"] for e in body["items"])
        # Every seq appears at most once across the two pages.
        assert len(seen) == len(set(seen))

    def test_the_timeline_reaches_events_that_predate_the_session(self, pg_client):
        """Ticket and vehicle registration cannot carry a custody_session_id,
        because no session exists yet when they are written."""
        manager = _manager()
        ticket, session, vehicle = _open_custody()
        _record(actor=manager, action=AuditAction.VEHICLE_CREATED.value,
                entity_type="vehicles", entity_id=vehicle.id)
        _record(actor=manager, action=AuditAction.TICKET_CREATED.value,
                entity_type="valet_tickets", entity_id=ticket.id)
        _record(actor=manager, action=AuditAction.CUSTODY_STARTED.value,
                custody_session_id=session.id)

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        actions = [e["action"] for e in data["events"]]
        assert actions == ["VEHICLE_CREATED", "TICKET_CREATED", "CUSTODY_STARTED"]
        scopes = {e["action"]: e["scope"] for e in data["events"]}
        assert scopes["VEHICLE_CREATED"] == "provenance"
        assert scopes["CUSTODY_STARTED"] == "custody"

    def test_the_arriving_login_is_included_even_though_it_predates_the_ticket(
        self, pg_client
    ):
        """The brief's step 1 is a login, which necessarily precedes issuance."""
        manager = _manager()
        customer = make_user(Role.CUSTOMER)
        _, session, _ = _open_custody(customer=customer)
        long_ago = datetime.now(timezone.utc) - timedelta(hours=6)
        _record(actor=customer, action=AuditAction.USER_LOGIN.value,
                entity_type="users", entity_id=customer.id, occurred_at=long_ago)

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        logins = [e for e in data["events"] if e["action"] == "USER_LOGIN"]
        assert len(logins) == 1
        assert logins[0]["scope"] == "login"
        assert data["lifecycle_progress"]["USER_LOGIN"] is True

    def test_unrelated_logins_are_not_swept_into_the_timeline(self, pg_client):
        """An unbounded login list would bury the custody in other sessions."""
        manager = _manager()
        stranger = make_user(Role.CUSTOMER)
        _, session, _ = _open_custody()
        _record(actor=stranger, action=AuditAction.USER_LOGIN.value,
                entity_type="users", entity_id=stranger.id)

        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert all(e["actor_id"] != str(stranger.id) for e in data["events"])

    def test_the_timeline_says_why_it_reaches_beyond_the_session(self, pg_client):
        manager = _manager()
        _, session, _ = _open_custody()
        data = pg_client.get(
            f"{AUDIT}/timeline/{session.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert data["scope_note"]

    def test_a_vehicle_returns_one_timeline_per_custody(self, pg_client):
        """Two visits must not be interleaved into a story that never happened."""
        manager = _manager()
        customer = make_user(Role.CUSTOMER)
        _, first, vehicle = _open_custody(customer=customer)
        # uq_custody_sessions_vehicle_open forbids two open sessions on one
        # vehicle, so the first visit has to close before the second opens --
        # which is what actually happens in the yard.
        first.status = str(CustodyStatus.CHECKED_OUT)
        first.picked_up_at = datetime.now(timezone.utc)
        db.session.commit()

        _, second, _ = _open_custody(customer=customer)
        second.vehicle_id = vehicle.id
        db.session.commit()

        _record(actor=manager, custody_session_id=first.id,
                action=AuditAction.CUSTODY_STARTED.value)
        _record(actor=manager, custody_session_id=second.id,
                action=AuditAction.CUSTODY_COMPLETED.value)

        data = pg_client.get(
            f"{AUDIT}/vehicles/{vehicle.id}", headers=bearer(manager)
        ).get_json()["data"]
        assert data["custody_count"] >= 2
        assert data["vehicle"]["plate_number"] == vehicle.plate_number
        sessions = {t["custody_session_id"] for t in data["timelines"]}
        assert str(first.id) in sessions
        assert str(second.id) in sessions
        # Each timeline holds only its own events.
        for timeline in data["timelines"]:
            for event in timeline["events"]:
                assert event["custody_session_id"] == timeline["custody_session_id"]

    def test_an_unknown_vehicle_is_a_404(self, pg_client):
        response = pg_client.get(
            f"{AUDIT}/vehicles/{uuid.uuid4()}", headers=bearer(_manager())
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
class TestRequestContext:
    def test_a_request_driven_entry_records_the_request_id(self, pg_client):
        """Correlating a log line with the API request that caused it is what
        lets a reviewer follow one action end to end."""
        manager = _manager()
        response = pg_client.get(
            f"{AUDIT}?actor_id={manager.id}", headers=bearer(manager)
        )
        assert response.status_code == 200
        request_id = response.headers.get("X-Request-Id")
        assert request_id, "the API should return the request id it correlates on"

    def test_the_writer_captures_ip_and_user_agent_inside_a_request(self, pg_app):
        from services.audit import record_event

        actor = _manager()
        # Drive a real request so the writer has a request context to read.
        with pg_app.test_request_context(
            "/api/v1/audit",
            headers={"User-Agent": "pytest-agent/1.0", "X-Forwarded-For": "203.0.113.9"},
        ):
            from flask import g

            g.request_id = "test-request-id"
            entry = record_event(
                action=AuditAction.USER_LOGIN,
                entity_type="users",
                entity_id=actor.id,
                description="captured inside a request context",
                actor=actor,
            )
        db.session.commit()

        refreshed = db.session.get(AuditLog, entry.id)
        assert refreshed.ip_address == "203.0.113.9"
        assert refreshed.user_agent == "pytest-agent/1.0"
        assert refreshed.request_id == "test-request-id"

    def test_outside_a_request_those_fields_are_null_not_invented(self, pg_app):
        from services.audit import record_event

        actor = _manager()
        entry = record_event(
            action=AuditAction.USER_LOGIN,
            entity_type="users",
            entity_id=actor.id,
            description="written with no request context",
            actor=actor,
        )
        db.session.commit()
        refreshed = db.session.get(AuditLog, entry.id)
        assert refreshed.ip_address is None
        assert refreshed.user_agent is None

    def test_the_leftmost_forwarded_address_is_used(self, pg_app):
        from flask import g

        from services.audit import record_event

        actor = _manager()
        with pg_app.test_request_context(
            "/api/v1/audit", headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2"}
        ):
            g.request_id = "chain"
            entry = record_event(
                action=AuditAction.USER_LOGIN,
                entity_type="users",
                entity_id=actor.id,
                description="proxy chain",
                actor=actor,
            )
        db.session.commit()
        assert db.session.get(AuditLog, entry.id).ip_address == "198.51.100.7"
