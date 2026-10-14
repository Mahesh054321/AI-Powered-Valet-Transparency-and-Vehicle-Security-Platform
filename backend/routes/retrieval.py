"""Retrieval-time prediction endpoints (Phase 9b).

    POST /retrieval/predict          estimate when a vehicle will be ready
    GET  /retrieval/<custody_id>     the estimate that was stored for a session
    GET  /retrieval/model            what model produced it, and how well it did

``POST /retrieval/predict`` answers the customer-facing question. It measures the
live situation -- queue length, staffing, zone occupancy -- and turns it into
four things the brief asks for: an estimated retrieval time, an estimated number
of minutes, a queue position and a recommended dispatch time.

The stored row is what ``GET /retrieval/<custody_id>`` returns. Reading it back
rather than recomputing matters: the estimate a customer was shown is part of
the record, and a live re-estimate would quietly change it as the queue moves.

**Every response states that the model was trained on synthetic data.** The
brief requires this and it is not a formality -- an MVP that publishes an
"8 minutes" without saying where the 8 came from is a false claim about
accuracy, whatever the number happens to be.

Mounted at both ``/api/v1/retrieval`` and ``/api/retrieval``.
"""

from __future__ import annotations

from flask import Blueprint

from ai.retrieval.features import FEATURE_LABELS, RETRIEVAL_FEATURE_COLUMNS, feature_unit
from ai.retrieval.model import PROTOTYPE_DISCLAIMER
from services.customer_dashboard import pending_retrievals
from services.retrieval import (
    estimate_ad_hoc,
    latest_prediction,
    predict_for_custody,
    request_vehicle,
    retrieval_model,
    validate_ad_hoc_payload,
)
from utils.exceptions import ValidationError
from utils.requests import json_payload
from utils.responses import created, ok
from utils.security import (
    Role,
    current_user,
    require_authentication,
    require_role,
)


def _feature_reference() -> list[dict[str, str]]:
    """The feature list with human labels, so inputs can be checked not trusted."""
    return [
        {"name": name, "label": FEATURE_LABELS.get(name, name), "unit": feature_unit(name)}
        for name in RETRIEVAL_FEATURE_COLUMNS
    ]


def _honesty_block() -> dict[str, object]:
    """The disclosure that must accompany any retrieval estimate."""
    return {
        "trained_on_synthetic_data": True,
        "data_basis": (
            "synthetic historical retrieval records generated for this MVP by "
            "ai/retrieval/dataset.py; no real valet operation data was used"
        ),
        "disclaimer": PROTOTYPE_DISCLAIMER,
    }


