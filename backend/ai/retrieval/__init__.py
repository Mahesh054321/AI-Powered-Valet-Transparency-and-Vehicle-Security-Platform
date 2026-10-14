"""Retrieval-time prediction (Phase 9b).

Predicts how long it will take to bring a vehicle to the handover point after a
customer asks for it, so the customer can be given an honest wait and a valet
can be dispatched at the right moment rather than either too early or too late.

    features.py   the eleven inputs, each one a real column or a live count
    dataset.py    synthetic labelled history, split by time
    model.py      ridge regression, MAE-scored against a baseline it must beat

**This is a baseline trained on synthetic data.** The generator in
``dataset.py`` defines the relationship the model recovers, so the MAE reported
by the evaluation script measures fit to that generator, not predictive power
in a real yard. The disclaimer travels in the saved artifact and in every
estimate the API returns. See ``docs/AI-INTEGRITY.md``.

Like the rest of ``ai/*``, this package imports neither Flask nor SQLAlchemy.
``services/retrieval.py`` owns the database reads and calls in here with plain
numbers.
"""

from ai.retrieval.features import (
    FEATURE_LABELS,
    RETRIEVAL_FEATURE_COLUMNS,
    RetrievalContext,
    extract_features,
)
from ai.retrieval.model import (
    METHOD_NAME,
    MODEL_VERSION,
    PROTOTYPE_DISCLAIMER,
    RetrievalMetrics,
    RetrievalModel,
    RetrievalPrediction,
    mean_absolute_error,
    round_minutes,
)

__all__ = [
    "FEATURE_LABELS",
    "METHOD_NAME",
    "MODEL_VERSION",
    "PROTOTYPE_DISCLAIMER",
    "RETRIEVAL_FEATURE_COLUMNS",
    "RetrievalContext",
    "RetrievalMetrics",
    "RetrievalModel",
    "RetrievalPrediction",
    "extract_features",
    "mean_absolute_error",
    "round_minutes",
]
