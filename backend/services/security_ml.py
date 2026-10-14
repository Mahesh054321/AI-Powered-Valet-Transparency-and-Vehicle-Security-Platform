"""Prototype ML classification, and the rule that keeps it subordinate.

This module is the seam between the geofence rule engine and the trained
classifier. It does three things:

1. **Load the artifact once.** The model is read from disk lazily and cached, so
   a request path never pays for a pickle load. If no artifact exists, the
   service reports itself unavailable and the pipeline behaves exactly as it did
   before the ML layer existed -- a supported deployment state, not a failure.

2. **Score one fix.** Features are built from the same numbers the rule engine
   already has, so the classifier sees the measurement, not a re-derivation of
   it.

3. **Combine the two opinions under one hard rule.**

The combination rule
--------------------
**The rule engine is a floor. The classifier may only raise a verdict, never
lower it.**

    rule BREACH      + ML NORMAL      -> BREACH      (alert still raised)
    rule SUSPICIOUS  + ML NORMAL      -> SUSPICIOUS  (not downgraded)
    rule WARNING     + ML SUSPICIOUS  -> SUSPICIOUS  (escalated)
    rule NORMAL      + ML SUSPICIOUS  -> SUSPICIOUS  (escalated)

Why the asymmetry matters: the classifier is a prototype trained on synthetic
telemetry whose labels were written by hand in ``ai/ml/dataset.py``. It has
never seen a real vehicle. Treating its disagreement as grounds to *quiet* a
measurement would let a guess override an observation, and the failure mode
would be a breach that raised no alert -- precisely the thing this project
exists to prevent. Escalating on a false positive costs a reviewer a glance.
De-escalating on a false negative costs the vehicle. Those are not comparable
errors, so only the cheap one is permitted.

The one case where ML could plausibly be trusted to override -- a rule BREACH it
considers benign -- is explicitly refused. ``tests/test_security_ml.py`` asserts
it, because a future refactor that "simplifies" the combination into a
max-of-ranks over *both* inputs would silently reintroduce it.

**Honesty.** The classifier's version, probability and disclaimer travel with
every prediction into the API response and onto the stored GPS row. Nothing here
presents a prototype's guess as a measurement. See ``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app

from ai.anomaly import geofence as engine
from ai.ml.features import (
    FEATURE_COLUMNS,
    FEATURE_LABELS,
    PriorObservation,
    extract_features,
    feature_unit,
)
from ai.ml.model import MODEL_VERSION, PROTOTYPE_DISCLAIMER, SecurityClassifier
from models.enums import AlertSeverity, AlertType, MlClassification, MovementStatus

logger = logging.getLogger(__name__)

#: Below this P(SUSPICIOUS) the classifier's opinion is recorded on the row but
#: does not change the fix's status. Above 0.5 on purpose: escalating on a coin
#: flip would fill the alert queue with noise and teach reviewers to ignore it,
#: which is worse than not escalating at all.
DEFAULT_ESCALATION_THRESHOLD = 0.70

#: Severity bands for an ML-driven escalation. These grade the *classifier's*
#: confidence, not the danger of the event -- a rule BREACH gets its severity
#: from the rule that fired, which knows the boundary; this only knows how
#: confidently the model disagreed with "normal".
SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.90, AlertSeverity.HIGH.value),
    (0.70, AlertSeverity.MEDIUM.value),
    (0.00, AlertSeverity.LOW.value),
)

#: The alert type an ML escalation raises. ``ABNORMAL_MOVEMENT`` rather than
#: ``GEOFENCE_BREACH``: the classifier is describing an unusual pattern, and
#: claiming a boundary crossing it never measured would be a lie the map could
#: contradict. ``AlertType`` is a closed vocabulary, so this is the honest
#: choice available in it rather than a new value added for convenience.
ML_ALERT_TYPE = AlertType.ABNORMAL_MOVEMENT.value

#: Marks provenance in the verdict evidence and therefore in the alert row.
ML_EVIDENCE_KEY = "ml"


def severity_for_probability(probability: float) -> str:
    """Map P(SUSPICIOUS) onto a severity band."""
    for threshold, severity in SEVERITY_BANDS:
        if probability >= threshold:
            return severity
    return AlertSeverity.LOW.value


@dataclass(frozen=True)
class MlOpinion:
    """The classifier's verdict on one fix, with the evidence attached."""

    classification: str
    probability: float
    severity: str
    reason: str
    model_version: str
    top_factors: tuple[tuple[str, float], ...] = ()
    disclaimer: str = PROTOTYPE_DISCLAIMER

    @property
    def is_suspicious(self) -> bool:
        return self.classification == MlClassification.SUSPICIOUS.value

    def as_dict(self) -> dict[str, Any]:
        """The shape the API returns. Every field is present or the whole
        object is ``None``; there is no half-populated prediction."""
        return {
            "classification": self.classification,
            "probability": self.probability,
            "severity": self.severity,
            "reason": self.reason,
            "model_version": self.model_version,
            "top_factors": [
                {"feature": name, "weight": weight} for name, weight in self.top_factors
            ],
            "disclaimer": self.disclaimer,
        }


