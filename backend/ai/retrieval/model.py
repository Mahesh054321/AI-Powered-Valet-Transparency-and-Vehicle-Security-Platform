"""A baseline retrieval-time predictor.

Deliberately a linear model. The brief asks for a baseline, and a linear fit is
the right first answer here for three reasons that matter more than accuracy:

1. **Its coefficients are the explanation.** A minute per vehicle in the queue,
   a minute per unit of zone occupancy -- these are numbers a manager can sanity
   check against how their yard actually behaves. A gradient-boosted model would
   score better on synthetic data and explain nothing, and an estimate the staff
   cannot interrogate will not survive first contact with a queue.
2. **It cannot fit noise the way a flexible model can.** The training data is
   synthetic; a high-capacity model would memorise the generator's quirks and
   report an MAE that flatters it.
3. **It is small enough to audit.** One file, no pickle by default.

**MAE is the primary metric**, as the brief specifies, and for a good reason:
mean *squared* error is dominated by the rare long retrieval, which is not how a
customer experiences the estimate. A median absolute error and RMSE are reported
alongside it, but MAE is the headline.

**MAE alone is meaningless, so a baseline is always reported with it.** A
predictor that ignored every feature and answered the training median would
score some MAE too. The model is only worth keeping if it beats that number, and
the evaluation script says so explicitly rather than leaving the reader to
wonder whether 3.2 minutes is good.

Saved as plain JSON -- coefficients, intercept, and the scaler's mean and
standard deviation. No pickle, so loading an artifact cannot execute anything
and the file can be read and diffed by a human.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ai.retrieval.features import (
    FEATURE_LABELS,
    RETRIEVAL_FEATURE_COLUMNS,
    feature_unit,
)

MODEL_VERSION = "retrieval-baseline/1.0.0"
METHOD_NAME = "linear-ridge-v1"

PROTOTYPE_DISCLAIMER = (
    "This estimate comes from a baseline model trained on synthetic historical "
    "data generated for this MVP. It has never seen a real valet operation and "
    "must not be treated as an operational forecast. Treat it as an indicative "
    "planning number."
)


@dataclass(frozen=True)
class RetrievalMetrics:
    """How well the model predicted, against the baseline it has to beat."""

    rows: int
    mae: float
    median_absolute_error: float
    rmse: float
    mean_actual: float
    mean_predicted: float
    #: MAE of always answering the training median -- the floor to beat.
    baseline_mae: float
    #: MAE of always answering the training mean. Reported because it is the
    #: number a naive "average retrieval takes N minutes" claim would score.
    mean_baseline_mae: float
    r2: float
    worst_error_minutes: float
    within_5_minutes: float
    within_10_minutes: float

    @property
    def improvement_over_baseline(self) -> float:
        """Fractional MAE reduction versus the median predictor.

        Negative means the model is worse than ignoring every feature, which is
        a real possible outcome and is surfaced rather than hidden.
        """
        if self.baseline_mae <= 0:
            return 0.0
        return (self.baseline_mae - self.mae) / self.baseline_mae

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "mae_minutes": round(self.mae, 3),
            "median_absolute_error_minutes": round(self.median_absolute_error, 3),
            "rmse_minutes": round(self.rmse, 3),
            "mean_actual_minutes": round(self.mean_actual, 2),
            "mean_predicted_minutes": round(self.mean_predicted, 2),
            "baseline_mae_minutes": round(self.baseline_mae, 3),
            "mean_baseline_mae_minutes": round(self.mean_baseline_mae, 3),
            "improvement_over_baseline": round(self.improvement_over_baseline, 4),
            "r2": round(self.r2, 4),
            "worst_error_minutes": round(self.worst_error_minutes, 2),
            "within_5_minutes": round(self.within_5_minutes, 4),
            "within_10_minutes": round(self.within_10_minutes, 4),
            "primary_metric": "mae_minutes",
        }


@dataclass(frozen=True)
class RetrievalPrediction:
    """One estimate, with the reasoning attached."""

    estimated_minutes: float
    confidence: float
    reason: str
    contributions: dict[str, float] = field(default_factory=dict)
    model_version: str = MODEL_VERSION
    method: str = METHOD_NAME
    trained_on_synthetic_data: bool = True
    disclaimer: str = PROTOTYPE_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_minutes": round(self.estimated_minutes, 1),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "contributions": {key: round(value, 3) for key, value in self.contributions.items()},
            "model_version": self.model_version,
            "method": self.method,
            "trained_on_synthetic_data": self.trained_on_synthetic_data,
            "disclaimer": self.disclaimer,
        }


def mean_absolute_error(actual: np.ndarray, predicted: np.ndarray) -> float:
    """The primary metric: mean of |actual - predicted|, in minutes."""
    if actual.size == 0:
        return 0.0
    return float(np.mean(np.abs(actual - predicted)))


class RetrievalModel:
    """Ridge regression over the retrieval features.

    A thin, explicit wrapper rather than a general framework: one problem, one
    algorithm, every method short enough to read.
    """

    def __init__(self, *, alpha: float = 1.0, random_state: int = 0) -> None:
        self.alpha = alpha
        self.random_state = random_state
        self.feature_columns: tuple[str, ...] = RETRIEVAL_FEATURE_COLUMNS
        self._estimator: Any = None
        self._mean: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        #: The training median, used for the baseline comparison and as the
        #: fallback when the model is asked about something absurd.
        self.train_median: float = 0.0
        self.metrics: RetrievalMetrics | None = None
        self.training_summary: dict[str, Any] = {}
        self.trained_at: str | None = None

    # -- training -----------------------------------------------------------
    def _build_estimator(self) -> Any:
        from sklearn.linear_model import Ridge

        # Ridge rather than plain least squares: with correlated features (queue
        # size and queue position move together) OLS coefficients can swing
        # wildly between fits, and since the coefficients are the explanation
        # shown to users, stability matters more than a marginally better fit.
        return Ridge(alpha=self.alpha, random_state=self.random_state)

    def fit(self, dataset: Any) -> RetrievalModel:
        """Fit on the training rows of a ``RetrievalDataset`` and score the rest."""
        train_mask = ~dataset.test_mask
        X_train = dataset.X[train_mask]
        y_train = dataset.y[train_mask]
        if X_train.shape[0] < 10:
            raise ValueError(
                f"refusing to fit on {X_train.shape[0]} rows; a linear model needs "
                "more than a handful of examples to produce coefficients that "
                "mean anything"
            )

        self._mean = X_train.mean(axis=0)
        # A constant feature would divide by zero; floor the scale at 1 so such
        # a column simply contributes nothing rather than producing NaN that
        # would silently poison every later prediction.
        self._scale = np.where(X_train.std(axis=0) > 1e-9, X_train.std(axis=0), 1.0)

        self._estimator = self._build_estimator()
        self._estimator.fit(self._scale_features(X_train), y_train)
        self.train_median = float(np.median(y_train))
        self.trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.training_summary = dataset.summary()

        self.metrics = self.score(dataset.X[dataset.test_mask], dataset.y[dataset.test_mask])
        return self

    def _scale_features(self, X: np.ndarray) -> np.ndarray:
        self._require_fitted()
        return (X - self._mean) / self._scale

    def _require_fitted(self) -> None:
        if self._estimator is None or self._mean is None or self._scale is None:
            raise RuntimeError("this model has not been fitted; call fit() or load() first")

    # -- evaluation ---------------------------------------------------------
    def score(self, X: np.ndarray, y: np.ndarray) -> RetrievalMetrics:
        """Score held-out rows, always reporting the baseline alongside."""
        self._require_fitted()
        if X.shape[0] == 0:
            raise ValueError("cannot score an empty set")

        # A retrieval cannot take negative time. Clamping is not cosmetic: an
        # unclamped linear model will happily predict -3 minutes for an empty
        # queue at 4am, and storing that would violate the schema's own CHECK.
        predicted = self.predict_array(X)

        errors = np.abs(y - predicted)
        baseline = np.full_like(y, self.train_median if self.train_median else float(np.median(y)))
        mean_baseline = np.full_like(y, float(np.mean(y)))

        ss_res = float(np.sum((y - predicted) ** 2))
        ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))

        return RetrievalMetrics(
            rows=int(X.shape[0]),
            mae=mean_absolute_error(y, predicted),
            median_absolute_error=float(np.median(errors)),
            rmse=float(np.sqrt(np.mean((y - predicted) ** 2))),
            mean_actual=float(np.mean(y)),
            mean_predicted=float(np.mean(predicted)),
            baseline_mae=mean_absolute_error(y, baseline),
            mean_baseline_mae=mean_absolute_error(y, mean_baseline),
            r2=(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0,
            worst_error_minutes=float(np.max(errors)),
            within_5_minutes=float(np.mean(errors <= 5.0)),
            within_10_minutes=float(np.mean(errors <= 10.0)),
        )

    # -- inference ----------------------------------------------------------
    def predict_minutes(self, features: dict[str, float]) -> float:
        """Estimated retrieval time in minutes, floored at one minute.

        Routes through ``predict_array`` rather than calling the estimator
        directly, so there is exactly one inference path. It originally did not:
        this method skipped the scaler while the batch path applied it, and the
        two disagreed by a factor of ten. Because the live service scores one
        request at a time, that would have put the broken path in production
        while every batch-scored metric still looked correct.
        """
        return float(self.predict_array(self._vector_from(features).reshape(1, -1))[0])

    def predict_array(self, X: np.ndarray) -> np.ndarray:
        """Estimate many rows at once, in feature-matrix order.

        Public so the evaluation script can score batches without reaching into
        the model's private scaler, which would silently break if the scaling
        ever changed.
        """
        self._require_fitted()
        return np.clip(self._estimator.predict(self._scale_features(X)), 1.0, None)

    def _vector_from(self, features: dict[str, float]) -> np.ndarray:
        missing = [name for name in self.feature_columns if name not in features]
        if missing:
            raise ValueError(
                f"missing features: {', '.join(missing)}; the model was trained on "
                f"{list(self.feature_columns)}"
            )
        return np.array([float(features[name]) for name in self.feature_columns], dtype=float)

    def explain(self, features: dict[str, float]) -> tuple[dict[str, float], str]:
        """Per-feature contributions to the estimate, in minutes.

        For a linear model these are exact: coefficient times the scaled value,
        and they sum with the intercept to the prediction. That is what makes
        the reason string a statement about this estimate rather than a generic
        description of the model.
        """
        self._require_fitted()
        vector = self._vector_from(features)
        scaled = (vector - self._mean) / self._scale
        coefficients = np.asarray(self._estimator.coef_).ravel()
        contributions = {
            name: float(coefficients[index] * scaled[index])
            for index, name in enumerate(self.feature_columns)
        }
        return contributions, "exact linear contributions in minutes"

    def predict(self, features: dict[str, float]) -> RetrievalPrediction:
        """Estimate one retrieval, with a reason a human can check."""
        minutes = self.predict_minutes(features)
        contributions, basis = self.explain(features)

        ranked = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
        strongest = [(name, value) for name, value in ranked[:3] if abs(value) > 0.05]
        if strongest:
            parts = []
            for name, value in strongest:
                label = FEATURE_LABELS.get(name, name)
                unit = feature_unit(name)
                raw = features[name]
                shown = f"{raw:.0f}{unit}" if unit else f"{raw:.2f}"
                direction = "adds" if value > 0 else "saves"
                parts.append(f"{label} {shown} {direction} {abs(value):.1f} min")
            detail = "; ".join(parts)
        else:
            detail = "no single factor dominated"

        reason = (
            f"Baseline linear model estimates {minutes:.0f} minutes. "
            f"{detail}. Basis: {basis}. "
            "Trained on synthetic data - an indicative planning number, not an "
            "operational forecast."
        )

        # Confidence is reported as how typical this input is, not as a
        # probability that the estimate is right -- the model has no calibrated
        # uncertainty and claiming one would be a fabrication. Inputs far from
        # the training distribution get a lower number.
        confidence = self._typicality(features)

        return RetrievalPrediction(
            estimated_minutes=minutes,
            confidence=confidence,
            reason=reason,
            contributions=contributions,
        )

    def _typicality(self, features: dict[str, float]) -> float:
        """How central this input is to what the model was trained on, 0..1.

        Deliberately not called a probability. A z-score distance from the
        training mean, mapped through a smooth decay, so an estimate made from
        an unusual queue length is visibly less trustworthy than a routine one.
        """
        self._require_fitted()
        vector = self._vector_from(features)
        z = np.abs((vector - self._mean) / self._scale)
        distance = float(np.sqrt(np.mean(z**2)))
        return round(1.0 / (1.0 + distance / 2.0), 3)

    # -- persistence --------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        """Write the model as plain JSON. No pickle, so loading executes nothing."""
        self._require_fitted()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "json-linear-retrieval-v1",
            "model_version": MODEL_VERSION,
            "method": METHOD_NAME,
            "algorithm": "ridge",
            "alpha": self.alpha,
            "feature_columns": list(self.feature_columns),
            "coefficients": [float(value) for value in np.asarray(self._estimator.coef_).ravel()],
            "intercept": float(np.asarray(self._estimator.intercept_).ravel()[0]),
            "scaler_mean": [float(value) for value in self._mean],
            "scaler_scale": [float(value) for value in self._scale],
            "train_median_minutes": self.train_median,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "training_summary": self.training_summary,
            "trained_at": self.trained_at,
            "trained_on_synthetic_data": True,
            "prototype_disclaimer": PROTOTYPE_DISCLAIMER,
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> RetrievalModel:
        """Read back a saved artifact, refusing anything malformed."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"no retrieval model artifact at {source}")
        payload = json.loads(source.read_text(encoding="utf-8"))

        if payload.get("format") != "json-linear-retrieval-v1":
            raise ValueError(
                f"unsupported artifact format {payload.get('format')!r}; this build reads "
                "'json-linear-retrieval-v1'"
            )

        columns = tuple(payload.get("feature_columns") or ())
        if columns != RETRIEVAL_FEATURE_COLUMNS:
            # Loading an artifact whose features do not match the code would
            # apply the right coefficients to the wrong columns and produce a
            # confident, meaningless number. Refuse instead.
            raise ValueError(
                "artifact was trained on a different feature set; retrain it. "
                f"artifact={list(columns)} code={list(RETRIEVAL_FEATURE_COLUMNS)}"
            )

        model = cls(alpha=float(payload.get("alpha", 1.0)))
        model._mean = np.array(payload["scaler_mean"], dtype=float)
        model._scale = np.array(payload["scaler_scale"], dtype=float)
        model.train_median = float(payload.get("train_median_minutes", 0.0))
        model.training_summary = payload.get("training_summary") or {}
        model.trained_at = payload.get("trained_at")

        from sklearn.linear_model import Ridge

        estimator = Ridge(alpha=model.alpha)
        estimator.coef_ = np.array(payload["coefficients"], dtype=float)
        estimator.intercept_ = float(payload["intercept"])
        model._estimator = estimator

        if payload.get("metrics"):
            metrics = payload["metrics"]
            model.metrics = RetrievalMetrics(
                rows=int(metrics.get("rows", 0)),
                mae=float(metrics.get("mae_minutes", 0.0)),
                median_absolute_error=float(metrics.get("median_absolute_error_minutes", 0.0)),
                rmse=float(metrics.get("rmse_minutes", 0.0)),
                mean_actual=float(metrics.get("mean_actual_minutes", 0.0)),
                mean_predicted=float(metrics.get("mean_predicted_minutes", 0.0)),
                baseline_mae=float(metrics.get("baseline_mae_minutes", 0.0)),
                mean_baseline_mae=float(metrics.get("mean_baseline_mae_minutes", 0.0)),
                r2=float(metrics.get("r2", 0.0)),
                worst_error_minutes=float(metrics.get("worst_error_minutes", 0.0)),
                within_5_minutes=float(metrics.get("within_5_minutes", 0.0)),
                within_10_minutes=float(metrics.get("within_10_minutes", 0.0)),
            )
        return model

    # -- introspection ------------------------------------------------------
    def feature_importance(self) -> list[tuple[str, float]]:
        """Coefficients by size, in minutes per standard deviation of the feature.

        Scaling first is what makes them comparable: a raw coefficient on
        ``zone_capacity`` (tens) is not comparable to one on ``vehicle_flagged``
        (0 or 1) until both are expressed per standard deviation.
        """
        self._require_fitted()
        coefficients = np.asarray(self._estimator.coef_).ravel()
        paired = [
            (name, float(coefficients[index]))
            for index, name in enumerate(self.feature_columns)
        ]
        return sorted(paired, key=lambda item: abs(item[1]), reverse=True)

    def describe(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "method": METHOD_NAME,
            "algorithm": "ridge",
            "alpha": self.alpha,
            "feature_columns": list(self.feature_columns),
            "coefficients": {
                name: round(value, 4) for name, value in self.feature_importance()
            },
            "intercept": round(float(np.asarray(self._estimator.intercept_).ravel()[0]), 4),
            "train_median_minutes": round(self.train_median, 2),
            "trained_at": self.trained_at,
            "training_summary": self.training_summary,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "trained_on_synthetic_data": True,
            "prototype_disclaimer": PROTOTYPE_DISCLAIMER,
        }


def round_minutes(value: float) -> int:
    """Round to whole minutes for display and storage.

    Rounds half up rather than to even, because an estimate shown as "8 minutes"
    that is really 8.5 should not quietly become 8 -- under-promising a wait is
    the direction that annoys a customer standing in a lobby.
    """
    return int(math.floor(value + 0.5))


__all__ = [
    "METHOD_NAME",
    "MODEL_VERSION",
    "PROTOTYPE_DISCLAIMER",
    "RetrievalMetrics",
    "RetrievalModel",
    "RetrievalPrediction",
    "mean_absolute_error",
    "round_minutes",
]
