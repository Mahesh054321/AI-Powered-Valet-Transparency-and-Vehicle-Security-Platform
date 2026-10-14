"""Machine-learning layer for the security module.

A prototype classifier that sits *alongside* the rule engine, never instead of
it. See ``model.PROTOTYPE_DISCLAIMER`` and ``docs/AI-INTEGRITY.md``.

Nothing in this package imports Flask or SQLAlchemy, so the feature definitions
and the estimator can be exercised without a database.
"""

from __future__ import annotations

from ai.ml.dataset import CLASS_NORMAL, CLASS_SUSPICIOUS, CLASSES, Dataset, generate_dataset
from ai.ml.features import FEATURE_COLUMNS, FeatureVector, extract_features
from ai.ml.model import (
    ALGORITHMS,
    MODEL_VERSION,
    PROTOTYPE_DISCLAIMER,
    Metrics,
    Prediction,
    SecurityClassifier,
)

__all__ = [
    "ALGORITHMS",
    "CLASS_NORMAL",
    "CLASS_SUSPICIOUS",
    "CLASSES",
    "FEATURE_COLUMNS",
    "MODEL_VERSION",
    "PROTOTYPE_DISCLAIMER",
    "Dataset",
    "FeatureVector",
    "Metrics",
    "Prediction",
    "SecurityClassifier",
    "extract_features",
    "generate_dataset",
]
