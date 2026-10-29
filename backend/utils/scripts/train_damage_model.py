"""Train the vehicle damage region classifier.

Usage (from backend root):
    python utils/scripts/train_damage_model.py

Exports model weights, scaler, and validation metrics to:
    backend/storage/models/damage_classifier.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from ai.vision.classifier import FEATURE_NAMES, MODEL_VERSION
from models.damage_detection import DamageType

OUTPUT_PATH = BACKEND_ROOT / "storage" / "models" / "damage_classifier.json"


def generate_synthetic_features(n_samples: int = 1200, seed: int = 42) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rng = np.random.default_rng(seed)
    classes = [
        DamageType.SCRATCH.value,
        DamageType.DENT.value,
        DamageType.PAINT_CHIP.value,
        DamageType.RUST.value,
        DamageType.OTHER.value,
    ]

    samples_per_class = n_samples // len(classes)
    X = []
    y = []

    for cls in classes:
        for _ in range(samples_per_class):
            if cls == DamageType.SCRATCH.value:
                # Elongated, high edge, low-moderate chroma
                feat = [
                    rng.uniform(0.001, 0.015),  # area_fraction
                    rng.uniform(3.5, 12.0),     # aspect_ratio
                    rng.uniform(0.1, 0.45),     # fill_ratio
                    rng.uniform(25.0, 75.0),    # mean_difference
                    rng.uniform(60.0, 160.0),   # peak_difference
                    rng.uniform(1.0, 10.0),     # mean_chroma
                    rng.uniform(40.0, 150.0),   # mean_edge
                    rng.uniform(-15.0, 20.0),   # mean_delta_l
                    rng.uniform(12.0, 45.0),    # mean_abs_delta_l
                    rng.uniform(5.0, 35.0),     # mean_pre_existing_edge
                    rng.uniform(0.1, 0.6),      # bipolarity
                    float(rng.integers(1, 4)),  # fragments
                ]
            elif cls == DamageType.DENT.value:
                # Blob-like, low chroma, low edge, high bipolarity, significant delta L
                feat = [
                    rng.uniform(0.003, 0.035),  # area_fraction
                    rng.uniform(1.0, 2.2),      # aspect_ratio
                    rng.uniform(0.5, 0.88),     # fill_ratio
                    rng.uniform(18.0, 50.0),    # mean_difference
                    rng.uniform(40.0, 110.0),   # peak_difference
                    rng.uniform(0.5, 6.0),      # mean_chroma
                    rng.uniform(2.0, 25.0),     # mean_edge
                    rng.uniform(-8.0, 8.0),     # mean_delta_l (close to 0)
                    rng.uniform(15.0, 55.0),    # mean_abs_delta_l (large)
                    rng.uniform(2.0, 25.0),     # mean_pre_existing_edge
                    rng.uniform(0.65, 0.98),    # bipolarity
                    float(rng.integers(1, 2)),  # fragments
                ]
            elif cls == DamageType.PAINT_CHIP.value:
                # Compact, sharp boundary, high chroma (primer/undercoat exposed)
                feat = [
                    rng.uniform(0.0008, 0.008), # area_fraction
                    rng.uniform(1.0, 2.4),      # aspect_ratio
                    rng.uniform(0.4, 0.8),      # fill_ratio
                    rng.uniform(35.0, 90.0),    # mean_difference
                    rng.uniform(80.0, 180.0),   # peak_difference
                    rng.uniform(16.0, 50.0),    # mean_chroma
                    rng.uniform(25.0, 85.0),    # mean_edge
                    rng.uniform(-30.0, 30.0),   # mean_delta_l
                    rng.uniform(25.0, 65.0),    # mean_abs_delta_l
                    rng.uniform(4.0, 30.0),     # mean_pre_existing_edge
                    rng.uniform(0.1, 0.5),      # bipolarity
                    float(rng.integers(1, 2)),  # fragments
                ]
            elif cls == DamageType.RUST.value:
                # Warm chroma shift (high a and b), irregular fill
                feat = [
                    rng.uniform(0.004, 0.04),   # area_fraction
                    rng.uniform(1.1, 3.2),      # aspect_ratio
                    rng.uniform(0.35, 0.75),    # fill_ratio
                    rng.uniform(25.0, 65.0),    # mean_difference
                    rng.uniform(50.0, 130.0),   # peak_difference
                    rng.uniform(22.0, 60.0),    # mean_chroma
                    rng.uniform(10.0, 45.0),    # mean_edge
                    rng.uniform(-20.0, 20.0),   # mean_delta_l
                    rng.uniform(18.0, 48.0),    # mean_abs_delta_l
                    rng.uniform(3.0, 25.0),     # mean_pre_existing_edge
                    rng.uniform(0.1, 0.5),      # bipolarity
                    float(rng.integers(1, 3)),  # fragments
                ]
            else:  # OTHER / DIRT_STAIN
                # Diffuse, low peak difference, low edge
                feat = [
                    rng.uniform(0.015, 0.08),   # area_fraction
                    rng.uniform(1.0, 4.0),      # aspect_ratio
                    rng.uniform(0.2, 0.6),      # fill_ratio
                    rng.uniform(14.0, 28.0),    # mean_difference
                    rng.uniform(25.0, 55.0),    # peak_difference
                    rng.uniform(3.0, 15.0),     # mean_chroma
                    rng.uniform(2.0, 18.0),     # mean_edge
                    rng.uniform(-10.0, 10.0),   # mean_delta_l
                    rng.uniform(8.0, 22.0),     # mean_abs_delta_l
                    rng.uniform(2.0, 20.0),     # mean_pre_existing_edge
                    rng.uniform(0.2, 0.6),      # bipolarity
                    float(rng.integers(1, 4)),  # fragments
                ]

            X.append(feat)
            y.append(cls)

    return np.array(X, dtype=np.float32), np.array(y), classes


def main() -> int:
    print("\n" + "=" * 70)
    print("  Training Prototype Vehicle Damage Classifier")
    print("=" * 70)

    X, y, classes = generate_synthetic_features(n_samples=2500, seed=20261018)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    clf = LogisticRegression(max_iter=1000, C=1.5, class_weight="balanced", random_state=42)
    clf.fit(X_train_scaled, y_train)

    y_pred = clf.predict(X_test_scaled)

    acc = float(clf.score(X_test_scaled, y_test))
    prec = float(precision_score(y_test, y_pred, average="weighted"))
    rec = float(recall_score(y_test, y_pred, average="weighted"))
    f1 = float(f1_score(y_test, y_pred, average="weighted"))

    print(f"  Test Accuracy : {acc:.4f}")
    print(f"  Precision     : {prec:.4f}")
    print(f"  Recall        : {rec:.4f}")
    print(f"  F1 Score      : {f1:.4f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))

    model_data = {
        "model_version": MODEL_VERSION,
        "algorithm": "multinomial_logistic_regression",
        "trained_at": datetime.now(tz=UTC).isoformat(),
        "classes": list(clf.classes_),
        "feature_names": list(FEATURE_NAMES),
        "weights": clf.coef_.tolist(),
        "intercepts": clf.intercept_.tolist(),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "metrics": {
            "accuracy": round(acc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "n_train": len(X_train),
            "n_test": len(X_test),
        },
        "trained_on_synthetic_data": True,
        "disclaimer": (
            "Model trained on synthetic vehicle damage feature vectors for demonstration. "
            "Real-world inspection findings remain UNVERIFIED until confirmed by a manager."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(model_data, f, indent=2)

    print(f"  Artifact saved -> {OUTPUT_PATH}")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
