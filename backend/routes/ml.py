"""Security ML model endpoints.

    GET  /ml/model      authenticated - what model is loaded, and what it can do
    POST /ml/classify   authenticated - score an ad-hoc feature set

Two endpoints, both read-only in the sense that neither writes to the database.
Classification happens inline during telemetry ingestion and is persisted there;
``POST /ml/classify`` exists so the model can be exercised and inspected without
fabricating a GPS fix, which matters when the thing you are demonstrating is the
*reasoning* rather than the record.

**These endpoints report a prototype.** Every response carries the disclaimer and
``trained_on_synthetic_data: true``. The ``GET /ml/model`` response includes the
training metrics and the confusion matrix so a reviewer can judge the model
rather than take a headline number on trust -- and so that a reader who only has
the API in front of them cannot mistake it for a production system.

Mounted at both ``/api/v1/ml`` and ``/api/ml``.
"""

from __future__ import annotations

from flask import Blueprint

from ai.ml.features import FEATURE_COLUMNS, FEATURE_LABELS, feature_unit
from ai.ml.model import PROTOTYPE_DISCLAIMER
from utils.exceptions import ValidationError
from utils.requests import json_payload
from utils.responses import ok
from utils.security import require_authentication


def _coerce_features(payload: dict) -> dict[str, float]:
    """Validate and coerce a caller-supplied feature dict.

    Every feature is required. Omitting one and silently defaulting it to zero
    would produce a confident prediction from incomplete input, which is the
    failure mode this whole endpoint exists to avoid: the point of exposing the
    model is that its inputs are visible and stated.
    """
    missing = [name for name in FEATURE_COLUMNS if name not in payload]
    if missing:
        raise ValidationError(
            "Every feature the model was trained on must be supplied. Omitting "
            "one would mean silently defaulting it to zero, and a confident "
            "prediction from partly-guessed input is exactly what this endpoint "
            "exists to prevent.",
            details={
                "missing_features": missing,
                "required_features": list(FEATURE_COLUMNS),
            },
        )

    features: dict[str, float] = {}
    invalid: dict[str, str] = {}
    for name in FEATURE_COLUMNS:
        raw = payload[name]
        try:
            features[name] = float(raw)
        except (TypeError, ValueError):
            invalid[name] = f"expected a number, got {raw!r}"
    if invalid:
        raise ValidationError(
            "Some features are not numbers.",
            details={"field": "features", "invalid": invalid},
        )

    if not 0.0 <= features["zone_breach"] <= 1.0:
        raise ValidationError(
            "'zone_breach' is a flag and must be 0 or 1.",
            details={"field": "zone_breach", "received": features["zone_breach"]},
        )
    if features["speed_kmph"] < 0 or features["distance_travelled_metres"] < 0:
        raise ValidationError(
            "Distances and speeds cannot be negative.",
            details={
                "field": "speed_kmph",
                "speed_kmph": features["speed_kmph"],
                "distance_travelled_metres": features["distance_travelled_metres"],
            },
        )
    return features


def create_ml_blueprint() -> Blueprint:
    """Build a fresh ML blueprint (mounted at two prefixes)."""
    blueprint = Blueprint("ml", __name__)

    @blueprint.get("/model")
    @blueprint.get("/model/")
    @require_authentication
    def model_info():
        """Describe the loaded classifier, or explain why there is none.

        Returns the training metrics, the confusion matrix, the feature
        importances and the disclaimer together. When no artifact is trained the
        response says so and gives the command that would produce one -- a
        rules-only deployment is a supported state, and hiding it would make the
        dashboard look broken when it is merely untrained.
        """
        from services.security_ml import security_model

        info = security_model.describe()
        info["feature_columns"] = [
            {"name": name, "label": FEATURE_LABELS.get(name, name), "unit": feature_unit(name)}
            for name in FEATURE_COLUMNS
        ]
        info["trained_on_synthetic_data"] = True
        info["disclaimer"] = PROTOTYPE_DISCLAIMER
        return ok(info)

    @blueprint.post("/classify")
    @blueprint.post("/classify/")
    @require_authentication
    def classify():
        """Score one feature set and explain the result.

        Returns the four things the brief asks for -- classification,
        probability, reason, severity -- plus the per-feature contributions that
        the reason is built from. Nothing is persisted: this is an inspection
        tool, not a telemetry endpoint.
        """
        from services.security_ml import security_model

        payload = json_payload(required=False)
        features = _coerce_features(payload)

        if not security_model.available:
            # Not an error condition. Say plainly what is missing and how to fix
            # it, rather than returning a 500 or an empty result.
            raise ValidationError(
                "No security classifier is loaded, so there is nothing to score. "
                "Train one with "
                "'python utils/scripts/train_security_model.py' and retry.",
                details={
                    "reason": security_model.load_error,
                    "required_features": list(FEATURE_COLUMNS),
                },
            )

        opinion = security_model.classify(features)
        if opinion is None:  # pragma: no cover - raced with a reload
            raise ValidationError(
                "The classifier could not score this input.",
                details={"reason": security_model.load_error},
            )

        return ok(
            {
                "classification": opinion.classification,
                "probability": round(opinion.probability, 4),
                "confidence": round(opinion.probability, 4),
                "severity": opinion.severity,
                "severity_basis": "graded from P(SUSPICIOUS), not from measured risk",
                "reason": opinion.reason,
                "top_factors": [
                    {
                        "feature": name,
                        "label": FEATURE_LABELS.get(name, name),
                        "unit": feature_unit(name),
                        "value": features[name],
                        "contribution": round(weight, 4),
                        "direction": "towards SUSPICIOUS" if weight > 0 else "towards NORMAL",
                    }
                    for name, weight in opinion.top_factors
                ],
                "model_version": opinion.model_version,
                "trained_on_synthetic_data": True,
                "disclaimer": PROTOTYPE_DISCLAIMER,
                "inputs": features,
                # Recomputed from the same bands the ingestion path uses, so a
                # caller can check the mapping rather than trust the label.
                "severity_bands": [
                    {"min_probability": threshold, "severity": severity}
                    for threshold, severity in _severity_bands()
                ],
            }
        )

    return blueprint


def _severity_bands() -> list[tuple[float, str]]:
    from services.security_ml import SEVERITY_BANDS

    return list(SEVERITY_BANDS)


__all__ = ["create_ml_blueprint"]
