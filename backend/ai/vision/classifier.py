"""Vehicle damage classification: feature extraction, baseline heuristic and ML model.

This module provides the machine-learning interface and feature extractor for
candidate vehicle damage regions detected by the vision pipeline.

Following the project's honesty mandates:
1. When no trained model artifact is found on disk, the system explicitly runs the
   calibrated baseline heuristic and records ``method='CLASSICAL_CV'``.
2. When a trained model artifact is present in ``storage/models/damage_classifier.json``,
   predictions carry ``method='ML_MODEL'`` and the exact ``model_version``.
3. The disclaimer clearly distinguishes model predictions on synthetic training data
   from real-world vehicle physical damage verification by humans.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ai.vision.compare import RegionCandidate
from ai.vision.config import PipelineConfig
from ai.vision.damage import TYPE_CONFIDENCE_CEILING
from models.damage_detection import DamageType, DetectionMethod

logger = logging.getLogger(__name__)

MODEL_VERSION = "damage-classifier/1.0.0"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "storage" / "models" / "damage_classifier.json"

FEATURE_NAMES = (
    "area_fraction",
    "aspect_ratio",
    "fill_ratio",
    "mean_difference",
    "peak_difference",
    "mean_chroma",
    "mean_edge",
    "mean_delta_l",
    "mean_abs_delta_l",
    "mean_pre_existing_edge",
    "bipolarity",
    "fragments",
)


def extract_features(candidate: RegionCandidate) -> dict[str, float]:
    """Extract a 12-dimensional feature dictionary from a candidate region."""
    denom = max(candidate.mean_abs_delta_l, 1e-4)
    bipolarity = float(np.clip(1.0 - abs(candidate.mean_delta_l) / denom, 0.0, 1.0))

    return {
        "area_fraction": float(candidate.area_fraction),
        "aspect_ratio": float(candidate.aspect_ratio),
        "fill_ratio": float(candidate.fill_ratio),
        "mean_difference": float(candidate.mean_difference),
        "peak_difference": float(candidate.peak_difference),
        "mean_chroma": float(candidate.mean_chroma),
        "mean_edge": float(candidate.mean_edge),
        "mean_delta_l": float(candidate.mean_delta_l),
        "mean_abs_delta_l": float(candidate.mean_abs_delta_l),
        "mean_pre_existing_edge": float(candidate.mean_pre_existing_edge),
        "bipolarity": bipolarity,
        "fragments": float(candidate.fragments),
    }


def feature_vector(candidate: RegionCandidate) -> list[float]:
    """Ordered feature vector matching ``FEATURE_NAMES``."""
    feat = extract_features(candidate)
    return [feat[name] for name in FEATURE_NAMES]


class DamageRegionFeatureExtractor:
    """Feature extractor wrapper for damage candidates."""

    @staticmethod
    def extract(candidate: RegionCandidate) -> dict[str, float]:
        return extract_features(candidate)

    @staticmethod
    def vector(candidate: RegionCandidate) -> list[float]:
        return feature_vector(candidate)


@dataclass
class ClassificationResult:
    """The result of classifying a candidate damage region."""

    damage_type: str
    confidence: float
    reasoning: str
    method: str
    model_version: str | None
    features: dict[str, float]


class DamageClassifier:
    """Damage region classifier supporting both ML inference and baseline heuristics."""

    def __init__(self, model_path: Path | str | None = None) -> None:
        self.model_path = Path(model_path or DEFAULT_MODEL_PATH)
        self.model_data: dict[str, Any] | None = None
        self._load_model()

    def _load_model(self) -> None:
        if not self.model_path.exists():
            self.model_data = None
            return
        try:
            with open(self.model_path, "r", encoding="utf-8") as f:
                self.model_data = json.load(f)
            logger.info("Loaded damage classifier model from %s", self.model_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load damage classifier from %s: %s", self.model_path, exc)
            self.model_data = None

    @property
    def is_ml_active(self) -> bool:
        return self.model_data is not None and "weights" in self.model_data

    def classify_region(
        self, candidate: RegionCandidate, *, config: PipelineConfig
    ) -> ClassificationResult:
        features = extract_features(candidate)

        # If trained ML weights are available, compute softmax scores
        if self.is_ml_active:
            try:
                weights = np.array(self.model_data["weights"], dtype=np.float32)  # shape: (n_classes, n_features)
                intercepts = np.array(self.model_data["intercepts"], dtype=np.float32)  # shape: (n_classes,)
                classes = self.model_data["classes"]
                means = np.array(self.model_data.get("scaler_mean", [0.0] * len(FEATURE_NAMES)), dtype=np.float32)
                scales = np.array(self.model_data.get("scaler_scale", [1.0] * len(FEATURE_NAMES)), dtype=np.float32)

                vec = np.array([features[name] for name in FEATURE_NAMES], dtype=np.float32)
                norm_vec = (vec - means) / np.maximum(scales, 1e-6)

                logits = weights @ norm_vec + intercepts
                exp_logits = np.exp(logits - np.max(logits))
                probs = exp_logits / np.sum(exp_logits)

                top_idx = int(np.argmax(probs))
                pred_label = classes[top_idx]
                pred_prob = float(probs[top_idx])
                capped_prob = min(round(float(pred_prob), 3), TYPE_CONFIDENCE_CEILING)

                reasoning = (
                    f"Classified as {pred_label} by ML classifier ({self.model_data.get('model_version')}) "
                    f"with confidence {capped_prob:.2f} based on shape, edge, and color features. "
                    "This is an automated reading on synthetic training data and needs human confirmation."
                )

                return ClassificationResult(
                    damage_type=pred_label,
                    confidence=capped_prob,
                    reasoning=reasoning,
                    method=str(DetectionMethod.ML_MODEL),
                    model_version=str(self.model_data.get("model_version", MODEL_VERSION)),
                    features=features,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ML classification failed; falling back to heuristic: %s", exc)

        # Baseline heuristic fallback
        return self._classify_baseline(candidate, features, config=config)

    def _classify_baseline(
        self,
        candidate: RegionCandidate,
        features: dict[str, float],
        *,
        config: PipelineConfig,
    ) -> ClassificationResult:
        from ai.vision.damage import classify as heuristic_classify

        label, confidence, reasoning = heuristic_classify(candidate, config=config)

        return ClassificationResult(
            damage_type=str(label.value if hasattr(label, "value") else label),
            confidence=confidence,
            reasoning=reasoning,
            method=str(DetectionMethod.CLASSICAL_CV),
            model_version=None,
            features=features,
        )


_DEFAULT_CLASSIFIER: DamageClassifier | None = None


def get_classifier() -> DamageClassifier:
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = DamageClassifier()
    return _DEFAULT_CLASSIFIER


__all__ = [
    "FEATURE_NAMES",
    "MODEL_VERSION",
    "ClassificationResult",
    "DamageClassifier",
    "extract_features",
    "feature_vector",
    "get_classifier",
]