@dataclass(frozen=True)
class CombinedVerdict:
    """A rule verdict after the classifier has had its say.

    ``verdict`` is a full :class:`~ai.anomaly.geofence.MovementVerdict`, so
    everything downstream -- alert raising, the tracking view -- keeps working
    unchanged whether or not the model was available.
    """

    verdict: engine.MovementVerdict
    opinion: MlOpinion | None
    rule_status: str
    escalated: bool
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.verdict.status

    def as_dict(self) -> dict[str, Any]:
        return {
            "movement_status": self.verdict.status,
            "rule_status": self.rule_status,
            "escalated_by_ml": self.escalated,
            "escalation_rationale": self.rationale,
            "reasons": self.verdict.reasons,
            "ml": self.opinion.as_dict() if self.opinion is not None else None,
        }


# ---------------------------------------------------------------------------
# the combination rule
# ---------------------------------------------------------------------------
def _ml_finding(opinion: MlOpinion) -> engine.Finding:
    """Phrase an escalation as a rule-shaped finding.

    Reusing ``engine.Finding`` is deliberate: the alert-raising code already
    iterates findings, so an escalation produces a throttled alert with a title,
    a reason and a confidence without a second code path that could drift.
    """
    factors = ", ".join(
        f"{FEATURE_LABELS.get(name, name)} ({weight:.2f})"
        for name, weight in opinion.top_factors[:3]
    )
    reason = (
        f"Prototype classifier estimates a {opinion.probability:.0%} chance of "
        f"suspicious behaviour. Leading factors: {factors}. "
        "This is a synthetic-data prototype and its opinion is advisory: the "
        "rule-based verdict was not lowered, only raised."
    )
    return engine.Finding(
        alert_type=ML_ALERT_TYPE,
        severity=opinion.severity,
        title="ML classifier flagged an unusual movement pattern",
        reason=reason,
        confidence=opinion.probability,
        status=MovementStatus.SUSPICIOUS.value,
    )


