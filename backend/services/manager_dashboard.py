"""Manager analytics: the numbers behind the manager dashboard.

Everything here is a real aggregate over the database. Nothing is estimated,
cached from a previous call, or filled in because a query came back thin.

**The rule this module is built around: a chart must say what it is made of.**
Every series carries ``n`` (how many records it summarises) and ``min_points``
(how few is too few to draw). A trend line through two points is not a trend,
it is a coincidence with axes, and presenting one as insight is the specific
failure this module exists to avoid. The endpoint returns the sample size and
the UI is required to show it and to refuse to draw below the minimum.

**Which numbers respect the date filter, and which do not.** Point-in-time
counts — cars in custody right now, alerts currently open — are *not* windowed,
because "how many cars do we have at this moment" has no date range. Windowed
measures — tickets per day, retrieval times, damage outcomes — are. Each metric
carries ``windowed`` so the UI cannot imply a scope a number does not have.

**Provenance is reported.** If the operational-history generator has been run,
the payload says so and the UI labels the charts as synthetic. A dashboard that
shows 90 days of history without saying where it came from would be the same
kind of overclaim this project avoids everywhere else.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select, text

from database import db
from models.custody_session import CustodySession
from models.damage_detection import DamageDetection, VehicleRegion
from models.enums import (
    AlertSeverity,
    AlertStatus,
    CustodyStatus,
    TicketStatus,
    VehicleStatus,
    VerificationStatus,
)
from models.gps_event import GpsEvent
from models.retrieval_prediction import RetrievalPrediction
from models.security_alert import SecurityAlert
from models.valet_ticket import ValetTicket
from models.vehicle import Vehicle
from models.vehicle_inspection import VehicleInspection
from utils.exceptions import AuthorizationError, ValidationError
from utils.security import AuthContext, Role

logger = logging.getLogger(__name__)

#: Who may read the manager dashboard and the CSV/JSON report export.
#:
#: Deliberately *not* ``is_staff`` -- that admits ``VALET_OPERATOR``, whose job
#: is to move cars, not to read cross-customer analytics. The capability matrix
#: grants ``report:read_all`` to manager and admin only, and the React nav hides
#: Reports from operators; the API is the enforcement boundary, so hiding the
#: link is presentation, not authorisation.
MANAGER_VIEW_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)

#: Custody states that mean the car is physically here.
OPEN_STATUSES = tuple(s.value for s in CustodyStatus if s.is_open)

#: Alert states that are not yet closed.
OPEN_ALERT_STATUSES = (
    AlertStatus.OPEN.value,
    AlertStatus.ACKNOWLEDGED.value,
    AlertStatus.INVESTIGATING.value,
)

#: Severity ordering, highest first, for sorting and "worst" reporting.
SEVERITY_ORDER = (
    AlertSeverity.CRITICAL.value,
    AlertSeverity.HIGH.value,
    AlertSeverity.MEDIUM.value,
    AlertSeverity.LOW.value,
)

#: Below this many points a time series is not drawn as a trend.
MIN_TREND_POINTS = 5

#: Below this many records a chart is reported as insufficient rather than drawn.
MIN_SAMPLE = 10

#: Longest window the endpoint will serve, so a stray ``from`` far in the past
#: cannot make the aggregate scan the whole table on every poll.
MAX_WINDOW_DAYS = 366


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _parse_date(value: Any, *, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValidationError(
            f"'{field}' must be an ISO date (YYYY-MM-DD).",
            details={"field": field, "received": str(value)},
        ) from exc


def _parse_filters(args: dict[str, Any]) -> dict[str, Any]:
    """Normalise and validate the four filters the UI exposes.

    The window is resolved here rather than in each query, so every series is
    filtered by exactly the same range. Building the predicate per chart is how
    two panels on one screen end up describing different periods.
    """
    date_from = _parse_date(args.get("date_from") or args.get("from"), field="date_from")
    date_to = _parse_date(args.get("date_to") or args.get("to"), field="date_to")

    today = datetime.now(timezone.utc).date()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to - timedelta(days=29)
    if date_from > date_to:
        raise ValidationError(
            "'date_from' cannot be after 'date_to'.",
            details={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )
    if (date_to - date_from).days > MAX_WINDOW_DAYS:
        raise ValidationError(
            f"The date range cannot exceed {MAX_WINDOW_DAYS} days.",
            details={"days": (date_to - date_from).days, "max": MAX_WINDOW_DAYS},
        )

    status = args.get("status")
    if status:
        status = str(status).upper()
        allowed = [s.value for s in CustodyStatus]
        if status not in allowed:
            raise ValidationError(
                f"'status' must be one of {allowed}.",
                details={"field": "status", "allowed": allowed},
            )

    severity = args.get("severity")
    if severity:
        severity = str(severity).upper()
        if severity not in SEVERITY_ORDER:
            raise ValidationError(
                f"'severity' must be one of {list(SEVERITY_ORDER)}.",
                details={"field": "severity", "allowed": list(SEVERITY_ORDER)},
            )

    vehicle_id = args.get("vehicle_id") or None
    if vehicle_id:
        vehicle_id = str(vehicle_id)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "severity": severity,
        "vehicle_id": vehicle_id,
    }


def _dt(day: date, *, end: bool = False) -> datetime:
    """Inclusive day boundaries as UTC datetimes."""
    if end:
        return datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc).replace(
            microsecond=999999
        )
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _rank_by(column, ordered: tuple[str, ...] | list[str]):
    """A ``CASE`` expression that sorts ``column`` by its position in ``ordered``.

    Written as explicit ``(condition, result)`` pairs. The dict shorthand looks
    like "match this value", but SQLAlchemy 2.0 treats dict keys as conditions,
    so it emits ``CASE WHEN 'CRITICAL' THEN 0`` and PostgreSQL then tries to
    cast a string to boolean. Explicit comparisons are what was meant.
    """
    return case(
        *[(column == value, index) for index, value in enumerate(ordered)],
        else_=len(ordered),
    )


def _session_predicates(filters: dict[str, Any]) -> list:
    """The window and filters that apply to custody sessions."""
    conditions = [
        CustodySession.dropped_off_at >= _dt(filters["date_from"]),
        CustodySession.dropped_off_at <= _dt(filters["date_to"], end=True),
    ]
    if filters["status"]:
        conditions.append(CustodySession.status == filters["status"])
    if filters["vehicle_id"]:
        conditions.append(CustodySession.vehicle_id == filters["vehicle_id"])
    return conditions


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _metrics(filters: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers.

    Each carries ``windowed`` because they are not all the same kind of number.
    Mixing a point-in-time count with a period total on one row of cards, with
    nothing to distinguish them, is how a reader concludes "we handled 40 cars
    today" from a figure that covered a month.
    """
    today = datetime.now(timezone.utc).date()
    today_start = _dt(today)

    vehicles_today = db.session.execute(
        select(func.count(ValetTicket.id)).where(ValetTicket.issued_at >= today_start)
    ).scalar_one()

    active = db.session.execute(
        select(func.count(CustodySession.id)).where(
            CustodySession.status.in_(OPEN_STATUSES)
        )
    ).scalar_one()

    pending_pickups = db.session.execute(
        select(func.count(CustodySession.id)).where(
            CustodySession.status == CustodyStatus.READY_FOR_PICKUP.value
        )
    ).scalar_one()

    open_alerts = db.session.execute(
        select(func.count(SecurityAlert.id)).where(
            SecurityAlert.status.in_(OPEN_ALERT_STATUSES)
        )
    ).scalar_one()

    # Unverified findings, not all findings. "Potential damage" means nobody has
    # confirmed it yet; counting confirmed damage as potential would be wrong in
    # the alarming direction, and counting everything would be meaningless.
    potential_damage = db.session.execute(
        select(func.count(DamageDetection.id))
        .join(VehicleInspection, VehicleInspection.id == DamageDetection.inspection_id)
        .where(
            DamageDetection.verification_status == VerificationStatus.UNVERIFIED.value,
            VehicleInspection.captured_at >= _dt(filters["date_from"]),
            VehicleInspection.captured_at <= _dt(filters["date_to"], end=True),
        )
    ).scalar_one()

    completed = db.session.execute(
        select(func.count(CustodySession.id)).where(*_session_predicates(filters)).where(
            CustodySession.picked_up_at.is_not(None)
        )
    ).scalar_one()

    # Measured, not predicted: request to ready-for-pickup on sessions that
    # actually finished. A prediction is a claim; this is what happened.
    retrieval = db.session.execute(
        select(
            func.count(RetrievalPrediction.id),
            func.avg(
                func.extract(
                    "epoch",
                    RetrievalPrediction.actual_ready_at - RetrievalPrediction.requested_at,
                )
            )
            / 60.0,
        )
        .join(CustodySession, CustodySession.id == RetrievalPrediction.custody_session_id)
        .where(
            RetrievalPrediction.actual_ready_at.is_not(None),
            RetrievalPrediction.requested_at >= _dt(filters["date_from"]),
            RetrievalPrediction.requested_at <= _dt(filters["date_to"], end=True),
        )
    ).first()

    retrieval_n, retrieval_mean = retrieval[0], retrieval[1]

    custody = db.session.execute(
        select(
            func.count(CustodySession.id),
            func.avg(
                func.extract(
                    "epoch",
                    CustodySession.picked_up_at - CustodySession.dropped_off_at,
                )
            )
            / 3600.0,
        ).where(
            *_session_predicates(filters),
            CustodySession.picked_up_at.is_not(None),
            CustodySession.dropped_off_at.is_not(None),
        )
    ).first()

    total_vehicles = db.session.execute(select(func.count(Vehicle.id))).scalar_one()
    in_custody = db.session.execute(
        select(func.count(Vehicle.id)).where(Vehicle.status == VehicleStatus.IN_CUSTODY.value)
    ).scalar_one()
    geofence_breaches = db.session.execute(
        select(func.count(SecurityAlert.id)).where(
            SecurityAlert.alert_type == "GEOFENCE_BREACH",
            SecurityAlert.occurred_at >= _dt(filters["date_from"]),
            SecurityAlert.occurred_at <= _dt(filters["date_to"], end=True),
        )
    ).scalar_one()

    def metric(label, value, unit, *, windowed, n=None, note=None):
        body = {
            "label": label,
            "value": round(value, 1) if isinstance(value, float) else value,
            "unit": unit,
            "windowed": windowed,
        }
        if n is not None:
            body["n"] = n
        if note:
            body["note"] = note
        return body

    return {
        "vehicles_today": metric(
            "Vehicles checked in today", vehicles_today, "vehicles", windowed=False
        ),
        "total_registered_vehicles": metric(
            "Total registered vehicles", total_vehicles, "vehicles", windowed=False
        ),
        "current_vehicles_in_custody": metric(
            "Current vehicles in custody", in_custody, "vehicles", windowed=False
        ),
        "active_sessions": metric(
            "Active custody sessions", active, "sessions", windowed=False
        ),
        "completed_sessions": metric(
            "Completed sessions", completed, "sessions", windowed=True,
            note="Closed within the selected range.",
        ),
        "pending_pickups": metric(
            "Pending pickups", pending_pickups, "vehicles", windowed=False
        ),
        "open_alerts": metric(
            "Open security alerts", open_alerts, "alerts", windowed=False,
            note="Open, acknowledged or investigating — not yet resolved.",
        ),
        "geofence_breaches": metric(
            "Geofence breaches", geofence_breaches, "breaches", windowed=True,
            note="Recorded boundary breaches in selected range.",
        ),
        "potential_damage": metric(
            "Potential damage cases", potential_damage, "findings", windowed=True,
            note=(
                "Findings no human has verified. Unverified means the detector "
                "flagged it, not that the damage is confirmed."
            ),
        ),
        "avg_retrieval_minutes": metric(
            "Average retrieval time",
            float(retrieval_mean) if retrieval_mean is not None else None,
            "minutes",
            windowed=True,
            n=retrieval_n,
            note="Measured request-to-ready on completed retrievals, not predicted.",
        ),
        "avg_custody_hours": metric(
            "Average custody duration",
            float(custody[1]) if custody[1] is not None else None,
            "hours",
            windowed=True,
            n=custody[0],
        ),
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def _chart(title, unit, series, *, n, kind="line", min_points=MIN_TREND_POINTS, note=None):
    """One chart payload.

    ``sufficient`` is computed here rather than left to the UI, so the rule
    about what may be drawn lives in one place and cannot be quietly dropped by
    a component that forgets to check.
    """
    return {
        "title": title,
        "unit": unit,
        "kind": kind,
        "series": series,
        "n": n,
        "min_points": min_points,
        "sufficient": n >= min_points,
        "note": note,
    }


def _vehicles_per_day(filters: dict[str, Any]) -> dict[str, Any]:
    rows = db.session.execute(
        select(
            func.date_trunc("day", ValetTicket.issued_at).label("day"),
            func.count(ValetTicket.id),
        )
        .where(
            ValetTicket.issued_at >= _dt(filters["date_from"]),
            ValetTicket.issued_at <= _dt(filters["date_to"], end=True),
        )
        .group_by("day")
        .order_by("day")
    ).all()

    # Days with zero arrivals must appear as zero. A line that skips empty days
    # joins two distant points and invents a slope between them.
    counts = {row[0].date().isoformat(): row[1] for row in rows}
    series = []
    day = filters["date_from"]
    while day <= filters["date_to"]:
        key = day.isoformat()
        series.append({"date": key, "value": counts.get(key, 0)})
        day += timedelta(days=1)

    return _chart(
        "Vehicles per day", "vehicles", series, n=len(series), kind="bar",
        min_points=2,
        note="Tickets issued per calendar day. Days with no arrivals are zero, not gaps.",
    )


def _retrieval_time(filters: dict[str, Any]) -> dict[str, Any]:
    """Daily mean measured retrieval time, with the volume behind each point.

    The volume matters: a day whose average rests on one car says nothing, and
    plotting it at the same weight as a day of twenty would be misleading.
    """
    rows = db.session.execute(
        select(
            func.date_trunc("day", RetrievalPrediction.requested_at).label("day"),
            func.count(RetrievalPrediction.id),
            func.avg(
                func.extract(
                    "epoch",
                    RetrievalPrediction.actual_ready_at - RetrievalPrediction.requested_at,
                )
            )
            / 60.0,
        )
        .where(
            RetrievalPrediction.actual_ready_at.is_not(None),
            RetrievalPrediction.requested_at >= _dt(filters["date_from"]),
            RetrievalPrediction.requested_at <= _dt(filters["date_to"], end=True),
        )
        .group_by("day")
        .order_by("day")
    ).all()

    series = [
        {
            "date": row[0].date().isoformat(),
            "value": round(float(row[2]), 1),
            "n": row[1],
        }
        for row in rows
        if row[1] >= 3  # a daily mean over one or two cars is noise, not a trend
    ]
    total_n = sum(point["n"] for point in series)

    return _chart(
        "Average retrieval time", "minutes", series, n=len(series), kind="line",
        note=(
            "Mean measured minutes from retrieval request to ready-for-pickup. "
            "Days with fewer than three retrievals are omitted rather than "
            "plotted, because a one-car average is not a trend."
        ),
    ) | {"records": total_n}


def _alerts_chart(filters: dict[str, Any]) -> dict[str, Any]:
    """Open-vs-closed alerts per week, split by severity.

    Weekly rather than daily: alerts are sparse, and a daily series would be
    mostly zeros with the occasional spike, which reads as a pattern where there
    is only a handful of events.
    """
    severity_condition = (
        [SecurityAlert.severity == filters["severity"]] if filters["severity"] else []
    )
    rows = db.session.execute(
        select(
            func.date_trunc("week", SecurityAlert.occurred_at).label("week"),
            SecurityAlert.severity,
            func.count(SecurityAlert.id),
            func.sum(
                case((SecurityAlert.status.in_(OPEN_ALERT_STATUSES), 1), else_=0)
            ),
        )
        .where(
            SecurityAlert.occurred_at >= _dt(filters["date_from"]),
            SecurityAlert.occurred_at <= _dt(filters["date_to"], end=True),
            *severity_condition,
        )
        .group_by("week", SecurityAlert.severity)
        .order_by("week")
    ).all()

    buckets: dict[str, dict[str, Any]] = {}
    for week, severity, count, open_count in rows:
        key = week.date().isoformat()
        bucket = buckets.setdefault(
            key, {"week": key, "total": 0, "open": 0, "by_severity": {}}
        )
        bucket["total"] += count
        bucket["open"] += int(open_count or 0)
        bucket["by_severity"][severity] = bucket["by_severity"].get(severity, 0) + count

    series = [buckets[key] for key in sorted(buckets)]
    total_n = sum(point["total"] for point in series)

    return _chart(
        "Security alerts", "alerts", series, n=total_n, kind="bar",
        min_points=MIN_SAMPLE,
        note=(
            "Alerts per ISO week, split by severity, with how many are still "
            "open. Weekly because alerts are sparse; a daily series would be "
            "mostly zeros."
        ),
    )


def _damage_chart(filters: dict[str, Any]) -> dict[str, Any]:
    """Findings by verification outcome, over time and by body region.

    The verification split is the interesting part: it shows how often the
    detector was right, how often it was wrong, and how much nobody has looked
    at yet. A chart of raw finding counts alone would hide all three.
    """
    rows = db.session.execute(
        select(
            func.date_trunc("week", VehicleInspection.captured_at).label("week"),
            DamageDetection.verification_status,
            func.count(DamageDetection.id),
        )
        .join(VehicleInspection, VehicleInspection.id == DamageDetection.inspection_id)
        .where(
            VehicleInspection.captured_at >= _dt(filters["date_from"]),
            VehicleInspection.captured_at <= _dt(filters["date_to"], end=True),
        )
        .group_by("week", DamageDetection.verification_status)
        .order_by("week")
    ).all()

    buckets: dict[str, dict[str, Any]] = {}
    for week, status, count in rows:
        key = week.date().isoformat()
        bucket = buckets.setdefault(
            key,
            {
                "week": key,
                "CONFIRMED": 0,
                "REJECTED": 0,
                "UNVERIFIED": 0,
                "total": 0,
            },
        )
        bucket[status] = bucket.get(status, 0) + count
        bucket["total"] += count

    series = [buckets[key] for key in sorted(buckets)]
    total_n = sum(point["total"] for point in series)

    # The region breakdown answers "where does damage actually happen", which a
    # time series cannot. Kept beside it rather than instead of it.
    region_rows = db.session.execute(
        select(DamageDetection.region, func.count(DamageDetection.id))
        .join(VehicleInspection, VehicleInspection.id == DamageDetection.inspection_id)
        .where(
            DamageDetection.verification_status == VerificationStatus.CONFIRMED.value,
            VehicleInspection.captured_at >= _dt(filters["date_from"]),
            VehicleInspection.captured_at <= _dt(filters["date_to"], end=True),
        )
        .group_by(DamageDetection.region)
        .order_by(func.count(DamageDetection.id).desc())
    ).all()

    return _chart(
        "Damage detections", "findings", series, n=total_n, kind="bar",
        min_points=MIN_SAMPLE,
        note=(
            "Findings per week by verification outcome. CONFIRMED means a "
            "manager stood behind the detector; REJECTED means it was a false "
            "positive; UNVERIFIED means nobody has looked yet."
        ),
    ) | {
        "by_region": [
            {"region": region, "count": count} for region, count in region_rows
        ]
    }


def _custody_duration(filters: dict[str, Any]) -> dict[str, Any]:
    """Distribution of custody durations.

    A histogram rather than a trend line. "How long do we hold cars" is a
    question about a distribution; averaging it into one point per day throws
    away the thing a manager actually needs, which is the long tail — the cars
    that sit for eleven hours and block a bay.
    """
    hours = func.extract(
        "epoch", CustodySession.picked_up_at - CustodySession.dropped_off_at
    ) / 3600.0
    rows = db.session.execute(
        select(
            func.floor(hours / 2) * 2,  # 2-hour buckets
            func.count(CustodySession.id),
        )
        .where(
            *_session_predicates(filters),
            CustodySession.picked_up_at.is_not(None),
            CustodySession.dropped_off_at.is_not(None),
        )
        .group_by(func.floor(hours / 2))
        .order_by(func.floor(hours / 2))
    ).all()

    series = [
        {
            "bucket": f"{int(row[0])}-{int(row[0]) + 2}h",
            "value": row[1],
        }
        for row in rows
    ]
    total_n = sum(point["value"] for point in series)

    return _chart(
        "Custody duration", "sessions", series, n=total_n, kind="bar",
        min_points=MIN_SAMPLE,
        note=(
            "Distribution of hours between drop-off and pickup, in two-hour "
            "buckets. Shown as a distribution rather than an average because "
            "the long tail is what occupies bays."
        ),
    )


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def _recent_tickets(filters: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    statement = (
        select(ValetTicket)
        .join(CustodySession, CustodySession.ticket_id == ValetTicket.id, isouter=True)
        .where(
            ValetTicket.issued_at >= _dt(filters["date_from"]),
            ValetTicket.issued_at <= _dt(filters["date_to"], end=True),
        )
    )
    if filters["vehicle_id"]:
        statement = statement.where(ValetTicket.vehicle_id == filters["vehicle_id"])

    tickets = list(
        db.session.execute(
            statement.order_by(ValetTicket.issued_at.desc()).limit(limit)
        ).scalars()
    )

    rows = []
    for ticket in tickets:
        session = next(
            (s for s in ticket.custody_sessions if s.picked_up_at is None),
            ticket.custody_sessions[-1] if ticket.custody_sessions else None,
        )
        duration_hours = None
        if session and session.dropped_off_at:
            end = session.picked_up_at or datetime.now(timezone.utc)
            duration_hours = round((end - session.dropped_off_at).total_seconds() / 3600, 1)

        rows.append(
            {
                "ticket_id": str(ticket.id),
                "ticket_code": ticket.ticket_code,
                "status": ticket.status,
                "custody_status": session.status if session else None,
                "issued_at": ticket.issued_at.isoformat() if ticket.issued_at else None,
                "vehicle": (
                    {
                        "id": str(ticket.vehicle.id),
                        "plate_number": ticket.vehicle.plate_number,
                        "make": ticket.vehicle.make,
                        "model": ticket.vehicle.model,
                    }
                    if ticket.vehicle
                    else None
                ),
                "customer": (
                    {
                        "id": str(ticket.customer.id),
                        "name": ticket.customer.full_name,
                        "email": ticket.customer.email,
                    }
                    if ticket.customer
                    else None
                ),
                "zone_code": session.zone.code if session and session.zone else None,
                "custody_id": str(session.id) if session else None,
                "duration_hours": duration_hours,
                "is_open": bool(session and session.picked_up_at is None),
            }
        )
    return rows


def _active_vehicles(filters: dict[str, Any], *, limit: int = 25) -> list[dict[str, Any]]:
    conditions = [CustodySession.status.in_(OPEN_STATUSES)]
    if filters["status"]:
        conditions.append(CustodySession.status == filters["status"])
    if filters["vehicle_id"]:
        conditions.append(CustodySession.vehicle_id == filters["vehicle_id"])

    sessions = list(
        db.session.execute(
            select(CustodySession)
            .where(*conditions)
            .order_by(CustodySession.dropped_off_at.asc().nulls_last())
            .limit(limit)
        ).scalars()
    )

    alert_counts = dict(
        db.session.execute(
            select(SecurityAlert.custody_session_id, func.count(SecurityAlert.id))
            .where(
                SecurityAlert.custody_session_id.in_([s.id for s in sessions])
                if sessions
                else False,
                SecurityAlert.status.in_(OPEN_ALERT_STATUSES),
            )
            .group_by(SecurityAlert.custody_session_id)
        ).all()
    ) if sessions else {}

    rows = []
    for session in sessions:
        now = datetime.now(timezone.utc)
        held_hours = (
            round((now - session.dropped_off_at).total_seconds() / 3600, 1)
            if session.dropped_off_at
            else None
        )
        rows.append(
            {
                "custody_session_id": str(session.id),
                "custody_status": session.status,
                "vehicle": (
                    {
                        "id": str(session.vehicle.id),
                        "plate_number": session.vehicle.plate_number,
                        "make": session.vehicle.make,
                        "model": session.vehicle.model,
                    }
                    if session.vehicle
                    else None
                ),
                "customer": (
                    {"name": session.ticket.customer.full_name}
                    if session.ticket and session.ticket.customer
                    else None
                ),
                "zone_code": session.zone.code if session.zone else None,
                "dropped_off_at": (
                    session.dropped_off_at.isoformat() if session.dropped_off_at else None
                ),
                "hours_in_custody": held_hours,
                "open_alerts": alert_counts.get(session.id, 0),
            }
        )
    return rows


def _alert_rows(filters: dict[str, Any], *, limit: int = 25) -> list[dict[str, Any]]:
    conditions = [
        SecurityAlert.occurred_at >= _dt(filters["date_from"]),
        SecurityAlert.occurred_at <= _dt(filters["date_to"], end=True),
    ]
    if filters["severity"]:
        conditions.append(SecurityAlert.severity == filters["severity"])
    if filters["vehicle_id"]:
        conditions.append(SecurityAlert.vehicle_id == filters["vehicle_id"])

    alerts = list(
        db.session.execute(
            select(SecurityAlert)
            .where(*conditions)
            # Critical first, then most recent: an operator scanning this table
            # should meet the worst thing at the top.
            .order_by(
                _rank_by(SecurityAlert.severity, SEVERITY_ORDER),
                SecurityAlert.occurred_at.desc(),
            )
            .limit(limit)
        ).scalars()
    )

    return [
        {
            "id": str(alert.id),
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "status": alert.status,
            "title": alert.title,
            "reason": alert.reason,
            "occurred_at": alert.occurred_at.isoformat() if alert.occurred_at else None,
            "is_open": alert.status in OPEN_ALERT_STATUSES,
            "custody_session_id": str(alert.custody_session_id),
            "vehicle": (
                {
                    "id": str(alert.custody_session.vehicle.id),
                    "plate_number": alert.custody_session.vehicle.plate_number,
                }
                if alert.custody_session and alert.custody_session.vehicle
                else None
            ),
            "zone_code": (
                alert.custody_session.zone.code
                if alert.custody_session and alert.custody_session.zone
                else None
            ),
            "acknowledged_by": (
                alert.acknowledged_by.email if alert.acknowledged_by else None
            ),
            "resolved_by": alert.resolved_by.email if alert.resolved_by else None,
        }
        for alert in alerts
    ]


def _damage_rows(filters: dict[str, Any], *, limit: int = 25) -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(DamageDetection)
        .join(VehicleInspection, VehicleInspection.id == DamageDetection.inspection_id)
        .join(CustodySession, CustodySession.id == VehicleInspection.custody_session_id)
        .where(
            VehicleInspection.captured_at >= _dt(filters["date_from"]),
            VehicleInspection.captured_at <= _dt(filters["date_to"], end=True),
            *(
                [CustodySession.vehicle_id == filters["vehicle_id"]]
                if filters["vehicle_id"]
                else []
            ),
        )
        # Unverified first: those are the ones still needing a human decision.
        .order_by(
            _rank_by(
                DamageDetection.verification_status,
                (
                    VerificationStatus.UNVERIFIED.value,
                    VerificationStatus.CONFIRMED.value,
                    VerificationStatus.REJECTED.value,
                ),
            ),
            DamageDetection.created_at.desc(),
        )
        .limit(limit)
    ).scalars()

    out = []
    for detection in rows:
        inspection = detection.inspection
        session = inspection.custody_session if inspection else None
        out.append(
            {
                "id": str(detection.id),
                "damage_type": detection.damage_type,
                "region": detection.region,
                "severity": detection.severity,
                "confidence": float(detection.confidence) if detection.confidence else None,
                "verification_status": detection.verification_status,
                "verified_by": detection.verified_by.email if detection.verified_by else None,
                "verified_at": detection.verified_at.isoformat() if detection.verified_at else None,
                "verification_notes": detection.verification_notes,
                "custody_session_id": str(session.id) if session else None,
                "vehicle": (
                    {
                        "id": str(session.vehicle.id),
                        "plate_number": session.vehicle.plate_number,
                    }
                    if session and session.vehicle
                    else None
                ),
                "captured_at": (
                    inspection.captured_at.isoformat()
                    if inspection and inspection.captured_at
                    else None
                ),
                "stage": inspection.stage if inspection else None,
            }
        )
    return out


def _disputed_sessions(*, limit: int = 25) -> list[dict[str, Any]]:
    """Open disputes are a point-in-time work queue, independent of date filters."""
    sessions = db.session.execute(
        select(CustodySession)
        .where(CustodySession.status == CustodyStatus.DISPUTED.value)
        .order_by(CustodySession.dropped_off_at.asc())
        .limit(limit)
    ).scalars()

    return [
        {
            "custody_session_id": str(session.id),
            "ticket_id": str(session.ticket_id),
            "ticket_code": session.ticket.ticket_code if session.ticket else None,
            "vehicle": (
                {
                    "id": str(session.vehicle.id),
                    "plate_number": session.vehicle.plate_number,
                }
                if session.vehicle
                else None
            ),
            "customer": (
                {"name": session.ticket.customer.full_name}
                if session.ticket and session.ticket.customer
                else None
            ),
            "disputed_at": session.updated_at.isoformat() if session.updated_at else None,
            "dropped_off_at": (
                session.dropped_off_at.isoformat() if session.dropped_off_at else None
            ),
        }
        for session in sessions
    ]


# ---------------------------------------------------------------------------
# Filter options and provenance
# ---------------------------------------------------------------------------
def _filter_options() -> dict[str, Any]:
    """The values the UI can offer in its filter controls.

    Served by the backend so the dropdowns cannot offer a status that does not
    exist in the data, or drift from the vocabulary the API validates against.
    """
    vehicles = list(
        db.session.execute(
            select(Vehicle.id, Vehicle.plate_number, Vehicle.make, Vehicle.model)
            .join(CustodySession, CustodySession.vehicle_id == Vehicle.id)
            .distinct()
            .order_by(Vehicle.plate_number)
        ).all()
    )
    return {
        "statuses": [s.value for s in CustodyStatus],
        "severities": list(SEVERITY_ORDER),
        "ticket_statuses": [s.value for s in TicketStatus],
        "regions": [r.value for r in VehicleRegion],
        "vehicles": [
            {
                "id": str(row[0]),
                "plate_number": row[1],
                "label": f"{row[1]} · {' '.join(p for p in (row[2], row[3]) if p)}",
            }
            for row in vehicles
        ],
    }


def _provenance() -> dict[str, Any]:
    """Whether the figures include generated operational history.

    Reported rather than hidden. If a reviewer sees ninety days of history they
    are entitled to know whether it records real operations or was written by a
    script, and the answer here is often "generated".
    """
    row = db.session.execute(
        text("SELECT value FROM schema_meta WHERE key = 'operational_history'")
    ).first()
    generated_tickets = db.session.execute(
        select(func.count(ValetTicket.id)).where(
            ValetTicket.metadata_["origin"].as_string() == "operational_history"
        )
    ).scalar_one()

    return {
        "contains_generated_history": generated_tickets > 0,
        "generated_tickets": generated_tickets,
        "generator_meta": row[0] if row else None,
        "note": (
            "This dataset includes synthetic operational history written by "
            "generate_operational_history.py so the charts have enough records "
            "to describe. Those rows are marked and are not a record of real "
            "valet operations."
            if generated_tickets
            else "No generated operational history is present; every figure "
            "comes from records created through the application."
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_dashboard(actor: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    """Compose the whole manager screen for one set of filters.

    ``build_report`` delegates here, so this is the only gate the export path
    needs: tightening it once closes both the screen and the download."""
    if actor.role not in MANAGER_VIEW_ROLES:
        raise AuthorizationError(
            f"Role '{actor.role}' cannot read the manager dashboard. "
            f"Required: {', '.join(MANAGER_VIEW_ROLES)}.",
            details={"role": actor.role, "required": list(MANAGER_VIEW_ROLES)},
        )

    filters = _parse_filters(args)

    gps_sources_query = db.session.execute(
        select(GpsEvent.source, func.count(GpsEvent.id))
        .where(
            GpsEvent.recorded_at >= _dt(filters["date_from"]),
            GpsEvent.recorded_at <= _dt(filters["date_to"], end=True),
        )
        .group_by(GpsEvent.source)
    ).all()
    gps_source_distribution = {row[0]: row[1] for row in gps_sources_query}

    return {
        "generated_for": {"user_id": actor.user_id, "role": actor.role},
        "filters": {
            "date_from": filters["date_from"].isoformat(),
            "date_to": filters["date_to"].isoformat(),
            "status": filters["status"],
            "severity": filters["severity"],
            "vehicle_id": filters["vehicle_id"],
        },
        "filter_options": _filter_options(),
        "provenance": _provenance(),
        "metrics": _metrics(filters),
        "gps_source_distribution": gps_source_distribution,
        "charts": {
            "vehicles_per_day": _vehicles_per_day(filters),
            "retrieval_time": _retrieval_time(filters),
            "security_alerts": _alerts_chart(filters),
            "damage_detections": _damage_chart(filters),
            "custody_duration": _custody_duration(filters),
        },
        "tables": {
            "recent_tickets": _recent_tickets(filters),
            "active_vehicles": _active_vehicles(filters),
            "security_alerts": _alert_rows(filters),
            "damage_cases": _damage_rows(filters),
            "pending_disputes": _disputed_sessions(),
        },
    }


def build_report(actor: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    """The same figures, flattened for export.

    Deliberately the *same* queries as the dashboard rather than a parallel
    implementation: a report that disagreed with the screen it summarised would
    be worse than no report, because it would be the artefact someone filed.
    """
    payload = build_dashboard(actor, args)
    charts = payload["charts"]

    return {
        "report": "valet_operations",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": {"user_id": actor.user_id, "role": actor.role},
        "filters": payload["filters"],
        "provenance": payload["provenance"],
        "metrics": payload["metrics"],
        "series": {
            name: {
                "title": chart["title"],
                "unit": chart["unit"],
                "n": chart["n"],
                "sufficient": chart["sufficient"],
                "points": chart["series"],
            }
            for name, chart in charts.items()
        },
        "tables": payload["tables"],
    }


__all__ = ["build_dashboard", "build_report"]
