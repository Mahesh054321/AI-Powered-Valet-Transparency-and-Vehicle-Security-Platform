"""Vehicle security and geofencing: ingest telemetry, evaluate it, raise alerts.

This module owns the workflow. It holds no geometry -- that lives in
:mod:`ai.anomaly.geofence`, which knows nothing about Flask or SQLAlchemy -- and
no telemetry generation, which lives in :mod:`ai.synthetic.gps_tracks`.

Four rules shape everything here.

**Provenance is recorded, never inferred.** Every fix stores whether it came
from the simulator, a receiver, or a keyboard. The tracking dashboard labels
simulated telemetry explicitly because the project mandate is that a demo must
not be mistakable for a real deployment.

**Alerts are raised on state change, not per fix.** A vehicle driving away from
the yard produces a dozen consecutive fixes that are all outside the zone.
Raising twelve identical alerts would bury the one that matters, so a finding
only becomes an alert when there is no open alert of that type for the same
session within ``ALERT_COOLDOWN``. The suppressed findings are still recorded on
their fixes and counted in the response, so nothing is hidden -- only the
notification is throttled.

**A verdict without its inputs is not stored.** Each fix keeps the zone it was
evaluated against, the signed distance to the boundary, and its speed, so the
stored verdict can always be re-derived from the row beside it.

**Telemetry rows are never audited individually.** ``gps_events`` is
append-only and high-volume; the row *is* the record. What gets audited is
everything a human did: raising, acknowledging and resolving alerts, generating
a simulated track, and reconfiguring a geofence.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from ai.anomaly import geofence as engine
from ai.synthetic.gps_tracks import TRACK_SCENARIOS, generate_track, scenario_catalogue
from app.extensions import db
from models.audit_log import AuditAction, AuditOutcome
from models.custody_session import CustodySession
from models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    CustodyStatus,
    MovementStatus,
    TelemetrySource,
    ZoneStatus,
    validate_status,
)
from models.gps_event import GpsEvent
from models.parking_zone import ParkingZone
from models.security_alert import SecurityAlert
from services import security_ml
from services.audit import record_event
from services.security_ml import security_model
from utils.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from utils.security import AuthContext, Role
from utils.validation import (
    validate_choice,
    validate_latitude,
    validate_longitude,
    validate_text,
    validate_uuid,
)

logger = logging.getLogger(__name__)

#: Who may record a position for a vehicle in custody.
TELEMETRY_ROLES = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)
#: Who may define or move a geofence. Moving a boundary changes the meaning of
#: every future fix, so it is a manager-level act rather than operator-level.
GEOFENCE_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)
#: Who may acknowledge or resolve a security alert. Deliberately the same
#: narrowness as damage verification: the person driving the vehicle is not the
#: one who dismisses the alert about it.
ALERT_TRIAGE_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)

#: Custody states in which a retrieval is underway. Surfaced to the dashboard.
RETRIEVAL_STATUSES = (
    CustodyStatus.RETRIEVAL_REQUESTED.value,
    CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
    CustodyStatus.READY_FOR_PICKUP.value,
)

#: Custody states in which the vehicle is *allowed* to move.
#:
#: Wider than ``RETRIEVAL_STATUSES`` on purpose. A valet driving a just-received
#: car to its bay is moving legitimately, and so is one relocating a vehicle
#: between bays; treating only retrieval as authorised movement flagged every
#: arrival as an anomaly, which is exactly the noise that teaches people to
#: ignore a security dashboard.
MOVEMENT_EXPECTED_STATUSES = (
    CustodyStatus.CHECKED_IN.value,
    CustodyStatus.IN_TRANSIT.value,
    *RETRIEVAL_STATUSES,
)

#: How long an open alert suppresses new alerts of the same type.
ALERT_COOLDOWN = timedelta(minutes=10)
#: How many preceding fixes the movement-pattern rules look at.
PRIOR_FIX_WINDOW = 4
#: Upper bound on a single ingest request, so one call cannot stall the worker.
MAX_BATCH_FIXES = 500
#: Upper bound on a generated track.
MAX_TRACK_FIXES = 400
#: Largest geofence radius accepted, in metres. A yard is not a county.
MAX_ZONE_RADIUS_METRES = 5_000.0
#: Default zone speed limit used when evaluating fixes.
DEFAULT_ZONE_SPEED_LIMIT_KMPH = 20.0
#: How far in the future a fix may be timestamped before it is rejected.
#:
#: Real receivers drift by seconds, so some tolerance is needed. But an alert
#: records when it occurred, and the schema requires acknowledgement to come
#: *after* that -- so a fix timestamped hours ahead produces an alert nobody can
#: acknowledge until the clock catches up. Refusing the fix at the door is
#: cheaper than discovering that during an incident.
MAX_CLOCK_SKEW_SECONDS = 300.0


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _audit(
    *,
    action: str,
    outcome: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor: AuthContext,
    failure_reason: str | None = None,
    new_values: dict | None = None,
    metadata: dict | None = None,
    custody_session_id: uuid.UUID | str | None = None,
) -> None:
    """Append an audit entry. A thin delegate to :func:`services.audit.record_event`."""
    record_event(
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        actor=actor,
        failure_reason=failure_reason,
        new_values=new_values,
        metadata=metadata,
        custody_session_id=custody_session_id,
    )


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def _require_role(actor: AuthContext, roles: tuple[str, ...], *, action: str) -> None:
    if actor.role not in roles:
        raise AuthorizationError(
            f"Role '{actor.role}' cannot {action}. Required: {', '.join(roles)}.",
            details={"role": actor.role, "required": list(roles)},
        )


def _load_session(custody_id: uuid.UUID | str) -> CustodySession:
    session = db.session.get(CustodySession, custody_id)
    if session is None:
        # 404 rather than 403: whether someone else's custody session exists is
        # not information this API should leak.
        raise NotFoundError(
            "No custody session was found with that id.",
            details={"custody_id": str(custody_id)},
        )
    return session


def _require_tracking_access(actor: AuthContext, session: CustodySession) -> None:
    """Staff track any vehicle; a customer tracks only their own."""
    if actor.role in TELEMETRY_ROLES:
        return
    ticket = session.ticket
    if ticket is not None and str(ticket.customer_id) == actor.user_id:
        return
    raise NotFoundError(
        "No custody session was found with that id.",
        details={"custody_id": str(session.id)},
    )


# ---------------------------------------------------------------------------
# Zone geometry helpers
# ---------------------------------------------------------------------------
def _zone_centre(zone: ParkingZone) -> tuple[float, float]:
    """The zone's declared centre, or the polygon centroid if it has none.

    Zones created before centre coordinates were mandatory still have a usable
    shape, so the centroid is derived rather than leaving the fix unlocatable.
    """
    if zone.centre_latitude is not None and zone.centre_longitude is not None:
        return float(zone.centre_latitude), float(zone.centre_longitude)
    ring = zone.geofence_polygon or []
    latitudes = [float(vertex[1]) for vertex in ring]
    longitudes = [float(vertex[0]) for vertex in ring]
    return sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)


def _effective_radius(zone: ParkingZone) -> float:
    """The zone's declared radius, or the furthest vertex from the centre."""
    if zone.radius_metres:
        return float(zone.radius_metres)
    centre = _zone_centre(zone)
    return max(
        (
            engine.haversine_m(centre[0], centre[1], float(v[1]), float(v[0]))
            for v in (zone.geofence_polygon or [])
        ),
        default=0.0,
    )