def combine(
    verdict: engine.MovementVerdict,
    opinion: MlOpinion | None,
    *,
    escalation_threshold: float = DEFAULT_ESCALATION_THRESHOLD,
) -> CombinedVerdict:
    """Merge a rule verdict with the classifier's opinion.

    Escalate-only, as documented in the module docstring. Returns a
    ``CombinedVerdict`` whose ``verdict`` is always safe to use directly.
    """
    rule_status = verdict.status

    # No model, or no opinion: the rule engine's answer stands untouched. This
    # is also the path a checkout with no trained artifact takes.
    if opinion is None:
        return CombinedVerdict(
            verdict=verdict,
            opinion=None,
            rule_status=rule_status,
            escalated=False,
            rationale="No ML model available; rule-based verdict used alone.",
            evidence={},
        )

    evidence = {ML_EVIDENCE_KEY: opinion.as_dict()}

    # -- the case that must never change -----------------------------------
    # A BREACH is a measured boundary crossing. Whatever the classifier thinks,
    # the vehicle was outside its zone, and the breach finding stays in the list
    # so the alert fires. Recording ML's disagreement is useful context for the
    # reviewer; acting on it would not be.
    if rule_status == MovementStatus.BREACH.value:
        return CombinedVerdict(
            verdict=verdict,
            opinion=opinion,
            rule_status=rule_status,
            escalated=False,
            rationale=(
                "A geofence breach is a measurement and is never downgraded by "
                f"the classifier, which rated it {opinion.probability:.0%} "
                "suspicious. The breach alert is raised regardless."
            ),
            evidence=evidence,
        )

    # -- the classifier is not confident enough to escalate -----------------
    if opinion.probability < escalation_threshold:
        return CombinedVerdict(
            verdict=verdict,
            opinion=opinion,
            rule_status=rule_status,
            escalated=False,
            rationale=(
                f"Classifier confidence {opinion.probability:.0%} is below the "
                f"{escalation_threshold:.0%} escalation threshold, so the "
                "rule-based verdict stands. Its opinion is still recorded."
            ),
            evidence=evidence,
        )

    # -- it is confident, but only escalation is permitted ------------------
    escalated_status = MovementStatus.SUSPICIOUS.value
    if engine._rank(escalated_status) <= engine._rank(rule_status):  # noqa: SLF001
        # Already at or above what ML could add. Nothing to do, and this is what
        # keeps a rule SUSPICIOUS from being touched by an ML NORMAL.
        return CombinedVerdict(
            verdict=verdict,
            opinion=opinion,
            rule_status=rule_status,
            escalated=False,
            rationale=(
                f"Rule-based verdict was already {rule_status}, at or above what "
                "the classifier can contribute. It is never lowered."
            ),
            evidence=evidence,
        )

    finding = _ml_finding(opinion)
    merged = engine.MovementVerdict(
        status=escalated_status,
        distance_from_boundary_metres=verdict.distance_from_boundary_metres,
        is_within_zone=verdict.is_within_zone,
        findings=verdict.findings + (finding,),
        evidence={**verdict.evidence, **evidence},
    )
    return CombinedVerdict(
        verdict=merged,
        opinion=opinion,
        rule_status=rule_status,
        escalated=True,
        rationale=(
            f"Classifier rated this fix {opinion.probability:.0%} suspicious, "
            f"above the {escalation_threshold:.0%} threshold, so the verdict was "
            f"raised from {rule_status} to {escalated_status}."
        ),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# model loading and scoring
# ---------------------------------------------------------------------------
class SecurityModelService:
    """Loads, caches and queries the prototype classifier.

    Thread-safe: the lock covers only the load, and a load failure is cached as
    "unavailable" rather than retried per request, so a missing artifact costs
    one log line and not one exception per fix.
    """

    def __init__(self) -> None:
        self._model: SecurityClassifier | None = None
        self._attempted = False
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # -- configuration ------------------------------------------------------
    @staticmethod
    def _config() -> dict[str, Any]:
        try:
            config = current_app.config
        except RuntimeError:  # pragma: no cover - outside an app context
            config = {}
        return {
            "enabled": bool(config.get("ML_ENABLED", True)),
            "path": Path(
                config.get("ML_MODEL_PATH", Path("storage/models/security_classifier.json"))
            ),
            "threshold": float(
                config.get("ML_ESCALATION_THRESHOLD", DEFAULT_ESCALATION_THRESHOLD)
            ),
        }

    # -- loading -------------------------------------------------------------
    @property
    def model(self) -> SecurityClassifier | None:
        """The loaded classifier, or ``None`` when unavailable."""
        with self._lock:
            if self._attempted:
                return self._model
            self._attempted = True
            config = self._config()
            if not config["enabled"]:
                self._load_error = "ML classification disabled by configuration."
                return None
            path = config["path"]
            if not path.exists():
                # Not an error. Rules-only is a supported way to run this.
                self._load_error = (
                    f"No model artifact at {path}. Run "
                    "'python utils/scripts/train_security_model.py' to train one. "
                    "Running on rule-based checks alone."
                )
                logger.info("Security ML unavailable: %s", self._load_error)
                return None
            try:
                self._model = SecurityClassifier.load(path)
                logger.info(
                    "Security ML loaded: %s (%s)", MODEL_VERSION, self._model.algorithm
                )
            except Exception as exc:  # noqa: BLE001 - any load failure must degrade
                # Deliberately broad: a corrupt artifact, a scikit-learn version
                # mismatch or an unpicklable payload must never take down
                # telemetry ingestion. Degrade to rules and say why.
                self._load_error = f"Model artifact could not be loaded: {exc}"
                logger.warning("Security ML load failed: %s", self._load_error)
                self._model = None
            return self._model

    @property
    def available(self) -> bool:
        return self.model is not None

    @property
    def load_error(self) -> str | None:
        """Why the model is unavailable, for the health response.

        Reading ``model`` first is not decoration: the load is lazy, so before
        it runs there is no error to report. Property access has to happen for
        ``_load_error`` to be populated at all.
        """
        _ = self.model
        return self._load_error

    def reset(self) -> None:
        """Forget the cached model. Used by tests after writing a new artifact."""
        with self._lock:
            self._model = None
            self._attempted = False
            self._load_error = None

    def describe(self) -> dict[str, Any]:
        """Model metadata for the health/model endpoints."""
        config = self._config()
        model = self.model
        if model is None:
            return {
                "available": False,
                "enabled": config["enabled"],
                "reason": self._load_error,
                "escalation_threshold": config["threshold"],
                "disclaimer": PROTOTYPE_DISCLAIMER,
            }
        return {
            **model.describe(),
            "available": True,
            "enabled": config["enabled"],
            "escalation_threshold": config["threshold"],
            "feature_columns": list(model.feature_columns),
        }

    # -- scoring -------------------------------------------------------------
    def classify(self, features: dict[str, float]) -> MlOpinion | None:
        """Score one feature vector. ``None`` when no model is available."""
        model = self.model
        if model is None:
            return None
        try:
            prediction = model.predict(features)
        except Exception as exc:  # noqa: BLE001 - scoring must not break ingestion
            logger.warning("Security ML scoring failed, falling back to rules: %s", exc)
            return None

        probability = float(prediction.probability)
        # `explain()` returns (detail, contributions, basis); the contributions
        # are the per-feature log-odds terms. Ranked by size, positive ones are
        # what pushed the estimate towards SUSPICIOUS, which is what a reviewer
        # needs to see to judge whether they agree.
        _, contributions, basis = model.explain(features)
        ranked = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
        top = tuple(
            (name, weight) for name, weight in ranked if abs(weight) > 1e-6
        )[:5]
        factors = ", ".join(
            f"{FEATURE_LABELS.get(name, name)} {self._format(name, features.get(name))} "
            f"({'+' if weight >= 0 else '-'}{abs(weight):.2f})"
            for name, weight in top[:3]
        ) or "no single feature dominated"

        if prediction.classification == MlClassification.SUSPICIOUS.value:
            reason = (
                f"Prototype classifier: {probability:.0%} chance of suspicious behaviour "
                f"({model.algorithm}). Leading factors: {factors}. "
                f"Contribution basis: {basis}."
            )
        else:
            reason = (
                f"Prototype classifier: {probability:.0%} chance of suspicious behaviour "
                f"({model.algorithm}). Nothing in this fix stood out against the "
                f"synthetic examples it was trained on. Contribution basis: {basis}."
            )

        return MlOpinion(
            classification=prediction.classification,
            probability=probability,
            severity=severity_for_probability(probability),
            reason=reason,
            model_version=prediction.model_version,
            top_factors=top,
        )

    @staticmethod
    def _format(name: str, value: float | None) -> str:
        if value is None:
            return "unavailable"
        unit = feature_unit(name)
        return f"= {value:.1f} {unit}".strip() if unit else f"= {value:.1f}"

    # -- the full path used by ingestion -------------------------------------
    def evaluate(
        self,
        verdict: engine.MovementVerdict,
        features: dict[str, float],
    ) -> CombinedVerdict:
        """Score a fix and combine it with the rule verdict in one call."""
        threshold = self._config()["threshold"]
        opinion = self.classify(features)
        return combine(verdict, opinion, escalation_threshold=threshold)


#: One service per process. Loading a scikit-learn estimator per request would
#: be wasteful, and the artifact does not change under a running server.
security_model = SecurityModelService()


def features_from_verdict(
    *,
    verdict: engine.MovementVerdict,
    latitude: float,
    longitude: float,
    speed_kmph: float | None,
    heading_degrees: float | None,
    recorded_at: datetime | None,
    priors: tuple[engine.PriorFix, ...] = (),
    buffer_metres: float = 0.0,
) -> dict[str, float]:
    """Build the classifier's features from the rule engine's own outputs.

    Reusing ``verdict.distance_from_boundary_metres`` and
    ``verdict.is_within_zone`` rather than recomputing them matters: the
    classifier and the rule engine must be judging the *same* measurement, or a
    disagreement between them would be an artefact of two different geometric
    calculations rather than a genuine difference of opinion.

    ``priors`` are the same :class:`~ai.anomaly.geofence.PriorFix` values the
    rule engine was given, converted rather than re-queried, so both layers see
    one history. ``PriorFix`` carries no heading; ``PriorObservation.heading_
    degrees`` stays ``None`` and ``extract_features`` falls back to the bearing
    implied by the positions, which is weaker evidence and labelled as such.
    """
    observations = tuple(
        PriorObservation(
            latitude=float(prior.latitude),
            longitude=float(prior.longitude),
            recorded_at=prior.recorded_at,
            speed_kmph=prior.speed_kmph,
            heading_degrees=getattr(prior, "heading_degrees", None),
        )
        for prior in priors
    )
    vector = extract_features(
        latitude=latitude,
        longitude=longitude,
        recorded_at=recorded_at,
        speed_kmph=speed_kmph,
        heading_degrees=heading_degrees,
        distance_from_zone_metres=verdict.distance_from_boundary_metres,
        is_within_zone=verdict.is_within_zone,
        buffer_metres=buffer_metres,
        priors=observations,
    )
    missing = [name for name in FEATURE_COLUMNS if name not in vector.values]
    if missing:  # pragma: no cover - guards against feature drift
        raise ValueError(f"Feature vector is missing {missing}")
    return vector.values


__all__ = [
    "DEFAULT_ESCALATION_THRESHOLD",
    "ML_ALERT_TYPE",
    "ML_EVIDENCE_KEY",
    "CombinedVerdict",
    "MlOpinion",
    "SecurityModelService",
    "combine",
    "features_from_verdict",
    "security_model",
    "severity_for_probability",
]