def create_retrieval_blueprint() -> Blueprint:
    """Build a fresh retrieval blueprint (mounted at two prefixes)."""
    blueprint = Blueprint("retrieval", __name__)

    @blueprint.post("/predict")
    @blueprint.post("/predict/")
    @require_authentication
    def predict():
        """Estimate a retrieval.

        Two modes, because they answer different questions:

        * With ``custody_session_id`` the live situation is measured from the
          database and the estimate is stored against that session. This is the
          customer-facing path.
        * With ``features`` and no session id, a supplied feature set is scored
          and nothing is written. This is the inspection path -- it lets a
          reviewer ask "what would it say for a queue of five?" without
          fabricating a custody record to hang it on.

        The response carries the estimate, the interval around it, the queue
        position, the recommended dispatch time, and the per-feature
        contributions that produced it.
        """
        payload = json_payload(required=False)
        actor = current_user()

        custody_session_id = payload.get("custody_session_id") or payload.get("custody_id")
        raw_features = payload.get("features")

        if not custody_session_id and raw_features is None:
            raise ValidationError(
                "Supply either 'custody_session_id' to estimate a real session, "
                "or 'features' to score a what-if. Supplying neither would mean "
                "inventing both.",
                details={
                    "accepted": ["custody_session_id", "features"],
                    "required_features_for_what_if": list(RETRIEVAL_FEATURE_COLUMNS),
                },
            )
        if custody_session_id and raw_features is not None:
            raise ValidationError(
                "Supply 'custody_session_id' or 'features', not both. For a real "
                "session the inputs are measured from the database, so caller-"
                "supplied features would be silently ignored -- and an estimate "
                "whose inputs the caller believes they chose is worse than no "
                "estimate.",
                details={"accepted": ["custody_session_id", "features"]},
            )

        if custody_session_id:
            if not isinstance(custody_session_id, str) or not custody_session_id.strip():
                raise ValidationError(
                    "'custody_session_id' must be a non-empty string.",
                    details={"field": "custody_session_id"},
                )
            result = predict_for_custody(
                custody_session_id.strip(),
                actor_id=actor.user_id,
                actor_role=actor.role,
                actor_email=actor.email,
                persist=not bool(payload.get("persist") is False),
            )
            status = 201 if result.get("persisted") else 200
            responder = created if status == 201 else ok
            return responder(
                {
                    **_shape_estimate(result),
                    **_honesty_block(),
                    "mode": "custody_session",
                    "persisted": result.get("persisted", False),
                    "history_note": (
                        "This zone has no completed retrieval on record yet, so "
                        "the historical component of the estimate is a model "
                        "default rather than a measurement."
                        if result.get("history_is_fallback")
                        else (
                            f"Based on {result.get('historical_sample_size')} completed "
                            "retrievals recorded for this zone."
                        )
                    ),
                }
            )

        features = validate_ad_hoc_payload(raw_features)
        result = estimate_ad_hoc(features)
        return ok(
            {
                **_shape_estimate(result),
                **_honesty_block(),
                "mode": "what_if",
                "persisted": False,
                "note": (
                    "Scored from caller-supplied features. Nothing was measured "
                    "from the database and nothing was stored."
                ),
            }
        )

    @blueprint.post("/request")
    @blueprint.post("/request/")
    @require_authentication
    def request():
        """Ask for the vehicle: the whole pickup request in one call.

        Changes the custody status, records the request, generates the estimate
        and puts the session on the queue the valet dashboard reads. Doing this
        as one endpoint rather than four client calls matters: a browser that
        changed the status and then failed to generate an estimate would leave
        the customer waiting on a car nobody had been told to fetch.

        An untrained predictor does **not** block the request. The status change
        and the audit entry stand, and the response reports the estimate as
        unavailable with the reason.
        """
        payload = json_payload(required=False)
        custody_session_id = (
            payload.get("custody_session_id") or payload.get("custody_id")
        )
        if not custody_session_id or not isinstance(custody_session_id, str):
            raise ValidationError(
                "'custody_session_id' is required and must be a non-empty string.",
                details={"field": "custody_session_id"},
            )

        result = request_vehicle(current_user(), custody_session_id.strip())
        estimate = result.get("estimate")
        return created(
            {
                **result,
                # Surfaced at the top level too, because these are what the
                # customer's screen actually renders.
                "estimated_minutes": (estimate or {}).get("estimated_minutes"),
                "estimated_retrieval_time": (estimate or {}).get("estimated_ready_at"),
                "queue_position": (estimate or {}).get("queue_position"),
                "recommended_dispatch": (estimate or {}).get("dispatch_label"),
                "message": (
                    "Vehicle requested. A valet has been notified and your "
                    "estimated retrieval time is below."
                    if estimate
                    else (
                        "Vehicle requested and a valet has been notified. No "
                        "estimate could be produced, so the wait is not quoted."
                    )
                ),
                **_honesty_block(),
            }
        )

    @blueprint.get("/queue")
    @blueprint.get("/queue/")
    @require_role(Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN)
    def queue():
        """The retrieval queue staff act on.

        This is what the valet dashboard polls. Staff-only: the list spans every
        customer's vehicles, so a customer must not be able to read it.

        The response states that it is polled rather than pushed. Calling it a
        notification without saying so would imply a delivery guarantee this
        project does not have.
        """
        return ok(pending_retrievals(current_user()))

    @blueprint.get("/<custody_id>")
    @blueprint.get("/<custody_id>/")
    @require_authentication
    def get_prediction(custody_id: str):
        """The stored estimate for a custody session.

        Returns what was recorded when the customer asked, not a fresh estimate:
        the queue has moved since, and silently re-answering would mean the
        number the customer saw no longer exists anywhere.
        """
        result = latest_prediction(custody_id)
        return ok({**result, **_honesty_block()})

    @blueprint.get("/model")
    @blueprint.get("/model/")
    @require_authentication
    def model_info():
        """Describe the predictor and the MAE it was held out on.

        Exposed so a reviewer can judge the model rather than take the headline
        number on trust: the held-out MAE, the baseline MAE it had to beat, and
        the feature list are all here.
        """
        info = retrieval_model.describe()
        info["feature_columns"] = _feature_reference()
        info["primary_metric"] = "mae_minutes"
        info["disclaimer"] = PROTOTYPE_DISCLAIMER
        info["trained_on_synthetic_data"] = True
        return ok(info)

    return blueprint


def _shape_estimate(result: dict) -> dict:
    """Normalise a service result into the response contract.

    Both the persisted and the what-if path pass through here, so the two cannot
    drift into returning differently-named fields for the same idea.
    """
    shaped: dict[str, object] = {
        # The four things the brief requires, spelled the way the UI reads them.
        "estimated_retrieval_time": result.get("estimated_ready_at"),
        "estimated_minutes": result.get("estimated_minutes"),
        "queue_position": result.get("queue_position"),
        "recommended_dispatch_time": result.get("dispatch_after"),
        "recommended_dispatch": result.get("dispatch_label") or "Now",
        # Context around the point estimate.
        "min_wait_minutes": result.get("min_wait_minutes"),
        "max_wait_minutes": result.get("max_wait_minutes"),
        "estimated_minutes_exact": result.get("estimated_minutes_raw"),
        "queue_size": result.get("queue_size"),
        "dispatch_delay_minutes": result.get("dispatch_delay_minutes"),
        "fetch_minutes": result.get("fetch_minutes"),
        "confidence": result.get("confidence"),
        "reason": result.get("reason"),
        # The per-feature contributions the reason is built from. Without these
        # the explanation is an assertion the caller cannot check.
        "contributions": result.get("contributions"),
        "model_version": result.get("model_version"),
        "method": result.get("method"),
        "inputs": result.get("inputs"),
        "custody_session_id": result.get("custody_session_id"),
        "prediction_id": result.get("prediction_id"),
        "requested_at": result.get("requested_at"),
    }
    return {key: value for key, value in shaped.items() if value is not None}


__all__ = ["create_retrieval_blueprint"]
