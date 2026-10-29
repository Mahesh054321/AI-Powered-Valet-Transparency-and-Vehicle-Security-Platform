"""Tests for the ML damage classifier, feature extractor, and fallback heuristics."""
import os
import json
import pytest
import numpy as np

from ai.vision.classifier import (
    DamageClassifier,
    DamageRegionFeatureExtractor,
    ClassificationResult,
    get_classifier,
)
from ai.vision.compare import RegionCandidate
from ai.vision.config import PipelineConfig
from ai.vision.damage import TYPE_CONFIDENCE_CEILING, DamageType


def _make_candidate(
    *,
    aspect_ratio: float = 8.5,
    fill_ratio: float = 0.6,
    area_fraction: float = 0.005,
    mean_diff: float = 80.0,
    peak_diff: float = 120.0,
    mean_edge: float = 45.0,
    mean_chroma: float = 12.0,
    delta_l: float = -5.0,
    delta_a: float = 1.0,
    delta_b: float = 1.5,
    mean_pre_existing_edge: float = 5.0,
) -> RegionCandidate:
    return RegionCandidate(
        bbox=(100, 100, 170, 20),
        area=2000,
        area_fraction=area_fraction,
        mean_difference=mean_diff,
        peak_difference=peak_diff,
        mean_delta_l=delta_l,
        mean_abs_delta_l=abs(delta_l) + 10.0,
        mean_delta_a=delta_a,
        mean_delta_b=delta_b,
        mean_chroma=mean_chroma,
        mean_edge=mean_edge,
        mean_pre_existing_edge=mean_pre_existing_edge,
        fill_ratio=fill_ratio,
        aspect_ratio=aspect_ratio,
        fragments=1,
        frame_width=960,
        frame_height=640,
    )


def test_feature_extractor_returns_12_features():
    extractor = DamageRegionFeatureExtractor()
    candidate = _make_candidate()
    features = extractor.extract(candidate)
    assert len(features) == 12
    for key, val in features.items():
        assert isinstance(val, (int, float))
        assert not np.isnan(val)


def test_classifier_loads_trained_model():
    classifier = get_classifier()
    assert classifier.model_data is not None
    assert classifier.is_ml_active is True
    assert "model_version" in classifier.model_data


def test_classifier_fallback_when_model_missing(tmp_path):
    missing_path = str(tmp_path / "nonexistent.json")
    clf = DamageClassifier(model_path=missing_path)
    assert clf.model_data is None
    assert clf.is_ml_active is False

    cand = _make_candidate(aspect_ratio=10.0, mean_edge=50.0)
    config = PipelineConfig()
    res = clf.classify_region(cand, config=config)
    assert isinstance(res, ClassificationResult)
    assert res.damage_type == DamageType.SCRATCH
    assert res.method == "CLASSICAL_CV"
    assert res.confidence <= TYPE_CONFIDENCE_CEILING


def test_classifier_confidence_never_exceeds_ceiling():
    classifier = get_classifier()
    config = PipelineConfig()
    cand = _make_candidate()
    res = classifier.classify_region(cand, config=config)
    assert res.confidence <= TYPE_CONFIDENCE_CEILING
    assert "human confirmation" in res.reasoning or "human review" in res.reasoning
