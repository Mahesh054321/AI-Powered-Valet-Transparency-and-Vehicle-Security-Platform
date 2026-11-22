"""Retrieval-time prediction service (Phase 9b).

Answers one question: **after a customer asks for their car, how long until it
is at the handover point, and when should a valet actually set off?**

Two halves, deliberately separated:

* The **live half** reads the real database -- how many retrievals are already
  queued, how many valets are on shift, which zone the car is in and how full it
  is, whether the vehicle is flagged. These are counted, not assumed.
* The **model half** turns those counts into minutes. It is a ridge regression
  trained offline on synthetic history by
  ``utils/scripts/train_retrieval_model.py``.

Why the split matters: the model is a prototype fitted to a generator. The queue
lengths, staffing and occupancy it is fed here are genuine measurements from this
database. Keeping the two apart means the honest part of the answer is visibly
honest, and the estimated part is labelled as an estimate.

**What the recommendation to dispatch is, and is not.** The model does not output
a dispatch time directly. This service derives one by asking the same model a
counterfactual: what would the wait be if this were the *only* request in the
queue? The difference is the part of the wait caused by waiting one's turn, and
that is how long dispatch can be deferred without the customer waiting longer.
It is a decomposition of one linear prediction, not a second opinion.

**Honesty.** Every estimate carries ``trained_on_synthetic_data: true`` and the
prototype disclaimer, and the stored row records the exact inputs it was
computed from, so a later reviewer can see what the model was told. See
``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import case, func, select

from ai.retrieval.dataset import zone_profile_for
from ai.retrieval.features import (
    RETRIEVAL_FEATURE_COLUMNS,
    RetrievalContext,
    extract_features,
)
from ai.retrieval.model import (
    METHOD_NAME,
    MODEL_VERSION,
    PROTOTYPE_DISCLAIMER,
    RetrievalModel,
    RetrievalPrediction,
    round_minutes,
)
from database import db
from models.audit_log import AuditAction, AuditOutcome
from models.custody_session import CustodySession
from models.enums import CustodyStatus, PredictionStatus, VehicleStatus
from models.parking_zone import ParkingZone
from models.retrieval_prediction import RetrievalPrediction as RetrievalPredictionRow
from models.user import User
from models.valet_ticket import ValetTicket
from models.vehicle import Vehicle
from services import tickets as ticket_service
from services.audit import record_event
from utils.exceptions import NotFoundError, ServiceUnavailableError, ValidationError
from utils.security import AuthContext, Role

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path("storage/models/retrieval_model.json")

#: Used only when a zone has no recorded retrieval history yet, so a first-ever
#: request still gets an estimate instead of an error. The real historical mean
#: is preferred whenever rows exist.
FALLBACK_ZONE_MEAN_MINUTES = 8.0

#: How wide the published interval is, as a fraction of the point estimate. A
#: single number implies precision this model does not have; a range states the
#: uncertainty instead of hiding it.
INTERVAL_FRACTION = 0.30

#: Statuses that mean a retrieval is occupying a valet or waiting for one.
QUEUE_STATUSES = (
    CustodyStatus.RETRIEVAL_REQUESTED.value,
    CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QueueSnapshot:
    """The live counts the model is fed. All measured, none assumed."""

    queue_size: int
    queue_position: int
    active_valets: int
    zone_capacity: int
    zone_occupancy: int
    zone_code: str
    vehicle_flagged: bool
    historical_zone_mean_minutes: float
    #: True when no completed retrieval exists for this zone yet, so the mean is
    #: a stand-in rather than a measurement. Surfaced in the response because it
    #: changes what the estimate is worth.
    history_is_fallback: bool
    historical_sample_size: int


class RetrievalModelService:
    """Loads and caches the retrieval artifact.

    Same shape as ``SecurityModelService``: the load happens once, is guarded by
    a lock, and a failure is cached as "unavailable" rather than retried on every
    request. A missing artifact is a supported deployment state, not an error --
    the endpoint then says plainly what to run to produce one.
    """

    def __init__(self) -> None:
        self._model: RetrievalModel | None = None
        self._attempted = False
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _config() -> dict[str, Any]:
        try:
            config = current_app.config
        except RuntimeError:  # pragma: no cover - outside an app context
            config = {}
        return {
            "enabled": bool(config.get("RETRIEVAL_ML_ENABLED", True)),
            "path": Path(config.get("RETRIEVAL_MODEL_PATH", DEFAULT_MODEL_PATH)),
        }

    @property
    def model(self) -> RetrievalModel | None:
        with self._lock:
            if self._attempted:
                return self._model
            self._attempted = True
            config = self._config()
            if not config["enabled"]:
                self._load_error = "Retrieval prediction disabled by configuration."
                return None
            path = config["path"]
            if not path.exists():
                self._load_error = (
                    f"No retrieval model artifact at {path}. Run "
                    "'python utils/scripts/train_retrieval_model.py' to train one."
                )
                logger.info("Retrieval model unavailable: %s", self._load_error)
                return None
            try:
                self._model = RetrievalModel.load(path)
                logger.info("Retrieval model loaded: %s", MODEL_VERSION)
            except Exception as exc:  # noqa: BLE001 - any load failure must degrade
                # Deliberately broad. A corrupt artifact or a scikit-learn
                # version mismatch must not turn the customer-facing pickup
                # screen into a 500; the endpoint explains itself instead.
                self._load_error = f"Retrieval model could not be loaded: {exc}"
                logger.warning("Retrieval model load failed: %s", self._load_error)
                self._model = None
            return self._model

    @property
    def available(self) -> bool:
        return self.model is not None

    @property
    def load_error(self) -> str | None:
        _ = self.model  # the load is lazy; reading the error forces it
        return self._load_error

    def reset(self) -> None:
        """Forget the cached model. Used by tests after writing a new artifact."""
        with self._lock:
            self._model = None
            self._attempted = False
            self._load_error = None

    def describe(self) -> dict[str, Any]:
        """Model metadata, including the MAE it was held out on."""
        config = self._config()
        model = self.model
        if model is None:
            return {
                "available": False,
                "enabled": config["enabled"],
                "reason": self._load_error,
                "model_version": MODEL_VERSION,
                "trained_on_synthetic_data": True,
                "disclaimer": PROTOTYPE_DISCLAIMER,
            }
        described = model.describe()
        described["available"] = True
        described["enabled"] = config["enabled"]
        described["primary_metric"] = "mae_minutes"
        described["trained_on_synthetic_data"] = True
        described["disclaimer"] = PROTOTYPE_DISCLAIMER
        return described


retrieval_model = RetrievalModelService()


# ---------------------------------------------------------------------------
# Reading the live situation
# ---------------------------------------------------------------------------
def _active_valet_count() -> int:
    """How many valet operators are currently usable.

    Counted from active accounts with the operator role. This is a proxy for
    staff on shift -- the platform has no shift roster -- and the response says
    so rather than implying a timesheet was consulted.
    """
    statement = select(func.count(User.id)).where(
        User.role == Role.VALET_OPERATOR.value,
        User.is_active.is_(True),
    )
    return int(db.session.execute(statement).scalar_one())


def _queue_ahead(custody: CustodySession, requested_at: datetime) -> tuple[int, int]:
    """How many retrievals are in the queue, and where this one sits in it.

    Position is by request time, so the queue is first-come-first-served in the
    order the platform actually recorded the requests. A retrieval that is
    already in progress still counts -- a valet working on someone else's car is
    exactly why this customer has to wait.
    """
    in_queue = select(CustodySession).where(
        CustodySession.status.in_(QUEUE_STATUSES),
        CustodySession.retrieval_requested_at.is_not(None),
    )
    sessions = db.session.execute(in_queue).scalars().all()

    # This session may not itself be in a queue status yet (the caller can ask
    # for an estimate before the status transition), so it is counted in
    # explicitly rather than relied upon to be present.
    others = [
        session
        for session in sessions
        if session.id != custody.id
        and session.retrieval_requested_at is not None
        and session.retrieval_requested_at < requested_at
    ]
    ahead = len(others)
    in_flight = len([session for session in sessions if session.id != custody.id])
    return in_flight + 1, ahead + 1


def _historical_zone_mean(zone_id: str | None, zone_code: str) -> tuple[float, int]:
    """Mean actual retrieval time for this zone, from completed predictions.

    Only rows with a recorded ``actual_ready_at`` count. A prediction that was
    never fulfilled says nothing about how long the yard takes, and including
    the *predicted* value would make the model's own output an input to itself --
    a feedback loop that would look accurate while measuring nothing.
    """
    if not zone_id:
        return _fallback_zone_mean(zone_code), 0

    statement = (
        select(
            func.avg(
                func.extract(
                    "epoch",
                    RetrievalPredictionRow.actual_ready_at
                    - RetrievalPredictionRow.requested_at,
                )
            ),
            func.count(RetrievalPredictionRow.id),
        )
        .join(
            CustodySession,
            CustodySession.id == RetrievalPredictionRow.custody_session_id,
        )
        .where(
            CustodySession.zone_id == zone_id,
            RetrievalPredictionRow.actual_ready_at.is_not(None),
        )
    )
    average_seconds, count = db.session.execute(statement).one()
    if average_seconds is None or not count:
        return _fallback_zone_mean(zone_code), 0
    return max(1.0, float(average_seconds) / 60.0), int(count)


def _fallback_zone_mean(zone_code: str) -> float:
    """A defensible stand-in when a zone has no completed retrieval yet."""
    profile = zone_profile_for(zone_code)
    # Derived from the generator's own walk/drive/service constants so the
    # fallback is at least in the right range for a zone of this size, rather
    # than a single global guess applied to every zone.
    walk_scale = float(profile.get("walk_scale", 1.0))
    return max(1.0, round(4.2 * walk_scale + 4.2, 2))


def read_snapshot(
    custody: CustodySession,
    *,
    requested_at: datetime,
    vehicle: Vehicle | None = None,
) -> QueueSnapshot:
    """Measure everything the model needs for one custody session."""
    zone = (
        db.session.get(ParkingZone, custody.zone_id) if custody.zone_id else None
    )
    zone_code = zone.code if zone else "UNASSIGNED"

    if vehicle is None and custody.vehicle_id:
        vehicle = db.session.get(Vehicle, custody.vehicle_id)

    queue_size, queue_position = _queue_ahead(custody, requested_at)
    historical_mean, sample_size = _historical_zone_mean(custody.zone_id, zone_code)

    return QueueSnapshot(
        queue_size=max(1, queue_size),
        queue_position=max(1, queue_position),
        active_valets=_active_valet_count(),
        zone_capacity=int(zone.capacity) if zone else 20,
        zone_occupancy=int(zone.current_occupancy) if zone else 0,
        zone_code=zone_code,
        vehicle_flagged=bool(vehicle and vehicle.status == VehicleStatus.FLAGGED.value),
        historical_zone_mean_minutes=historical_mean,
        history_is_fallback=sample_size == 0,
        historical_sample_size=sample_size,
    )


# ---------------------------------------------------------------------------
# Turning a snapshot into an estimate
# ---------------------------------------------------------------------------
def _context_for(snapshot: QueueSnapshot, requested_at: datetime) -> RetrievalContext:
    return RetrievalContext(
        queue_size=snapshot.queue_size,
        queue_position=snapshot.queue_position,
        active_valets=snapshot.active_valets,
        zone_capacity=snapshot.zone_capacity,
        zone_occupancy=snapshot.zone_occupancy,
        requested_at=requested_at,
        historical_zone_mean_minutes=snapshot.historical_zone_mean_minutes,
        vehicle_flagged=snapshot.vehicle_flagged,
    )


def _alone_context(snapshot: QueueSnapshot, requested_at: datetime) -> RetrievalContext:
    """The same situation, but as if this were the only request waiting.

    Used to split the estimate into "waiting your turn" and "actually fetching
    the car", which is what makes a dispatch recommendation possible.
    """
    return RetrievalContext(
        queue_size=1,
        queue_position=1,
        active_valets=snapshot.active_valets,
        zone_capacity=snapshot.zone_capacity,
        zone_occupancy=snapshot.zone_occupancy,
        requested_at=requested_at,
        historical_zone_mean_minutes=snapshot.historical_zone_mean_minutes,
        vehicle_flagged=snapshot.vehicle_flagged,
    )


def estimate(snapshot: QueueSnapshot, requested_at: datetime) -> dict[str, Any]:
    """Predict one retrieval from a measured snapshot.

    Returns a plain dict rather than a model object so the route layer can shape
    the response without importing anything from ``ai.*``.
    """
    model = retrieval_model.model
    if model is None:
        raise ServiceUnavailableError(
            "No retrieval model is loaded, so an estimate cannot be produced. "
            + (retrieval_model.load_error or ""),
            details={
                "reason": retrieval_model.load_error,
                "hint": "python utils/scripts/train_retrieval_model.py",
            },
        )

    context = _context_for(snapshot, requested_at)
    features = extract_features(context)
    prediction: RetrievalPrediction = model.predict(features)

    # The counterfactual: same situation, empty queue. The gap between the two
    # predictions is the part of the wait caused by other people's cars.
    alone = model.predict_minutes(extract_features(_alone_context(snapshot, requested_at)))
    queue_wait_minutes = max(0.0, prediction.estimated_minutes - alone)
    fetch_minutes = max(1.0, alone)

    estimated_minutes = round_minutes(prediction.estimated_minutes)
    spread = max(1, int(round(estimated_minutes * INTERVAL_FRACTION)))
    min_minutes = max(1, estimated_minutes - spread)
    max_minutes = estimated_minutes + spread

    predicted_ready_at = requested_at + timedelta(minutes=estimated_minutes)
    dispatch_after = requested_at + timedelta(minutes=round_minutes(queue_wait_minutes))

    return {
        "estimated_minutes": estimated_minutes,
        "estimated_minutes_raw": round(prediction.estimated_minutes, 2),
        "min_wait_minutes": min_minutes,
        "max_wait_minutes": max_minutes,
        "estimated_ready_at": predicted_ready_at,
        "queue_position": snapshot.queue_position,
        "queue_size": snapshot.queue_size,
        "dispatch_after": dispatch_after,
        "dispatch_delay_minutes": round_minutes(queue_wait_minutes),
        "fetch_minutes": round_minutes(fetch_minutes),
        "confidence": prediction.confidence,
        "reason": prediction.reason,
        "contributions": prediction.contributions,
        "model_version": prediction.model_version,
        "method": prediction.method,
        "inputs": features,
        "trained_on_synthetic_data": True,
        "disclaimer": prediction.disclaimer,
    }


def _dispatch_wording(delay_minutes: int) -> str:
    """Human phrasing for the recommendation, shown verbatim in the UI."""
    if delay_minutes <= 0:
        return "Now"
    if delay_minutes == 1:
        return "In 1 minute"
    return f"In {delay_minutes} minutes"


def resolve_custody_session(identifier: str | uuid.UUID | None) -> CustodySession | None:
    """Resolve a custody session from a custody ID, ticket ID, or ticket code."""
    if not identifier:
        return None
    raw_str = str(identifier).strip()

    # 1. Try direct CustodySession ID
    try:
        session_uuid = uuid.UUID(raw_str)
        custody = db.session.get(CustodySession, session_uuid)
        if custody is not None:
            return custody
    except (ValueError, TypeError):
        pass

    # 2. Try ValetTicket UUID or ticket_code
    ticket = None
    try:
        ticket = db.session.get(ValetTicket, uuid.UUID(raw_str))
    except (ValueError, TypeError):
        pass

    if ticket is None:
        ticket = db.session.execute(
            select(ValetTicket).where(ValetTicket.ticket_code == raw_str)
        ).scalars().first()

    if ticket is not None:
        custody = db.session.execute(
            select(CustodySession)
            .where(CustodySession.ticket_id == ticket.id)
            .order_by(CustodySession.dropped_off_at.desc())
            .limit(1)
        ).scalars().first()
        if custody is not None:
            return custody

    return None


def predict_for_custody(
    custody_session_id: str,
    *,
    actor_id: str | None = None,
    actor_role: str | None = None,
    actor_email: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Produce (and normally store) a retrieval estimate for one custody session.

    Any earlier pending estimate for the same session is marked SUPERSEDED
    rather than deleted: the customer may have asked twice, and the first answer
    is part of the record of what they were told.
    """
    custody = resolve_custody_session(custody_session_id)
    if custody is None:
        raise NotFoundError(
            "No custody session with that id.",
            details={"custody_session_id": custody_session_id},
        )

    requested_at = custody.retrieval_requested_at or _utcnow()
    snapshot = read_snapshot(custody, requested_at=requested_at)
    result = estimate(snapshot, requested_at)

    if not persist:
        result.update(
            {
                "custody_session_id": custody.id,
                "requested_at": requested_at,
                "dispatch_label": _dispatch_wording(result["dispatch_delay_minutes"]),
                "persisted": False,
                "history_is_fallback": snapshot.history_is_fallback,
                "historical_sample_size": snapshot.historical_sample_size,
                "zone_code": snapshot.zone_code,
            }
        )
        return result

    # Supersede what this session was told before, keeping the rows.
    prior = db.session.execute(
        select(RetrievalPredictionRow).where(
            RetrievalPredictionRow.custody_session_id == custody.id,
            RetrievalPredictionRow.status == PredictionStatus.PENDING.value,
        )
    ).scalars().all()
    for row in prior:
        row.status = PredictionStatus.SUPERSEDED.value

    row = RetrievalPredictionRow(
        custody_session_id=custody.id,
        requested_by_id=actor_id,
        status=PredictionStatus.PENDING.value,
        requested_at=requested_at,
        predicted_ready_at=result["estimated_ready_at"],
        dispatch_after=result["dispatch_after"],
        predicted_wait_minutes=result["estimated_minutes"],
        min_wait_minutes=result["min_wait_minutes"],
        max_wait_minutes=result["max_wait_minutes"],
        confidence=result["confidence"],
        queue_position=result["queue_position"],
        method=result["method"],
        model_version=result["model_version"],
        inputs_snapshot={
            "features": result["inputs"],
            "queue_size": snapshot.queue_size,
            "queue_position": snapshot.queue_position,
            "active_valets": snapshot.active_valets,
            "zone_code": snapshot.zone_code,
            "zone_capacity": snapshot.zone_capacity,
            "zone_occupancy": snapshot.zone_occupancy,
            "vehicle_flagged": snapshot.vehicle_flagged,
            "historical_zone_mean_minutes": snapshot.historical_zone_mean_minutes,
            "historical_sample_size": snapshot.historical_sample_size,
            "history_is_fallback": snapshot.history_is_fallback,
            "dispatch_delay_minutes": result["dispatch_delay_minutes"],
            "fetch_minutes": result["fetch_minutes"],
            "trained_on_synthetic_data": True,
        },
        explanation=result["reason"][:1000],
    )
    db.session.add(row)
    # flush first so ``row.id`` exists for the audit entry's entity_id, then
    # commit: every other service in this codebase commits, and no route does.
    # Flushing alone returned 201 with a prediction_id while the row was
    # discarded at the end of the request, so the customer was handed a
    # reference to something that did not exist.
    db.session.flush()
    # The estimate, the queue position and the method are recorded together
    # because they are what make the entry useful later: an audit line saying
    # only "a prediction was generated" cannot be compared against what
    # actually happened.
    record_event(
        action=AuditAction.RETRIEVAL_PREDICTED,
        entity_type="retrieval_predictions",
        entity_id=row.id,
        custody_session_id=custody.id,
        # This function is reached with the actor threaded down as separate
        # values rather than as a User row or AuthContext.
        actor_id=actor_id,
        actor_role=actor_role,
        actor_email=actor_email,
        outcome=AuditOutcome.SUCCESS,
        description=(
            f"Retrieval estimate {result['estimated_minutes']} min "
            f"(queue position {result['queue_position']}, "
            f"{result['method']}, synthetic-trained model)"
        ),
        metadata={
            "estimated_minutes": result["estimated_minutes"],
            "queue_position": result["queue_position"],
            "method": result["method"],
            "confidence": result.get("confidence"),
        },
    )
    db.session.commit()

    result.update(
        {
            "prediction_id": str(row.id),
            "custody_session_id": custody.id,
            "requested_at": requested_at,
            "dispatch_label": _dispatch_wording(result["dispatch_delay_minutes"]),
            "persisted": True,
            "history_is_fallback": snapshot.history_is_fallback,
            "historical_sample_size": snapshot.historical_sample_size,
            "zone_code": snapshot.zone_code,
        }
    )
    return result