def _resolve_zone(
    session: CustodySession, explicit_zone_id: uuid.UUID | str | None
) -> ParkingZone | None:
    """The zone a fix should be judged against.

    Resolution is explicit: an override wins, otherwise the zone the custody
    session was assigned. There is no "nearest zone" fallback, because guessing
    which boundary a vehicle was supposed to stay inside would make the verdict
    depend on something the reviewer cannot see.
    """
    if explicit_zone_id is not None:
        zone = db.session.get(ParkingZone, explicit_zone_id)
        if zone is None:
            raise NotFoundError(
                "No parking zone was found with that id.",
                details={"zone_id": str(explicit_zone_id)},
            )
        return zone
    return session.zone


def _engine_config(zone: ParkingZone | None) -> engine.GeofenceConfig:
    buffer_metres = float(zone.buffer_metres) if zone is not None else 25.0
    return engine.GeofenceConfig(
        buffer_metres=buffer_metres,
        zone_speed_limit_kmph=DEFAULT_ZONE_SPEED_LIMIT_KMPH,
    )


def _prior_fixes(
    custody_session_id: uuid.UUID, before: datetime, limit: int = PRIOR_FIX_WINDOW
) -> tuple[engine.PriorFix, ...]:
    """The fixes immediately preceding ``before``, oldest first."""
    rows = (
        db.session.execute(
            select(GpsEvent)
            .where(
                GpsEvent.custody_session_id == custody_session_id,
                GpsEvent.recorded_at < before,
            )
            .order_by(GpsEvent.recorded_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return tuple(
        reversed(
            [
                engine.PriorFix(
                    latitude=float(row.latitude),
                    longitude=float(row.longitude),
                    recorded_at=row.recorded_at,
                    speed_kmph=float(row.speed_kmph) if row.speed_kmph is not None else None,
                )
                for row in rows
            ]
        )
    )


# ---------------------------------------------------------------------------
# Alert de-duplication
# ---------------------------------------------------------------------------
_OPEN_ALERT_STATUSES = (
    AlertStatus.OPEN.value,
    AlertStatus.ACKNOWLEDGED.value,
    AlertStatus.INVESTIGATING.value,
)


def _recent_open_alert(
    custody_session_id: uuid.UUID, alert_type: str, occurred_at: datetime
) -> SecurityAlert | None:
    """An open alert of the same type inside the cooldown window, if any."""
    return db.session.execute(
        select(SecurityAlert)
        .where(
            SecurityAlert.custody_session_id == custody_session_id,
            SecurityAlert.alert_type == alert_type,
            SecurityAlert.status.in_(_OPEN_ALERT_STATUSES),
            SecurityAlert.occurred_at >= occurred_at - ALERT_COOLDOWN,
        )
        .order_by(SecurityAlert.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _raise_alerts(
    *,
    session: CustodySession,
    event: GpsEvent,
    zone: ParkingZone | None,
    verdict: engine.MovementVerdict,
    actor: AuthContext,
) -> tuple[list[SecurityAlert], list[str]]:
    """Turn findings into alerts, throttled by type.

    Returns the alerts created and the alert types that were suppressed because
    an equivalent alert is already open. Suppression is reported to the caller
    rather than swallowed: a dashboard that silently stops alerting is worse
    than one that over-alerts.
    """
    created: list[SecurityAlert] = []
    suppressed: list[str] = []
    if not verdict.findings:
        return created, suppressed

    occurred_at = event.recorded_at or _now()
    for finding in verdict.findings:
        validate_status(finding.alert_type, AlertType, field="alert_type")
        validate_status(finding.severity, AlertSeverity, field="severity")
        if _recent_open_alert(session.id, finding.alert_type, occurred_at) is not None:
            suppressed.append(finding.alert_type)
            continue
        alert = SecurityAlert(
            custody_session_id=session.id,
            vehicle_id=session.vehicle_id,
            zone_id=zone.id if zone is not None else None,
            gps_event_id=event.id,
            alert_type=finding.alert_type,
            severity=finding.severity,
            status=AlertStatus.OPEN.value,
            title=finding.title[:200],
            description=(
                f"Detected at {occurred_at.isoformat()} for vehicle "
                f"{session.vehicle.plate_number if session.vehicle else 'unknown'}."
            ),
            reason=finding.reason[:1000],
            confidence=finding.confidence,
            evidence={
                **verdict.evidence,
                "finding": finding.alert_type,
                "finding_status": finding.status,
                "fix_id": str(event.id),
            },
            occurred_at=occurred_at,
        )
        db.session.add(alert)
        created.append(alert)

    if created:
        db.session.flush()
        # A fix can arrive before a zone has been assigned; the detector still
        # classifies it and still raises. The audit entry must survive that
        # rather than assuming a zone is present.
        zone_code = zone.code if zone is not None else None
        for alert in created:
            # GPS_BREACH is the observation; SECURITY_ALERT_CREATED is the
            # response to it. Both are recorded because they answer different
            # questions -- "did the vehicle leave the fence?" and "was anyone
            # told?" -- and a system that only logged the second would leave no
            # trace of the underlying movement when an alert was suppressed or
            # mis-triaged.
            if alert.alert_type == str(AlertType.GEOFENCE_BREACH):
                _audit(
                    action=AuditAction.GPS_BREACH,
                    outcome=AuditOutcome.SUCCESS.value,
                    entity_type="gps_events",
                    entity_id=event.id,
                    description=(
                        f"Vehicle left geofence {zone_code}: {alert.reason}"
                    ),
                    actor=actor,
                    custody_session_id=session.id,
                    new_values={
                        "latitude": event.latitude,
                        "longitude": event.longitude,
                        "movement_status": event.movement_status,
                    },
                    metadata={
                        "zone_code": zone_code,
                        "distance_outside_metres": (
                            verdict.evidence or {}
                        ).get("distance_outside_metres"),
                        "is_simulated": event.is_simulated,
                        "telemetry_source": event.source,
                    },
                )
            _audit(
                action=AuditAction.SECURITY_ALERT_CREATED,
                outcome=AuditOutcome.SUCCESS.value,
                entity_type="security_alerts",
                entity_id=alert.id,
                description=f"{alert.alert_type} ({alert.severity}): {alert.reason}",
                actor=actor,
                custody_session_id=session.id,
                new_values={
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "custody_session_id": str(session.id),
                    "gps_event_id": str(event.id),
                },
                metadata={
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "zone_code": zone_code,
                    "is_simulated": event.is_simulated,
                },
            )
    # Project the new security alerts into the centralized feed. Done here, at
    # the point the fact is established, rather than by a poller that would
    # have to guess which rows it had already seen.
    _publish_to_feed(created, actor=actor)

    return created, suppressed


def _publish_to_feed(alerts: list[SecurityAlert], *, actor=None) -> None:
    """Mirror geofence alerts into the centralized feed.

    Best-effort by design: the security alert is the authoritative record and is
    already flushed. If projecting it into the feed fails, that must not roll
    back the detection -- losing a recorded breach because a notification could
    not be built would be the wrong trade.
    """
    if not alerts:
        return
    try:
        from services.alerts import raise_from_security_alert

        for alert in alerts:
            raise_from_security_alert(alert, actor=actor)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "Failed to project %d security alert(s) into the feed", len(alerts)
        )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
def _parse_timestamp(value: Any, *, field: str = "recorded_at") -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"'{field}' must be an ISO-8601 timestamp.",
            details={"field": field, "received": str(value)},
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_float(value: Any, *, field: str, minimum: float | None = None) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"'{field}' must be a number.", details={"field": field}
        ) from exc
    if minimum is not None and number < minimum:
        raise ValidationError(
            f"'{field}' must be at least {minimum}.",
            details={"field": field, "received": number},
        )
    return number


def _normalise_fix_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept the brief's field names alongside the schema's own.

    The brief specifies ``custody_id``, ``speed`` and ``timestamp``; the schema
    calls them ``custody_session_id``, ``speed_kmph`` and ``recorded_at``. Both
    are accepted so the API matches the specification and the storage keeps its
    consistent naming.
    """
    custody_id = payload.get("custody_session_id") or payload.get("custody_id")
    if custody_id in (None, ""):
        raise ValidationError(
            "'custody_id' is required.",
            details={"field": "custody_id", "missing_fields": ["custody_id"]},
        )
    return {
        "custody_session_id": validate_uuid(custody_id, field="custody_id"),
        "latitude": validate_latitude(payload.get("latitude")),
        "longitude": validate_longitude(payload.get("longitude")),
        "speed_kmph": _optional_float(
            payload.get("speed_kmph", payload.get("speed")),
            field="speed_kmph",
            minimum=0.0,
        ),
        "heading_degrees": _optional_float(
            payload.get("heading_degrees"), field="heading_degrees", minimum=0.0
        ),
        "accuracy_metres": _optional_float(
            payload.get("accuracy_metres"), field="accuracy_metres", minimum=0.0
        ),
        "recorded_at": _parse_timestamp(
            payload.get("recorded_at", payload.get("timestamp")), field="recorded_at"
        ),
        "zone_id": (
            validate_uuid(payload["zone_id"], field="zone_id")
            if payload.get("zone_id")
            else None
        ),
        "device_id": (
            validate_text(str(payload["device_id"])[:64], field="device_id", max_length=64)
            if payload.get("device_id")
            else None
        ),
    }


def _apply_ml_classification(
    rule_verdict: engine.MovementVerdict,
    *,
    latitude: float,
    longitude: float,
    speed_kmph: float | None,
    heading_degrees: float | None,
    recorded_at: datetime,
    priors: tuple[engine.PriorFix, ...],
    buffer_metres: float,
) -> tuple[engine.MovementVerdict, security_ml.MlOpinion | None]:
    """Score a fix with the prototype classifier and merge the two opinions.

    Wrapped so that no failure in the ML layer can break telemetry ingestion.
    The rule verdict is always returned even if feature extraction or scoring
    raises: a GPS fix that cannot be stored because a prototype model choked on
    it would be a far worse outcome than a fix stored without an ML opinion.
    """
    try:
        features = security_ml.features_from_verdict(
            verdict=rule_verdict,
            latitude=latitude,
            longitude=longitude,
            speed_kmph=speed_kmph,
            heading_degrees=heading_degrees,
            recorded_at=recorded_at,
            priors=priors,
            buffer_metres=buffer_metres,
        )
        combined = security_model.evaluate(rule_verdict, features)
    except Exception as exc:  # noqa: BLE001 - ingestion must not depend on the model
        logger.warning("Security ML unavailable for this fix, using rule verdict: %s", exc)
        return rule_verdict, None
    return combined.verdict, combined.opinion


def ingest_fix(
    payload: dict[str, Any],
    actor: AuthContext,
    *,
    source: str = TelemetrySource.MANUAL.value,
    device_id: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Record one position fix, classify it, and raise any alerts it triggers."""
    _require_role(actor, TELEMETRY_ROLES, action="record GPS telemetry")
    validate_status(source, TelemetrySource, field="source")
    normalised = _normalise_fix_payload(payload)
    session = _load_session(normalised["custody_session_id"])

    zone = _resolve_zone(session, normalised["zone_id"])
    recorded_at = normalised["recorded_at"] or _now()
    now = _now()
    skew = (recorded_at - now).total_seconds()
    if skew > MAX_CLOCK_SKEW_SECONDS:
        raise ValidationError(
            f"'timestamp' is {skew / 60.0:.0f} minutes in the future. A fix cannot be "
            f"more than {MAX_CLOCK_SKEW_SECONDS / 60.0:.0f} minutes ahead of the "
            "server clock: any alert it raises would be unacknowledgeable until "
            "that time passed. Check the reporting device's clock.",
            details={
                "field": "timestamp",
                "received": recorded_at.isoformat(),
                "server_time": now.isoformat(),
                "maximum_skew_seconds": MAX_CLOCK_SKEW_SECONDS,
            },
        )
    priors = _prior_fixes(session.id, recorded_at)
    config = _engine_config(zone)

    centre = _zone_centre(zone) if zone is not None else None
    rule_verdict = engine.evaluate_fix(
        latitude=normalised["latitude"],
        longitude=normalised["longitude"],
        zone_ring=zone.geofence_polygon if zone is not None else None,
        zone_centre=centre,
        zone_radius_metres=_effective_radius(zone) if zone is not None else None,
        speed_kmph=normalised["speed_kmph"],
        heading_degrees=normalised["heading_degrees"],
        recorded_at=recorded_at,
        prior_fixes=priors,
        movement_expected=session.status in MOVEMENT_EXPECTED_STATUSES,
        config=config,
    )

    # The prototype classifier weighs in on the same measurement. It may raise
    # the verdict and may never lower it; a rule BREACH raises its alert whatever
    # the model thinks. See ``services/security_ml.py`` for why the asymmetry is
    # load-bearing rather than cautious. When no artifact is trained, this
    # returns the rule verdict untouched and ``ml`` stays None.
    verdict, opinion = _apply_ml_classification(
        rule_verdict,
        latitude=normalised["latitude"],
        longitude=normalised["longitude"],
        speed_kmph=normalised["speed_kmph"],
        heading_degrees=normalised["heading_degrees"],
        recorded_at=recorded_at,
        priors=priors,
        buffer_metres=config.buffer_metres,
    )
    validate_status(verdict.status, MovementStatus, field="movement_status")

    event = GpsEvent(
        custody_session_id=session.id,
        recorded_at=recorded_at,
        latitude=normalised["latitude"],
        longitude=normalised["longitude"],
        speed_kmph=normalised["speed_kmph"],
        heading_degrees=normalised["heading_degrees"],
        accuracy_metres=normalised["accuracy_metres"],
        source=source,
        device_id=device_id or normalised["device_id"],
        zone_id=zone.id if zone is not None else None,
        is_within_zone=verdict.is_within_zone,
        distance_from_zone_metres=verdict.distance_from_boundary_metres,
        movement_status=verdict.status,
        ml_classification=opinion.classification if opinion is not None else None,
        ml_probability=opinion.probability if opinion is not None else None,
        ml_model_version=opinion.model_version if opinion is not None else None,
    )
    db.session.add(event)
    db.session.flush()

    alerts, suppressed = _raise_alerts(
        session=session, event=event, zone=zone, verdict=verdict, actor=actor
    )
    if commit:
        db.session.commit()

    return {
        "gps_event": fix_dict(event),
        "movement_status": verdict.status,
        "rule_movement_status": rule_verdict.status,
        "escalated_by_ml": rule_verdict.status != verdict.status,
        "reasons": verdict.reasons,
        "evidence": verdict.evidence,
        # Present only when a model actually ran. A null here means "no
        # classifier available", not "the classifier saw nothing wrong" - the
        # distinction matters, and collapsing it would overstate what happened.
        "ml": opinion.as_dict() if opinion is not None else None,
        "alerts_raised": [alert_dict(alert) for alert in alerts],
        "alerts_suppressed": suppressed,
    }


def ingest_batch(
    payloads: list[dict[str, Any]],
    actor: AuthContext,
    *,
    source: str = TelemetrySource.MANUAL.value,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Ingest several fixes in one transaction.

    Fixes are processed in timestamp order, not submission order: the
    movement-pattern rules compare each fix against the ones that came before
    it, so ingesting them out of sequence would produce verdicts that no
    reviewer could reproduce.

    **Every fix in a batch must carry its own timestamp.** Without one the
    server would stamp each fix with the instant it was processed, which for a
    batch means microseconds apart. The movement rules divide displacement by
    that interval, so a batch of un-timestamped fixes would report a physically
    impossible speed between every pair and classify the whole upload as
    suspicious. Requiring the timestamp makes that failure impossible rather
    than quietly wrong.
    """
    if not payloads:
        raise ValidationError(
            "At least one fix is required.", details={"field": "fixes"}
        )
    if len(payloads) > MAX_BATCH_FIXES:
        raise ValidationError(
            f"At most {MAX_BATCH_FIXES} fixes may be submitted at once.",
            details={"field": "fixes", "received": len(payloads), "maximum": MAX_BATCH_FIXES},
        )
    missing = [
        index
        for index, item in enumerate(payloads)
        if not (item.get("recorded_at") or item.get("timestamp"))
    ]
    if missing:
        raise ValidationError(
            "Every fix in a batch must carry a 'timestamp'. Without it the "
            "server would stamp each fix at processing time, microseconds "
            "apart, and the movement rules would read that as impossible speed.",
            details={
                "field": "timestamp",
                "fixes_missing_timestamp": missing,
            },
        )
    ordered = sorted(
        payloads,
        key=lambda item: str(item.get("recorded_at") or item.get("timestamp") or ""),
    )
    results = [
        ingest_fix(item, actor, source=source, device_id=device_id, commit=False)
        for item in ordered
    ]
    db.session.commit()
    return {
        "accepted": len(results),
        "results": results,
        "alerts_raised": sum(len(item["alerts_raised"]) for item in results),
    }


def simulate_track(
    custody_id: uuid.UUID | str,
    actor: AuthContext,
    *,
    scenario: str,
    seed: int | str | None = None,
    zone_id: uuid.UUID | str | None = None,
    end_at: datetime | None = None,
) -> dict[str, Any]:
    """Generate a simulated track and ingest it as ``SIMULATED`` telemetry.

    Every generated fix goes through the same classification path as a hardware
    fix. Nothing here writes a verdict directly, so a simulated track exercises
    the real engine and its findings are genuinely computed.
    """
    _require_role(actor, TELEMETRY_ROLES, action="generate simulated telemetry")
    if scenario not in TRACK_SCENARIOS:
        raise ValidationError(
            f"Unknown simulator scenario '{scenario}'.",
            details={"field": "scenario", "allowed": sorted(TRACK_SCENARIOS)},
        )
    session = _load_session(custody_id)
    zone = _resolve_zone(session, zone_id)
    if zone is None:
        raise ConflictError(
            "This custody session has no parking zone assigned, so there is no "
            "geofence to simulate movement around.",
            details={"custody_id": str(session.id), "hint": "assign_zone_first"},
        )

    anchor = end_at or _now()
    track = generate_track(
        scenario,
        centre_latitude=_zone_centre(zone)[0],
        centre_longitude=_zone_centre(zone)[1],
        radius_metres=_effective_radius(zone),
        start_time=anchor,
        seed=seed,
    )
    # Re-anchor so the track ends now, which keeps the dashboard's
    # "last updated" reading honest during a live demo.
    if track.fixes:
        offset = anchor - track.fixes[-1].recorded_at
        fixes = tuple(
            replace(fix, recorded_at=fix.recorded_at + offset) for fix in track.fixes
        )
    else:
        fixes = ()
    if len(fixes) > MAX_TRACK_FIXES:
        raise ValidationError(
            f"The generated track has {len(fixes)} fixes, above the limit of "
            f"{MAX_TRACK_FIXES}.",
            details={"scenario": scenario, "maximum": MAX_TRACK_FIXES},
        )

    statuses: dict[str, int] = {}
    alerts_raised: list[dict[str, Any]] = []
    for fix in fixes:
        result = ingest_fix(
            {
                "custody_session_id": str(session.id),
                "latitude": fix.latitude,
                "longitude": fix.longitude,
                "speed_kmph": fix.speed_kmph,
                "heading_degrees": fix.heading_degrees,
                "accuracy_metres": fix.accuracy_metres,
                "recorded_at": fix.recorded_at.isoformat(),
                "zone_id": str(zone.id),
            },
            actor,
            source=TelemetrySource.SIMULATED.value,
            device_id=f"SIMULATOR:{scenario}",
            commit=False,
        )
        statuses[result["movement_status"]] = statuses.get(result["movement_status"], 0) + 1
        alerts_raised.extend(result["alerts_raised"])

    _audit(
        action="GPS_TRACK_SIMULATED",
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="custody_sessions",
        entity_id=session.id,
        description=(
            f"Generated {len(fixes)} simulated GPS fixes for scenario "
            f"'{scenario}'. Provenance recorded as SIMULATED on every row."
        ),
        actor=actor,
        new_values={
            "scenario": scenario,
            "seed": seed,
            "fix_count": len(fixes),
            "source": TelemetrySource.SIMULATED.value,
            "status_counts": statuses,
        },
    )
    db.session.commit()

    return {
        "custody_session_id": str(session.id),
        "zone": zone_dict(zone),
        "source": TelemetrySource.SIMULATED.value,
        "is_simulated": True,
        "track": track.to_dict(),
        "fix_count": len(fixes),
        "status_counts": statuses,
        "alerts_raised": alerts_raised,
        "notes": [
            "Every fix in this track is SIMULATED telemetry generated for "
            "development. It is labelled as such on each row and in the "
            "dashboard, and it must not be presented as hardware data."
        ],
    }


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def fix_dict(event: GpsEvent, *, note: str | None = None) -> dict[str, Any]:
    payload = event.to_dict()
    payload["is_simulated"] = event.is_simulated
    if note is not None:
        payload["note"] = note
    return payload


def zone_dict(zone: ParkingZone) -> dict[str, Any]:
    payload = zone.to_dict()
    centre = _zone_centre(zone)
    payload["centre"] = {"latitude": round(centre[0], 6), "longitude": round(centre[1], 6)}
    payload["effective_radius_metres"] = round(_effective_radius(zone), 1)
    return payload


def alert_dict(alert: SecurityAlert) -> dict[str, Any]:
    payload = alert.to_dict()
    payload["description"] = alert.description
    payload["gps_event_id"] = str(alert.gps_event_id) if alert.gps_event_id else None
    payload["acknowledged_by_id"] = (
        str(alert.acknowledged_by_id) if alert.acknowledged_by_id else None
    )
    payload["resolved_by_id"] = str(alert.resolved_by_id) if alert.resolved_by_id else None
    payload["acknowledged_by_email"] = (
        alert.acknowledged_by.email if alert.acknowledged_by else None
    )
    payload["resolved_by_email"] = alert.resolved_by.email if alert.resolved_by else None
    payload["is_open"] = alert.is_open
    return payload


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def list_fixes(
    custody_id: uuid.UUID | str,
    actor: AuthContext,
    *,
    page: int = 1,
    page_size: int = 100,
    status: str | None = None,
) -> dict[str, Any]:
    """Page through a session's telemetry, newest first."""
    session = _load_session(custody_id)
    _require_tracking_access(actor, session)
    if status is not None:
        status = validate_choice(
            status, [item.value for item in MovementStatus], field="status"
        )

    query = select(GpsEvent).where(GpsEvent.custody_session_id == session.id)
    if status is not None:
        query = query.where(GpsEvent.movement_status == status)

    total = db.session.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar_one()
    rows = (
        db.session.execute(
            query.order_by(GpsEvent.recorded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return {
        "custody_session_id": str(session.id),
        "fixes": [fix_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def tracking_view(custody_id: uuid.UUID | str, actor: AuthContext) -> dict[str, Any]:
    """Everything the live tracking dashboard needs for one custody session."""
    session = _load_session(custody_id)
    _require_tracking_access(actor, session)
    zone = session.zone

    rows = (
        db.session.execute(
            select(GpsEvent)
            .where(GpsEvent.custody_session_id == session.id)
            .order_by(GpsEvent.recorded_at.desc())
            .limit(MAX_TRACK_FIXES)
        )
        .scalars()
        .all()
    )
    ordered = list(reversed(rows))
    latest = rows[0] if rows else None

    status_counts = {item.value: 0 for item in MovementStatus}
    sources: set[str] = set()
    for row in ordered:
        status_counts[row.movement_status] = status_counts.get(row.movement_status, 0) + 1
        sources.add(row.source)

    alerts = (
        db.session.execute(
            select(SecurityAlert)
            .where(SecurityAlert.custody_session_id == session.id)
            .order_by(SecurityAlert.occurred_at.desc())
        )
        .scalars()
        .all()
    )

    has_simulated = TelemetrySource.SIMULATED.value in sources
    has_hardware = TelemetrySource.HARDWARE.value in sources
    return {
        "custody_session_id": str(session.id),
        "custody_status": session.status,
        "retrieval_active": session.status in RETRIEVAL_STATUSES,
        "movement_expected": session.status in MOVEMENT_EXPECTED_STATUSES,
        "vehicle": (
            {
                "id": str(session.vehicle.id),
                "plate_number": session.vehicle.plate_number,
                "make": session.vehicle.make,
                "model": session.vehicle.model,
                "color": session.vehicle.color,
            }
            if session.vehicle
            else None
        ),
        "zone": zone_dict(zone) if zone else None,
        "latest_fix": fix_dict(latest) if latest else None,
        "last_updated_at": latest.recorded_at.isoformat() if latest else None,
        "movement_status": latest.movement_status if latest else None,
        "fix_count": len(ordered),
        "status_counts": status_counts,
        "sources": sorted(sources),
        # Provenance is surfaced three ways so the UI cannot miss it: a flag for
        # the banner, the raw list for a legend, and per-fix on every reading.
        "has_simulated_telemetry": has_simulated,
        "has_hardware_telemetry": has_hardware,
        "telemetry_note": (
            "This track contains SIMULATED telemetry generated for development "
            "and must not be presented as hardware data."
            if has_simulated and not has_hardware
            else (
                "This track mixes SIMULATED and HARDWARE telemetry; each fix is "
                "labelled individually."
                if has_simulated
                else None
            )
        ),
        "track": [fix_dict(row) for row in ordered],
        "alerts": [alert_dict(alert) for alert in alerts],
        "open_alert_count": sum(1 for alert in alerts if alert.is_open),
    }


# ---------------------------------------------------------------------------
# Geofences
# ---------------------------------------------------------------------------
def _validated_polygon(value: Any, *, field: str = "geofence_polygon") -> list[list[float]]:
    """Validate a ``[longitude, latitude]`` ring.

    GeoJSON ordering is required because that is what ``L.polygon`` expects and
    what the schema stores. Accepting ``[lat, lon]`` silently would produce a
    geofence somewhere else on the planet, which is a failure mode worth
    refusing loudly.
    """
    if not isinstance(value, list) or len(value) < 3:
        raise ValidationError(
            f"'{field}' must be a list of at least three [longitude, latitude] pairs.",
            details={"field": field},
        )
    ring: list[list[float]] = []
    for index, vertex in enumerate(value):
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            raise ValidationError(
                f"'{field}[{index}]' must be a [longitude, latitude] pair.",
                details={"field": field, "index": index},
            )
        longitude = validate_longitude(vertex[0], field=f"{field}[{index}][0]")
        latitude = validate_latitude(vertex[1], field=f"{field}[{index}][1]")
        ring.append([longitude, latitude])
    return ring


def _validated_radius(value: Any, *, field: str = "radius") -> float:
    try:
        radius = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"'{field}' must be a number.", details={"field": field}
        ) from exc
    if radius <= 0:
        raise ValidationError(
            f"'{field}' must be greater than zero: a zero-radius geofence would "
            "classify every fix as a breach.",
            details={"field": field, "received": radius},
        )
    if radius > MAX_ZONE_RADIUS_METRES:
        raise ValidationError(
            f"'{field}' must be at most {MAX_ZONE_RADIUS_METRES:.0f} m.",
            details={"field": field, "received": radius, "maximum": MAX_ZONE_RADIUS_METRES},
        )
    return radius


def create_geofence(payload: dict[str, Any], actor: AuthContext) -> dict[str, Any]:
    """Create a parking zone and its geofence.

    Accepts the brief's ``centre + radius`` description and generates a circular
    ring into ``geofence_polygon``, so the API matches the specification while
    the stored shape stays a polygon and one point-in-polygon test serves both
    this and an irregular real-world yard. An explicit ``geofence_polygon``
    overrides the generated circle.
    """
    _require_role(actor, GEOFENCE_ROLES, action="create a geofence")

    code = validate_text(payload.get("code"), field="code", max_length=32).upper()
    name = validate_text(payload.get("name"), field="name", max_length=80)
    latitude = validate_latitude(payload.get("latitude"))
    longitude = validate_longitude(payload.get("longitude"))
    radius = _validated_radius(payload.get("radius"))

    existing = db.session.execute(
        select(ParkingZone).where(func.upper(ParkingZone.code) == code)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "A parking zone with that code already exists.",
            details={"field": "code", "code": code},
        )

    ring = (
        _validated_polygon(payload["geofence_polygon"])
        if payload.get("geofence_polygon")
        else engine.circular_ring(latitude, longitude, radius)
    )
    buffer_metres = _optional_float(
        payload.get("buffer_metres"), field="buffer_metres", minimum=0.0
    )
    capacity = payload.get("capacity")
    try:
        capacity_value = int(capacity) if capacity not in (None, "") else 1
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "'capacity' must be a whole number.", details={"field": "capacity"}
        ) from exc
    if capacity_value < 1:
        raise ValidationError(
            "'capacity' must be at least 1.",
            details={"field": "capacity", "received": capacity_value},
        )

    zone = ParkingZone(
        code=code,
        name=name,
        description=(
            validate_text(str(payload["description"]), field="description", max_length=300)
            if payload.get("description")
            else None
        ),
        geofence_polygon=ring,
        centre_latitude=latitude,
        centre_longitude=longitude,
        radius_metres=radius,
        buffer_metres=buffer_metres if buffer_metres is not None else 25.0,
        capacity=capacity_value,
        current_occupancy=0,
        status=ZoneStatus.ACTIVE.value,
    )
    db.session.add(zone)
    db.session.flush()

    _audit(
        action="GEOFENCE_CONFIGURED",
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="parking_zones",
        entity_id=zone.id,
        description=(
            f"Created geofence '{zone.code}' at ({latitude}, {longitude}) with a "
            f"{radius:.0f} m radius and a {zone.buffer_metres} m tolerance."
        ),
        actor=actor,
        new_values={
            "code": zone.code,
            "latitude": latitude,
            "longitude": longitude,
            "radius_metres": radius,
            "buffer_metres": float(zone.buffer_metres),
            "polygon_vertices": len(ring),
            "polygon_supplied": bool(payload.get("geofence_polygon")),
        },
    )
    db.session.commit()
    return zone_dict(zone)


def update_geofence(
    zone_id: uuid.UUID | str, payload: dict[str, Any], actor: AuthContext
) -> dict[str, Any]:
    """Reconfigure an existing geofence.

    Audited as ``GEOFENCE_CONFIGURED`` rather than a generic ``UPDATE``: moving
    a boundary retroactively changes what every future fix means, which is a
    security-relevant act rather than housekeeping.
    """
    _require_role(actor, GEOFENCE_ROLES, action="modify a geofence")
    zone = db.session.get(ParkingZone, validate_uuid(zone_id, field="zone_id"))
    if zone is None:
        raise NotFoundError(
            "No parking zone was found with that id.", details={"zone_id": str(zone_id)}
        )

    changed: dict[str, Any] = {}
    if payload.get("name"):
        zone.name = validate_text(payload["name"], field="name", max_length=80)
        changed["name"] = zone.name
    if payload.get("description") is not None:
        zone.description = validate_text(
            str(payload["description"]), field="description", max_length=300
        )
        changed["description"] = zone.description
    if payload.get("buffer_metres") is not None:
        zone.buffer_metres = _optional_float(
            payload["buffer_metres"], field="buffer_metres", minimum=0.0
        )
        changed["buffer_metres"] = float(zone.buffer_metres)
    if payload.get("status"):
        zone.status = validate_status(
            str(payload["status"]).upper(), ZoneStatus, field="status"
        )
        changed["status"] = zone.status

    geometry_changed = False
    if payload.get("latitude") is not None or payload.get("longitude") is not None:
        latitude = validate_latitude(
            payload.get("latitude", zone.centre_latitude), field="latitude"
        )
        longitude = validate_longitude(
            payload.get("longitude", zone.centre_longitude), field="longitude"
        )
        radius = (
            _validated_radius(payload["radius"])
            if payload.get("radius") is not None
            else (float(zone.radius_metres) if zone.radius_metres else None)
        )
        if radius is None:
            raise ValidationError(
                "'radius' is required when moving a zone that has no stored radius.",
                details={"field": "radius"},
            )
        zone.centre_latitude = latitude
        zone.centre_longitude = longitude
        zone.radius_metres = radius
        zone.geofence_polygon = engine.circular_ring(latitude, longitude, radius)
        geometry_changed = True
        changed.update(
            {"latitude": latitude, "longitude": longitude, "radius_metres": radius}
        )
    elif payload.get("radius") is not None:
        radius = _validated_radius(payload["radius"])
        centre = _zone_centre(zone)
        zone.radius_metres = radius
        zone.centre_latitude = centre[0]
        zone.centre_longitude = centre[1]
        zone.geofence_polygon = engine.circular_ring(centre[0], centre[1], radius)
        geometry_changed = True
        changed["radius_metres"] = radius
    elif payload.get("geofence_polygon"):
        zone.geofence_polygon = _validated_polygon(payload["geofence_polygon"])
        geometry_changed = True
        changed["polygon_vertices"] = len(zone.geofence_polygon)

    if geometry_changed:
        _audit(
            action="GEOFENCE_CONFIGURED",
            outcome=AuditOutcome.SUCCESS.value,
            entity_type="parking_zones",
            entity_id=zone.id,
            description=(
                f"Reconfigured geofence '{zone.code}'. The boundary change applies "
                f"to fixes recorded from now on; existing verdicts are unchanged."
            ),
            actor=actor,
            new_values=changed,
        )
    elif changed:
        _audit(
            action="UPDATE",
            outcome=AuditOutcome.SUCCESS.value,
            entity_type="parking_zones",
            entity_id=zone.id,
            description=f"Updated parking zone '{zone.code}'.",
            actor=actor,
            new_values=changed,
        )
    db.session.commit()
    return zone_dict(zone)


def list_geofences(
    actor: AuthContext | None = None, *, status: str | None = None
) -> dict[str, Any]:
    """List parking zones with the geometry the map needs."""
    query = select(ParkingZone)
    if status:
        status = validate_choice(status, [item.value for item in ZoneStatus], field="status")
        query = query.where(ParkingZone.status == status)
    zones = db.session.execute(query.order_by(ParkingZone.code)).scalars().all()
    return {
        "zones": [zone_dict(zone) for zone in zones],
        "total": len(zones),
        "viewer_role": actor.role if actor else None,
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def list_alerts(
    actor: AuthContext,
    *,
    custody_session_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """List security alerts. Customers see only their own vehicles' alerts."""
    query = select(SecurityAlert)
    if actor.role not in TELEMETRY_ROLES:
        query = query.join(CustodySession).where(
            CustodySession.ticket.has(customer_id=uuid.UUID(actor.user_id))
        )
    if custody_session_id:
        query = query.where(
            SecurityAlert.custody_session_id == validate_uuid(
                custody_session_id, field="custody_session_id"
            )
        )
    if status:
        status = validate_choice(status, [item.value for item in AlertStatus], field="status")
        query = query.where(SecurityAlert.status == status)
    if severity:
        severity = validate_choice(
            severity, [item.value for item in AlertSeverity], field="severity"
        )
        query = query.where(SecurityAlert.severity == severity)

    total = db.session.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar_one()
    rows = (
        db.session.execute(
            query.order_by(SecurityAlert.occurred_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return {
        "alerts": [alert_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _load_alert(alert_id: uuid.UUID | str) -> SecurityAlert:
    alert = db.session.get(SecurityAlert, validate_uuid(alert_id, field="alert_id"))
    if alert is None:
        raise NotFoundError(
            "No security alert was found with that id.", details={"alert_id": str(alert_id)}
        )
    return alert


def _require_acknowledgeable(alert: SecurityAlert, now: datetime) -> None:
    """Explain, rather than let the database explain, an unactionable alert.

    ``ck_security_alerts_ack_after_occurrence`` forbids acknowledging an alert
    before it occurred, which is right. But when a fix arrived with a skewed
    clock the alert's ``occurred_at`` can sit in the future, and without this
    guard the manager gets an opaque constraint violation in the middle of an
    incident instead of being told which clock to fix.
    """
    if alert.occurred_at and alert.occurred_at > now:
        raise ConflictError(
            "This alert's occurrence timestamp is in the future, so it cannot be "
            "acknowledged yet. The GPS fix that raised it was recorded with a "
            "clock ahead of the server.",
            details={
                "alert_id": str(alert.id),
                "occurred_at": alert.occurred_at.isoformat(),
                "server_time": now.isoformat(),
                "hint": "correct_the_reporting_device_clock",
            },
        )


def acknowledge_alert(alert_id: uuid.UUID | str, actor: AuthContext) -> dict[str, Any]:
    """Record that a manager has seen an alert. Does not close it."""
    _require_role(actor, ALERT_TRIAGE_ROLES, action="acknowledge a security alert")
    alert = _load_alert(alert_id)
    now = _now()
    _require_acknowledgeable(alert, now)
    if alert.status in (AlertStatus.RESOLVED.value, AlertStatus.DISMISSED.value):
        raise ConflictError(
            f"This alert is already {alert.status.lower()} and cannot be acknowledged.",
            details={"alert_id": str(alert.id), "status": alert.status},
        )
    if alert.acknowledged_at is None:
        alert.acknowledged_by_id = uuid.UUID(actor.user_id)
        alert.acknowledged_at = now
    if alert.status == AlertStatus.OPEN.value:
        alert.status = AlertStatus.ACKNOWLEDGED.value
    _audit(
        action="ALERT_ACKNOWLEDGED",
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="security_alerts",
        entity_id=alert.id,
        description=f"Acknowledged {alert.alert_type} ({alert.severity}).",
        actor=actor,
    )
    db.session.commit()
    return alert_dict(alert)


def resolve_alert(
    alert_id: uuid.UUID | str,
    actor: AuthContext,
    *,
    dismiss: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Close an alert as resolved, or dismiss it as a false positive.

    Dismissal is kept distinct from resolution on purpose. "We checked and the
    vehicle was fine" and "this alert should not have fired" are different
    claims, and the second one is feedback about the detector that a reviewer
    needs to be able to count.
    """
    _require_role(actor, ALERT_TRIAGE_ROLES, action="resolve a security alert")
    alert = _load_alert(alert_id)
    now = _now()
    _require_acknowledgeable(alert, now)
    if alert.status in (AlertStatus.RESOLVED.value, AlertStatus.DISMISSED.value):
        raise ConflictError(
            f"This alert is already {alert.status.lower()}.",
            details={"alert_id": str(alert.id), "status": alert.status},
        )
    if alert.acknowledged_at is None:
        # Resolving without acknowledging is legitimate - a manager may act
        # immediately - but the schema wants acknowledgement recorded, and it
        # is genuinely useful to know the alert was seen.
        alert.acknowledged_by_id = uuid.UUID(actor.user_id)
        alert.acknowledged_at = now
    alert.status = AlertStatus.DISMISSED.value if dismiss else AlertStatus.RESOLVED.value
    alert.resolved_by_id = uuid.UUID(actor.user_id)
    alert.resolved_at = now
    alert.resolution_notes = (
        validate_text(notes, field="notes", max_length=1000) if notes else None
    )
    _audit(
        action="ALERT_RESOLVED",
        outcome=AuditOutcome.SUCCESS.value,
        entity_type="security_alerts",
        entity_id=alert.id,
        description=(
            f"{'Dismissed as a false positive' if dismiss else 'Resolved'}: "
            f"{alert.alert_type} ({alert.severity})."
        ),
        actor=actor,
        new_values={
            "status": alert.status,
            "resolution_notes": alert.resolution_notes,
        },
    )
    db.session.commit()
    return alert_dict(alert)


def simulator_scenarios() -> list[dict[str, str]]:
    """The tracks the simulator can generate, for the demo picker."""
    return scenario_catalogue()


__all__ = [
    "ALERT_COOLDOWN",
    "ALERT_TRIAGE_ROLES",
    "GEOFENCE_ROLES",
    "MAX_BATCH_FIXES",
    "MOVEMENT_EXPECTED_STATUSES",
    "RETRIEVAL_STATUSES",
    "TELEMETRY_ROLES",
    "acknowledge_alert",
    "alert_dict",
    "create_geofence",
    "fix_dict",
    "ingest_batch",
    "ingest_fix",
    "list_alerts",
    "list_fixes",
    "list_geofences",
    "resolve_alert",
    "simulate_track",
    "simulator_scenarios",
    "tracking_view",
    "update_geofence",
    "zone_dict",
]
