"""Training, evaluation and persistence for the security classifier.

Two algorithms are supported, and the choice is not cosmetic:

* **``logistic``** (the default). Its coefficients are directly interpretable,
  so a prediction's reason can state *which features pushed it and by how much*
  - an exact decomposition of the log-odds, not a post-hoc story. The artifact
  is plain JSON, which means no pickle, no `joblib`, and nothing that executes
  code when it is loaded.
* **``random_forest``**. Usually more accurate on this kind of data, and it
  reports feature importances. Those importances are *global*, so the reason it
  produces is weaker: it can say which features the forest relies on in general,
  not how they moved this particular prediction. Its artifact is a pickle and
  must only ever be loaded from a path this project wrote.

Both are wrapped in a `StandardScaler`, because distance is measured in metres
and direction in degrees; an unscaled linear model would let metres dominate.

**Nothing here should be described as a production model.** It is fitted on
synthetic telemetry whose labels were written by hand - see
``ai/ml/dataset.py`` and ``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ai.ml.dataset import CLASS_NORMAL, CLASS_SUSPICIOUS, CLASSES, Dataset
from ai.ml.features import FEATURE_COLUMNS, FEATURE_LABELS, feature_unit

ALGORITHM_LOGISTIC = "logistic"
ALGORITHM_RANDOM_FOREST = "random_forest"
ALGORITHMS = (ALGORITHM_LOGISTIC, ALGORITHM_RANDOM_FOREST)

#: Every artifact carries this. It travels with the model rather than living
#: only in the documentation, so a consumer who never reads the README still
#: cannot miss it.
PROTOTYPE_DISCLAIMER = (
    "Prototype classifier trained and evaluated on synthetic telemetry whose "
    "labels were written by hand. It has never seen a real vehicle and must not "
    "be represented as a production model or relied on as the sole basis for a "
    "security decision. Rule-based geofence checks remain authoritative."
)

#: Bump when the feature set, the algorithm or the training data changes. It is
#: stored in the artifact and surfaced on every prediction, so a stale model
#: serving a new feature set is detectable rather than silent.
MODEL_VERSION = "security-prototype/1.0.0"


@dataclass(frozen=True)
class Metrics:
    """Classification metrics, reported for the class that matters.

    ``SUSPICIOUS`` is the positive class. A geofence system that quietly never
    flags anything scores 100% accuracy on a rare-event dataset, so accuracy
    alone is close to meaningless and is reported alongside the rest rather
    than instead of it.
    """

    accuracy: float
    precision: float
    recall: float
    f1: float
    confusion_matrix: list[list[int]]
    labels: tuple[str, ...] = CLASSES
    rows: int = 0
    baseline_accuracy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "confusion_matrix": {
                "labels": list(self.labels),
                "matrix": self.confusion_matrix,
                "reading": "rows are the true label, columns the predicted label",
            },
            "rows": self.rows,
            # What a model that always answered NORMAL would have scored. Every
            # number above should be read against this one.
            "baseline_accuracy_always_normal": round(self.baseline_accuracy, 4),
        }


@dataclass
class Prediction:
    """A classification with the evidence behind it."""

    classification: str
    probability: float
    reason: str
    contributions: dict[str, float] = field(default_factory=dict)
    model_version: str = MODEL_VERSION
    algorithm: str = ALGORITHM_LOGISTIC
    trained_on_synthetic_data: bool = True
    disclaimer: str = PROTOTYPE_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "probability": round(self.probability, 4),
            "confidence": round(self.probability, 4),
            "reason": self.reason,
            "contributions": {
                key: round(value, 4) for key, value in self.contributions.items()
            },
            "model_version": self.model_version,
            "algorithm": self.algorithm,
            "trained_on_synthetic_data": self.trained_on_synthetic_data,
            "disclaimer": self.disclaimer,
        }


class SecurityClassifier:
    """A thin, explicit wrapper around a scikit-learn estimator.

    Deliberately not a general-purpose ML framework: one problem, two
    algorithms, and every method short enough to audit.
    """

    def __init__(
        self,
        algorithm: str = ALGORITHM_LOGISTIC,
        *,
        class_weight: str | dict | None = "balanced",
        random_state: int = 0,
    ) -> None:
        if algorithm not in ALGORITHMS:
            raise ValueError(
                f"algorithm must be one of {', '.join(ALGORITHMS)}; got {algorithm!r}"
            )
        self.algorithm = algorithm
        self.class_weight = class_weight
        self.random_state = random_state
        self.feature_columns: tuple[str, ...] = FEATURE_COLUMNS
        self._mean: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        self._estimator: Any = None
        self.metrics: Metrics | None = None
        self.training_summary: dict[str, Any] = {}
        self.trained_at: str | None = None

    # -- training ----------------------------------------------------------
    def _build_estimator(self):
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression

        if self.algorithm == ALGORITHM_LOGISTIC:
            # max_iter raised: the default 100 can stop short on scaled features
            # and return a model that has not actually converged, which would
            # quietly degrade every metric reported from it.
            return LogisticRegression(
                class_weight=self.class_weight,
                max_iter=2000,
                random_state=self.random_state,
            )
        return RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            class_weight=self.class_weight,
            random_state=self.random_state,
            n_jobs=-1,
        )

    def _validate_columns(self, X: np.ndarray) -> None:
        if X.ndim != 2 or X.shape[1] != len(self.feature_columns):
            raise ValueError(
                f"expected a (n, {len(self.feature_columns)}) matrix matching "
                f"{list(self.feature_columns)}; got shape {X.shape}"
            )

    def fit(self, dataset: Dataset) -> Metrics:
        """Fit on the dataset's training split and evaluate on its test split."""
        train_X = dataset.X[dataset.train_mask]
        train_y = dataset.y[dataset.train_mask]
        test_X = dataset.X[dataset.test_mask]
        test_y = dataset.y[dataset.test_mask]
        self._validate_columns(train_X)
        if train_X.shape[0] == 0 or test_X.shape[0] == 0:
            raise ValueError("the dataset has an empty train or test split")

        self._mean = train_X.mean(axis=0)
        # A constant feature has zero variance; dividing by it would produce
        # inf/NaN and poison every later prediction. Treat it as unscaled.
        scale = train_X.std(axis=0)
        self._scale = np.where(scale > 1e-9, scale, 1.0)

        self._estimator = self._build_estimator()
        self._estimator.fit(self._scale_features(train_X), train_y)

        self.trained_at = datetime.now(tz=UTC).isoformat()
        self.training_summary = {
            "algorithm": self.algorithm,
            "class_weight": str(self.class_weight),
            **dataset.summary(),
        }
        self.metrics = self.score(test_X, test_y)
        return self.metrics

    # -- evaluation --------------------------------------------------------
    def score(self, X: np.ndarray, y: np.ndarray) -> Metrics:
        """Accuracy, precision, recall, F1 and the confusion matrix.

        Computed with scikit-learn rather than by hand: these are standard
        definitions and re-deriving them invites an off-by-one that would make
        the reported numbers wrong in a way nobody would notice.
        """
        from sklearn.metrics import (
            accuracy_score,
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
        )

        self._require_fitted()
        self._validate_columns(X)
        predicted = self._estimator.predict(self._scale_features(X))

        # pos_label=1 is the SUSPICIOUS class. zero_division=0 keeps a model
        # that never predicts the positive class at 0 rather than NaN.
        majority = float(np.mean(y == 0))
        return Metrics(
            accuracy=float(accuracy_score(y, predicted)),
            precision=float(
                precision_score(y, predicted, pos_label=1, zero_division=0)
            ),
            recall=float(recall_score(y, predicted, pos_label=1, zero_division=0)),
            f1=float(f1_score(y, predicted, pos_label=1, zero_division=0)),
            confusion_matrix=confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
            labels=CLASSES,
            rows=int(X.shape[0]),
            baseline_accuracy=majority,
        )

    # -- prediction --------------------------------------------------------
    def _scale_features(self, X: np.ndarray) -> np.ndarray:
        self._require_fitted()
        return (X - self._mean) / self._scale

    def _require_fitted(self) -> None:
        if self._estimator is None or self._mean is None or self._scale is None:
            raise RuntimeError(
                "this classifier has not been fitted; call fit() or load() first"
            )

    def predict_probability(self, features: dict[str, float]) -> float:
        """P(SUSPICIOUS) for one feature dict."""
        self._require_fitted()
        vector = np.array(
            [[float(features[column]) for column in self.feature_columns]], dtype=float
        )
        probabilities = self._estimator.predict_proba(self._scale_features(vector))
        classes = list(self._estimator.classes_)
        if 1 not in classes:
            return 0.0
        return float(probabilities[0][classes.index(1)])

    def explain(self, features: dict[str, float]) -> tuple[str, dict[str, float], str]:
        """A reason string, per-feature contributions, and how exact they are.

        For logistic regression the contributions are exact: coefficient times
        scaled value, which sum with the intercept to the log-odds. For a random
        forest they are an approximation built from the global importances, and
        the returned reason says so rather than implying a precision it does
        not have.
        """
        self._require_fitted()
        vector = np.array(
            [float(features[column]) for column in self.feature_columns], dtype=float
        )
        scaled = (vector - self._mean) / self._scale

        if self.algorithm == ALGORITHM_LOGISTIC:
            coefficients = np.asarray(self._estimator.coef_).ravel()
            contributions = {
                column: float(coefficients[index] * scaled[index])
                for index, column in enumerate(self.feature_columns)
            }
            basis = "exact log-odds contributions"
        else:
            importances = np.asarray(self._estimator.feature_importances_).ravel()
            contributions = {
                column: float(importances[index] * abs(scaled[index]))
                for index, column in enumerate(self.feature_columns)
            }
            basis = "global feature importances, so indicative rather than exact"

        ranked = sorted(
            contributions.items(), key=lambda item: abs(item[1]), reverse=True
        )
        strongest = [key for key, _ in ranked[:3] if abs(contributions[key]) > 1e-6]
        if not strongest:
            detail = "no feature contributed meaningfully"
        else:
            parts = []
            for key in strongest:
                label = FEATURE_LABELS.get(key, key)
                unit = feature_unit(key)
                value = features[key]
                parts.append(f"{label} ({value:.1f}{unit})")
            detail = ", ".join(parts)
        return detail, contributions, basis

    def predict(self, features: dict[str, float]) -> Prediction:
        """Classify one feature dict, with the reason and its provenance."""
        missing = [column for column in self.feature_columns if column not in features]
        if missing:
            raise ValueError(
                f"missing features: {', '.join(missing)}; the model was trained on "
                f"{list(self.feature_columns)}"
            )
        probability = self.predict_probability(features)
        classification = CLASS_SUSPICIOUS if probability >= 0.5 else CLASS_NORMAL
        detail, contributions, basis = self.explain(features)
        reason = (
            f"Prototype model ({self.algorithm}) estimates P(suspicious) = "
            f"{probability:.2f}. Largest contributors: {detail}. Basis: {basis}. "
            "Trained on synthetic data - not a production model."
        )
        return Prediction(
            classification=classification,
            probability=probability,
            reason=reason,
            contributions=contributions,
            algorithm=self.algorithm,
        )

    # -- persistence -------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        """Write the artifact. JSON for logistic regression, joblib for a forest."""
        self._require_fitted()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        metadata = {
            "model_version": MODEL_VERSION,
            "algorithm": self.algorithm,
            "feature_columns": list(self.feature_columns),
            "classes": list(CLASSES),
            "trained_at": self.trained_at,
            "training_summary": self.training_summary,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "trained_on_synthetic_data": True,
            "prototype_disclaimer": PROTOTYPE_DISCLAIMER,
        }

        if self.algorithm == ALGORITHM_LOGISTIC:
            payload = {
                **metadata,
                "format": "json-logistic-v1",
                # The estimator's own integer labels. `metadata["classes"]` holds
                # the human-readable names for display; conflating the two made
                # load() try to build an int array from "NORMAL".
                "estimator_classes": np.asarray(self._estimator.classes_).tolist(),
                "scaler": {
                    "mean": self._mean.tolist(),
                    "scale": self._scale.tolist(),
                },
                "coefficients": np.asarray(self._estimator.coef_).ravel().tolist(),
                "intercept": float(np.asarray(self._estimator.intercept_).ravel()[0]),
            }
            destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            import joblib

            # A pickle. Loading it executes code from the file, so an artifact
            # must only ever come from a path this project wrote - never from
            # a request body, an upload, or an untrusted share.
            joblib.dump(
                {
                    "metadata": metadata,
                    "scaler": {"mean": self._mean, "scale": self._scale},
                    "estimator": self._estimator,
                },
                destination,
            )
        return destination

    @classmethod
    def load(cls, path: str | Path) -> SecurityClassifier:
        """Read an artifact this project wrote, validating what it claims."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"no model artifact at {source}")

        if source.suffix == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload.get("format") != "json-logistic-v1":
                raise ValueError(
                    f"unrecognised artifact format {payload.get('format')!r}"
                )
            model = cls(algorithm=payload["algorithm"])
            model.feature_columns = tuple(payload["feature_columns"])
            model._mean = np.array(payload["scaler"]["mean"], dtype=float)
            model._scale = np.array(payload["scaler"]["scale"], dtype=float)
            model.trained_at = payload.get("trained_at")
            model.training_summary = payload.get("training_summary", {})
            metrics = payload.get("metrics")
            if metrics:
                model.metrics = Metrics(
                    accuracy=metrics["accuracy"],
                    precision=metrics["precision"],
                    recall=metrics["recall"],
                    f1=metrics["f1"],
                    confusion_matrix=metrics["confusion_matrix"]["matrix"],
                    rows=metrics.get("rows", 0),
                    baseline_accuracy=metrics.get("baseline_accuracy_always_normal", 0.0),
                )
            model._load_logistic(
                payload["coefficients"],
                payload["intercept"],
                payload["estimator_classes"],
            )
            return model

        import joblib

        bundle = joblib.load(source)
        metadata = bundle["metadata"]
        model = cls(algorithm=metadata["algorithm"])
        model.feature_columns = tuple(metadata["feature_columns"])
        model._mean = np.asarray(bundle["scaler"]["mean"], dtype=float)
        model._scale = np.asarray(bundle["scaler"]["scale"], dtype=float)
        model._estimator = bundle["estimator"]
        model.trained_at = metadata.get("trained_at")
        model.training_summary = metadata.get("training_summary", {})
        return model

    def _load_logistic(
        self, coefficients: list[float], intercept: float, classes: list[int]
    ) -> None:
        """Rebuild a LogisticRegression from stored weights without refitting."""
        from sklearn.linear_model import LogisticRegression

        estimator = LogisticRegression()
        estimator.classes_ = np.array(classes, dtype=int)
        estimator.coef_ = np.array([coefficients], dtype=float)
        estimator.intercept_ = np.array([intercept], dtype=float)
        estimator.n_features_in_ = len(coefficients)
        self._estimator = estimator

    # -- introspection -----------------------------------------------------
    def feature_importance(self) -> list[tuple[str, float]]:
        """Coefficients (logistic) or importances (forest), largest first."""
        self._require_fitted()
        if self.algorithm == ALGORITHM_LOGISTIC:
            weights = np.abs(np.asarray(self._estimator.coef_).ravel())
        else:
            weights = np.asarray(self._estimator.feature_importances_).ravel()
        ranked = sorted(
            zip(self.feature_columns, weights.tolist(), strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return [(name, round(float(value), 4)) for name, value in ranked]

    def describe(self) -> dict[str, Any]:
        """Everything a reviewer needs to judge this model, in one dict."""
        return {
            "model_version": MODEL_VERSION,
            "algorithm": self.algorithm,
            "feature_columns": list(self.feature_columns),
            "trained_at": self.trained_at,
            "training_summary": self.training_summary,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "feature_importance": self.feature_importance(),
            "trained_on_synthetic_data": True,
            "prototype_disclaimer": PROTOTYPE_DISCLAIMER,
        }


def sigmoid(log_odds: float) -> float:
    """Exposed for tests that reconstruct a probability from contributions."""
    return 1.0 / (1.0 + math.exp(-log_odds))


__all__ = [
    "ALGORITHMS",
    "ALGORITHM_LOGISTIC",
    "ALGORITHM_RANDOM_FOREST",
    "MODEL_VERSION",
    "PROTOTYPE_DISCLAIMER",
    "Metrics",
    "Prediction",
    "SecurityClassifier",
    "sigmoid",
]