def prediction_recency_order(model: type[RetrievalPredictionRow]) -> tuple:
    """Sort predictions so the one that currently governs comes first.

    Three ordering keys, and the reason each is needed:

    1. **PENDING before anything else.** A superseded, dispatched or fulfilled
       row is history. The estimate a customer is waiting on is the PENDING one,
       so it wins outright rather than competing on a timestamp.
    2. **``created_at`` descending, not ``requested_at``.** ``requested_at`` is
       a *modelling input*: it is when the customer asked inside the timeline
       being modelled, and the demo seed deliberately writes future-dated rows.
       Ordering "latest" by it is ordering by fiction, and two rows can share a
       value exactly — which made ``limit(1)`` return whichever row PostgreSQL
       happened to reach first, so the valet queue showed a stale 14-minute
       estimate while the customer's own card showed the fresh 9-minute one.
       ``created_at`` is when the row was written, so it reflects real order.
    3. **``id`` descending** as a deterministic tiebreaker, so a tie can never
       again produce a different answer on two reads.
    """
    return (
        case((model.status == PredictionStatus.PENDING.value, 0), else_=1),
        model.created_at.desc(),
        model.id.desc(),
    )


def latest_prediction(custody_session_id: str) -> dict[str, Any]:
    """The most recent stored estimate for a custody session, with its inputs."""
    custody = resolve_custody_session(custody_session_id)
    if custody is None:
        raise NotFoundError(
            "No custody session with that id.",
            details={"custody_session_id": custody_session_id},
        )

    row = (
        db.session.execute(
            select(RetrievalPredictionRow)
            .where(RetrievalPredictionRow.custody_session_id == custody.id)
            .order_by(*prediction_recency_order(RetrievalPredictionRow))
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise NotFoundError(
            "No retrieval prediction has been generated for this custody session "
            "yet. Request one with POST /api/retrieval/predict.",
            details={"custody_session_id": str(custody.id)},
        )

    snapshot = row.inputs_snapshot or {}
    delay = int(snapshot.get("dispatch_delay_minutes", 0) or 0)
    dispatch_label = _dispatch_wording(delay)
    return {
        **row.to_dict(),
        "estimated_minutes": row.predicted_wait_minutes,
        "estimated_retrieval_time": (
            row.predicted_ready_at.isoformat() if row.predicted_ready_at else None
        ),
        "recommended_dispatch_time": (
            row.dispatch_after.isoformat() if row.dispatch_after else None
        ),
        "recommended_dispatch": dispatch_label,
        "dispatch_label": dispatch_label,
        "dispatch_delay_minutes": delay,
        "fetch_minutes": snapshot.get("fetch_minutes"),
        "trained_on_synthetic_data": True,
        "model_version": row.model_version or MODEL_VERSION,
        "method": row.method,
        "disclaimer": PROTOTYPE_DISCLAIMER,
    }


def validate_ad_hoc_payload(payload: dict) -> dict[str, float]:
    """Validate a caller-supplied feature set for a what-if estimate.

    Every trained feature is required. Silently defaulting a missing one to zero
    would produce a confident answer from partly-guessed input -- the same
    failure mode the security ML endpoint refuses.
    """
    missing = [name for name in RETRIEVAL_FEATURE_COLUMNS if name not in payload]
    if missing:
        raise ValidationError(
            "Every feature the model was trained on must be supplied.",
            details={
                "missing_features": missing,
                "required_features": list(RETRIEVAL_FEATURE_COLUMNS),
            },
        )

    features: dict[str, float] = {}
    invalid: dict[str, str] = {}
    for name in RETRIEVAL_FEATURE_COLUMNS:
        try:
            features[name] = float(payload[name])
        except (TypeError, ValueError):
            invalid[name] = f"expected a number, got {payload[name]!r}"
    if invalid:
        raise ValidationError(
            "Some features are not numbers.",
            details={"field": "features", "invalid": invalid},
        )

    if features["queue_position"] < 1 or features["queue_size"] < 1:
        raise ValidationError(
            "Queue size and position must be at least 1.",
            details={"field": "queue_position", "received": features["queue_position"]},
        )
    if features["queue_position"] > features["queue_size"]:
        raise ValidationError(
            "Queue position cannot exceed queue size.",
            details={
                "queue_position": features["queue_position"],
                "queue_size": features["queue_size"],
            },
        )
    if features["active_valets"] < 0 or features["zone_capacity"] < 1:
        raise ValidationError(
            "Active valets cannot be negative and zone capacity must be at least 1.",
            details={"field": "zone_capacity", "zone_capacity": features["zone_capacity"]},
        )
    if not 0.0 <= features["zone_occupancy_percent"] <= 100.0:
        raise ValidationError(
            "Zone occupancy is a percentage and must be between 0 and 100.",
            details={
                "field": "zone_occupancy_percent",
                "received": features["zone_occupancy_percent"],
            },
        )
    if not 0.0 <= features["vehicle_flagged"] <= 1.0:
        raise ValidationError(
            "'vehicle_flagged' is a flag and must be 0 or 1.",
            details={"field": "vehicle_flagged", "received": features["vehicle_flagged"]},
        )
    for name in ("hour_sin", "hour_cos", "dow_sin", "dow_cos"):
        if not -1.0 <= features[name] <= 1.0:
            raise ValidationError(
                f"'{name}' is a sine or cosine and must be between -1 and 1.",
                details={"field": name, "received": features[name]},
            )
    return features


def estimate_ad_hoc(features: dict[str, float]) -> dict[str, Any]:
    """Score a supplied feature set without touching the database.

    Exists so the model can be exercised and its reasoning inspected without
    inventing a custody session -- useful when what is being demonstrated is the
    explanation rather than the record.
    """
    model = retrieval_model.model
    if model is None:
        raise ServiceUnavailableError(
            "No retrieval model is loaded. " + (retrieval_model.load_error or ""),
            details={"hint": "python utils/scripts/train_retrieval_model.py"},
        )
    prediction = model.predict(features)
    return {
        "estimated_minutes": round_minutes(prediction.estimated_minutes),
        "estimated_minutes_raw": round(prediction.estimated_minutes, 2),
        "confidence": prediction.confidence,
        "reason": prediction.reason,
        "contributions": prediction.contributions,
        "model_version": prediction.model_version,
        "method": prediction.method,
        "inputs": features,
        "persisted": False,
        "trained_on_synthetic_data": True,
        "disclaimer": prediction.disclaimer,
    }


__all__ = [
    "FALLBACK_ZONE_MEAN_MINUTES",
    "INTERVAL_FRACTION",
    "METHOD_NAME",
    "QueueSnapshot",
    "RetrievalModelService",
    "estimate",
    "estimate_ad_hoc",
    "latest_prediction",
    "predict_for_custody",
    "prediction_recency_order",
    "read_snapshot",
    "request_vehicle",
    "retrieval_model",
    "validate_ad_hoc_payload",
]


def request_vehicle(
    actor: AuthContext,
    custody_session_id: str,
) -> dict[str, Any]:
    """The whole pickup request, in one call.

    The five steps the brief names, in the order they have to happen:

    1. **Change the custody status** to ``RETRIEVAL_REQUESTED``. This goes
       through ``ticket_service.update_custody_status`` so the state machine and
       its audit entry are the ones already tested elsewhere, not a second
       implementation of the same rules.
    2. **Record the request** with a ``RETRIEVAL_REQUESTED`` audit entry. The
       status change is audited as a status change; this one is the customer's
       *act of asking*, which is the thing a dispute would turn on.
    3. **Generate the prediction**, after the status change and not before: the
       model counts how many retrievals are queued, and this request has to be
       in that count for the estimate to include it.
    4. **Make it visible to the valet dashboard**, which polls
       ``pending_retrievals``. There is no push channel in this project, so the
       notification is a row a queue reads, and the response says so rather than
       implying an event was delivered.
    5. **Return the estimate** for the customer to see immediately.

    **The estimate failing must not fail the request.** If no model is trained,
    the customer has still asked for their car and that has still been recorded;
    refusing the whole operation would mean an untrained predictor blocks
    pickups. The estimate is reported as unavailable with the reason, and the
    request stands.
    """
    # Lock the session so simultaneous clicks/retries cannot create two request
    # audit events or race the state transition.
    session = ticket_service.get_custody_session(actor, custody_session_id)
    session = db.session.execute(
        select(CustodySession)
        .where(CustodySession.id == session.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    active_retrieval_states = {
        CustodyStatus.RETRIEVAL_REQUESTED.value,
        CustodyStatus.RETRIEVAL_IN_PROGRESS.value,
        CustodyStatus.READY_FOR_PICKUP.value,
    }
    if (
        session.status in active_retrieval_states
        and session.retrieval_requested_at is not None
    ):
        try:
            estimate = latest_prediction(str(session.id))
        except NotFoundError:
            estimate = None
        return {
            "custody_session_id": str(session.id),
            "custody_status": session.status,
            "previous_status": session.status,
            "requested_at": session.retrieval_requested_at.isoformat(),
            "estimate": estimate,
            "estimate_available": estimate is not None,
            "estimate_error": (
                None
                if estimate is not None
                else "The request is active; no estimate has been recorded yet."
            ),
            "already_requested": True,
            "valet_notification": {
                "delivery": "polled",
                "queued": session.status == CustodyStatus.RETRIEVAL_REQUESTED.value,
                "note": (
                    "The valet dashboard reads this queue on a short poll. There is "
                    "no websocket channel, so the request appears there within one "
                    "poll interval rather than instantly."
                ),
            },
        }

    previous_status = session.status
    session = ticket_service.update_custody_status(
        actor, session.id, CustodyStatus.RETRIEVAL_REQUESTED.value, commit=False
    )

    record_event(
        action=AuditAction.PICKUP_REQUESTED,
        entity_type="custody_sessions",
        entity_id=session.id,
        custody_session_id=session.id,
        actor=actor,
        outcome=AuditOutcome.SUCCESS,
        description=(
            f"Customer requested vehicle retrieval; custody moved "
            f"{previous_status} -> {session.status}"
        ),
        metadata={"previous_status": previous_status, "new_status": session.status},
    )
    db.session.commit()

    estimate: dict[str, Any] | None = None
    estimate_error: str | None = None
    try:
        estimate = predict_for_custody(
            str(session.id),
            actor_id=actor.user_id,
            actor_role=actor.role,
            actor_email=actor.email,
        )
    except ServiceUnavailableError as exc:
        # No trained artifact. The request is still valid and still recorded.
        estimate_error = str(exc.message)
        logger.info("Retrieval requested without an estimate: %s", exc.message)

    db.session.refresh(session)

    return {
        "custody_session_id": str(session.id),
        "custody_status": session.status,
        "previous_status": previous_status,
        "requested_at": (
            session.retrieval_requested_at.isoformat()
            if session.retrieval_requested_at
            else None
        ),
        "estimate": estimate,
        "estimate_available": estimate is not None,
        "estimate_error": estimate_error,
        "already_requested": False,
        # What "notify" actually means here, stated rather than implied.
        "valet_notification": {
            "delivery": "polled",
            "queued": session.status == CustodyStatus.RETRIEVAL_REQUESTED.value,
            "note": (
                "The valet dashboard reads this queue on a short poll. There is "
                "no websocket channel, so the request appears there within one "
                "poll interval rather than instantly."
            ),
        },
    }
