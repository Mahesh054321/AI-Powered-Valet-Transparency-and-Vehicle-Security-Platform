"""Tests for the manager dashboard and report export.

The requirement these tests exist to protect is *"do not create meaningless
charts"*. That is not a stylistic preference that can be checked by eye — it is
a rule with a testable shape: every series must report how many records it
summarises, and must declare itself insufficient below a stated minimum, so a
two-point trend cannot be presented as a finding.

Also covered:

* **filters really filter** — a filter that is parsed and then ignored is worse
  than no filter, because the reader believes the number is scoped;
* **point-in-time and windowed metrics are labelled differently** — mixing them
  on one row of cards is how "40 cars today" gets read off a monthly figure;
* **the report is the same code path as the screen** — a report that disagreed
  with the dashboard it summarised would be the artefact someone filed;
* **exports are audited**.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

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

DASHBOARD = "/api/v1/manager/dashboard"
REPORT = "/api/v1/manager/report"

#: A window that comfortably contains the fixtures these tests create, while
#: staying inside the service's own ``MAX_WINDOW_DAYS`` limit. Fixtures are
#: backdated no more than ~40 days, so 300 days is ample headroom.
WIDE = {
    "date_from": (datetime.now(timezone.utc) - timedelta(days=300)).date().isoformat(),
    "date_to": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
}


def _custody(client, *, status=CustodyStatus.PARKED, days_ago=5, zone=None, closed=False):
    """A ticket plus custody session, backdated so it falls inside a window."""
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    zone = zone or make_zone()
    db.session.commit()

    issued = client.post(
        "/api/v1/tickets", json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert issued.status_code == 201, issued.get_json()
    ticket_id = issued.get_json()["data"]["ticket"]["id"]

    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ticket = db.session.get(ValetTicket, uuid.UUID(ticket_id))
    ticket.issued_at = when
    ticket.status = str(TicketStatus.COMPLETED if closed else TicketStatus.ACTIVE)

    session = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        # A closed session must be CHECKED_OUT: the schema refuses a row that is
        # both picked up and still in an open state.
        status=str(CustodyStatus.CHECKED_OUT) if closed else str(status),
        dropped_off_at=when,
        picked_up_at=when + timedelta(hours=6) if closed else None,
        ready_for_pickup_at=when + timedelta(hours=5, minutes=45) if closed else None,
        retrieval_requested_at=when + timedelta(hours=5, minutes=30) if closed else None,
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
        "when": when,
    }


def _alert(custody_id, *, severity=AlertSeverity.HIGH, status=AlertStatus.OPEN, days_ago=5):
    alert = SecurityAlert(
        custody_session_id=uuid.UUID(custody_id),
        alert_type="GEOFENCE_BREACH",
        severity=str(severity),
        status=str(status),
        title="Vehicle left its zone",
        reason="Last fix was outside the configured boundary.",
        confidence=0.9,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def _manager():
    user = make_user(Role.VALET_MANAGER)
    db.session.commit()
    return user


class TestAccess:
    def test_a_customer_is_refused(self, pg_client):
        """403, not an empty 200: an empty dashboard reads as "nothing to see"."""
        customer = make_user()
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(customer), query_string=WIDE)

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"

    def test_anonymous_is_refused(self, pg_client):
        assert pg_client.get(DASHBOARD, query_string=WIDE).status_code == 401

    @pytest.mark.parametrize("role", [Role.VALET_MANAGER, Role.ADMIN])
    def test_every_manager_role_may_read_it(self, pg_client, role):
        user = make_user(role)
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(user), query_string=WIDE)

        assert response.status_code == 200, response.get_json()

    def test_an_operator_may_not_read_it(self, pg_client):
        """The aggregate view is a management function, not an operational one.

        The capability matrix grants ``report:read_all`` to manager and admin
        only, and the React nav hides Reports from operators. Hiding the link is
        presentation; this is the actual boundary.
        """
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        response = pg_client.get(DASHBOARD, headers=bearer(operator), query_string=WIDE)

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"


class TestShape:
    def test_every_metric_chart_and_table_is_present(self, pg_client):
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        assert set(payload["metrics"]) == {
            "vehicles_today",
            "active_sessions",
            "completed_sessions",
            "pending_pickups",
            "open_alerts",
            "potential_damage",
            "avg_retrieval_minutes",
            "avg_custody_hours",
        }
        assert set(payload["charts"]) == {
            "vehicles_per_day",
            "retrieval_time",
            "security_alerts",
            "damage_detections",
            "custody_duration",
        }
        assert set(payload["tables"]) == {
            "recent_tickets",
            "active_vehicles",
            "security_alerts",
            "damage_cases",
            "pending_disputes",
        }

    def test_open_disputes_are_visible_in_manager_work_queue(self, pg_client):
        dispute = _custody(pg_client, status=CustodyStatus.DISPUTED)
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        rows = payload["tables"]["pending_disputes"]
        assert len(rows) == 1
        assert rows[0]["custody_session_id"] == dispute["custody_id"]
        assert rows[0]["ticket_id"] == dispute["ticket_id"]
        assert rows[0]["vehicle"]["plate_number"] == dispute["vehicle"].plate_number
        assert rows[0]["customer"]["name"] == dispute["customer"].full_name

    def test_every_chart_declares_its_sample_size_and_minimum(self, pg_client):
        """The anti-meaningless-chart rule, stated as a contract.

        A chart that cannot say how much it is made of cannot be judged, so the
        fields are required rather than optional.
        """
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        for name, chart in payload["charts"].items():
            assert "n" in chart, name
            assert "min_points" in chart, name
            assert "sufficient" in chart, name
            assert chart["sufficient"] is (chart["n"] >= chart["min_points"]), name
            # Every series must carry the reasoning, not just the picture.
            assert chart["note"], name

    def test_metrics_say_whether_they_are_windowed(self, pg_client):
        """Point-in-time and period figures must not be interchangeable.

        Nothing on the card row distinguishes them visually, so the payload has
        to, or "40 sessions" gets read as today's count when it covered a month.
        """
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        point_in_time = {"vehicles_today", "active_sessions", "pending_pickups", "open_alerts"}
        for name, metric in payload["metrics"].items():
            assert "windowed" in metric, name
            if name in point_in_time:
                assert metric["windowed"] is False, name
            else:
                assert metric["windowed"] is True, name

    def test_provenance_is_reported(self, pg_client):
        """The payload must say whether the history is generated."""
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        provenance = payload["provenance"]
        assert "contains_generated_history" in provenance
        assert "generated_tickets" in provenance
        assert provenance["note"]

    def test_filter_options_are_served_by_the_backend(self, pg_client):
        """So the UI cannot offer a status the API would reject."""
        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        options = payload["filter_options"]
        assert str(CustodyStatus.PARKED) in options["statuses"]
        assert str(AlertSeverity.CRITICAL) in options["severities"]
        assert isinstance(options["vehicles"], list)


class TestChartsAreNotMeaningless:
    def test_a_sparse_series_declares_itself_insufficient(self, pg_client):
        """A two-day window must not present itself as a trend."""
        today = datetime.now(timezone.utc).date()
        _custody(pg_client, closed=True, days_ago=0)

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(_manager()),
            query_string={
                "date_from": today.isoformat(),
                "date_to": today.isoformat(),
            },
        ).get_json()["data"]

        # Damage and alerts are sparse by nature; on a one-day window they have
        # to report insufficient rather than render a single bar as a pattern.
        for name in ("damage_detections", "security_alerts"):
            chart = payload["charts"][name]
            if chart["n"] < chart["min_points"]:
                assert chart["sufficient"] is False, name

    def test_days_with_no_arrivals_are_zero_not_gaps(self, pg_client):
        """A line that skips empty days joins distant points and invents a slope."""
        _custody(pg_client, days_ago=6)
        today = datetime.now(timezone.utc).date()

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(_manager()),
            query_string={
                "date_from": (today - timedelta(days=6)).isoformat(),
                "date_to": today.isoformat(),
            },
        ).get_json()["data"]

        series = payload["charts"]["vehicles_per_day"]["series"]
        # One point per calendar day in the window, no gaps.
        assert len(series) == 7
        dates = [point["date"] for point in series]
        assert dates == sorted(dates)
        assert len(set(dates)) == 7

        # The arrival this test created must be counted on its own day rather
        # than dropped. The total is not asserted: the test database is
        # session-scoped and never reset, so other tests' tickets fall inside
        # this window and an absolute count would fail for reasons unrelated to
        # what is being checked.
        by_date = {point["date"]: point["value"] for point in series}
        created = (
            datetime.now(timezone.utc) - timedelta(days=6)
        ).date().isoformat()
        assert by_date[created] >= 1
        # Empty days are present as zeros, not omitted.
        assert any(point["value"] == 0 for point in series)

    def test_the_retrieval_chart_reports_its_underlying_record_count(self, pg_client):
        """The point count and the record count are different numbers.

        A chart can have many daily points each resting on one car. Reporting
        only the point count would hide that.
        """
        _custody(pg_client, closed=True, days_ago=3)

        payload = pg_client.get(
            DASHBOARD, headers=bearer(_manager()), query_string=WIDE
        ).get_json()["data"]

        chart = payload["charts"]["retrieval_time"]
        assert "records" in chart
        # Every point must also carry how many retrievals it averages.
        for point in chart["series"]:
            assert point["n"] >= 3


class TestFilters:
    def test_the_severity_filter_restricts_the_alert_table(self, pg_client):
        context = _custody(pg_client, days_ago=4)
        _alert(context["custody_id"], severity=AlertSeverity.CRITICAL)
        _alert(context["custody_id"], severity=AlertSeverity.LOW)
        manager = _manager()

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(manager),
            query_string={**WIDE, "severity": "CRITICAL"},
        ).get_json()["data"]

        rows = payload["tables"]["security_alerts"]
        assert rows, "expected at least one CRITICAL alert"
        assert all(row["severity"] == "CRITICAL" for row in rows)

    def test_the_status_filter_restricts_the_session_counts(self, pg_client):
        _custody(pg_client, status=CustodyStatus.PARKED, days_ago=2)

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(_manager()),
            query_string={**WIDE, "status": str(CustodyStatus.READY_FOR_PICKUP)},
        ).get_json()["data"]

        rows = payload["tables"]["active_vehicles"]
        assert all(row["custody_status"] == "READY_FOR_PICKUP" for row in rows)

    def test_the_vehicle_filter_restricts_the_ticket_table(self, pg_client):
        first = _custody(pg_client, days_ago=3)
        _custody(pg_client, days_ago=2)
        manager = _manager()

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(manager),
            query_string={**WIDE, "vehicle_id": str(first["vehicle"].id)},
        ).get_json()["data"]

        rows = payload["tables"]["recent_tickets"]
        assert rows, "expected the filtered vehicle's ticket"
        assert all(row["vehicle"]["id"] == str(first["vehicle"].id) for row in rows)

    def test_the_date_filter_excludes_records_outside_the_window(self, pg_client):
        old = _custody(pg_client, days_ago=30)
        today = datetime.now(timezone.utc).date()

        payload = pg_client.get(
            DASHBOARD,
            headers=bearer(_manager()),
            query_string={
                "date_from": (today - timedelta(days=2)).isoformat(),
                "date_to": today.isoformat(),
            },
        ).get_json()["data"]

        codes = [row["ticket_code"] for row in payload["tables"]["recent_tickets"]]
        ticket = db.session.get(ValetTicket, uuid.UUID(old["ticket_id"]))
        assert ticket.ticket_code not in codes


class TestValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"date_from": "2026-09-09", "date_to": "2026-06-01"},
            {"date_from": "not-a-date"},
            {"severity": "BOGUS"},
            {"status": "NOPE"},
            {"date_from": "2020-01-01", "date_to": "2026-09-09"},
        ],
    )
    def test_a_bad_filter_is_refused_rather_than_ignored(self, pg_client, params):
        """An ignored filter is worse than a rejected one.

        If the endpoint quietly dropped an unparseable date it would return a
        full aggregate that the caller believes is scoped, which is a wrong
        number presented with confidence.
        """
        manager = _manager()

        response = pg_client.get(DASHBOARD, headers=bearer(manager), query_string=params)

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"

    def test_an_inverted_range_names_both_dates(self, pg_client):
        manager = _manager()

        response = pg_client.get(
            DASHBOARD,
            headers=bearer(manager),
            query_string={"date_from": "2026-09-09", "date_to": "2026-06-01"},
        )

        details = response.get_json()["error"]["details"]
        assert "date_from" in details and "date_to" in details

    def test_the_window_defaults_to_thirty_days(self, pg_client):
        manager = _manager()

        payload = pg_client.get(DASHBOARD, headers=bearer(manager)).get_json()["data"]

        start = date.fromisoformat(payload["filters"]["date_from"])
        end = date.fromisoformat(payload["filters"]["date_to"])
        assert (end - start).days == 29


class TestReport:
    def test_the_report_agrees_with_the_dashboard(self, pg_client):
        """Same code path, so the numbers cannot drift apart.

        This is the assertion that matters: the report is what gets filed, and a
        filed document that contradicted the screen would be worse than none.
        """
        manager = _manager()
        _custody(pg_client, closed=True, days_ago=4)

        dashboard = pg_client.get(
            DASHBOARD, headers=bearer(manager), query_string=WIDE
        ).get_json()["data"]
        report = pg_client.get(
            REPORT, headers=bearer(manager), query_string={**WIDE, "format": "json"}
        ).get_json()["data"]

        for name, metric in dashboard["metrics"].items():
            assert report["metrics"][name]["value"] == metric["value"], name

        for name, chart in dashboard["charts"].items():
            assert report["series"][name]["n"] == chart["n"], name
            assert len(report["series"][name]["points"]) == len(chart["series"]), name

    def test_csv_export_is_parseable_and_labelled(self, pg_client):
        manager = _manager()
        _custody(pg_client, closed=True, days_ago=2)

        response = pg_client.get(
            REPORT, headers=bearer(manager), query_string={**WIDE, "format": "csv"}
        )

        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert "attachment" in response.headers["Content-Disposition"]
        # The screen shows a synthetic-data banner; a CSV cannot, so the header
        # carries it instead rather than letting the file pass as real records.
        assert response.headers["X-Report-Contains-Generated-History"] in {"true", "false"}

        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        assert rows, "expected at least a header and some rows"
        sections = {row["section"] for row in rows}
        assert any(section.startswith("metric") for section in sections)
        assert any(section.startswith("series:") for section in sections)

    def test_an_unknown_format_is_refused(self, pg_client):
        manager = _manager()

        response = pg_client.get(
            REPORT, headers=bearer(manager), query_string={**WIDE, "format": "pdf"}
        )

        assert response.status_code == 422
        assert response.get_json()["error"]["details"]["allowed"] == ["csv", "json"]

    def test_an_export_is_audited(self, pg_client):
        """A manager downloading operational figures is worth recording."""
        manager = _manager()
        before = db.session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == str(AuditAction.EXPORT)
            )
        ).scalar_one()

        response = pg_client.get(
            REPORT, headers=bearer(manager), query_string={**WIDE, "format": "json"}
        )
        assert response.status_code == 200

        after = db.session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == str(AuditAction.EXPORT)
            )
        ).scalar_one()
        assert after == before + 1

        row = db.session.execute(
            select(AuditLog)
            .where(AuditLog.action == str(AuditAction.EXPORT))
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        ).scalars().first()
        assert row.actor_id == manager.id
        assert "JSON" in row.description

    def test_the_report_is_also_refused_to_a_customer(self, pg_client):
        customer = make_user()
        db.session.commit()

        response = pg_client.get(
            REPORT, headers=bearer(customer), query_string={**WIDE, "format": "json"}
        )

        assert response.status_code == 403
