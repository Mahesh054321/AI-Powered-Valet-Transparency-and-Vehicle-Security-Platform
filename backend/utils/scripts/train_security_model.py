"""Train the prototype security classifier on synthetic telemetry.

Usage (from the ``backend`` directory, with the virtual environment active):

    python utils/scripts/train_security_model.py
    python utils/scripts/train_security_model.py --algorithm random_forest
    python utils/scripts/train_security_model.py --episodes 2000 --seed 42
    python utils/scripts/train_security_model.py --output storage/models/security.json
                                                                     # (any path)

The artifact is written to ``storage/models/`` by default - generated output,
not source. It is regenerable from this script at any time, which is why it is
not committed: a checked-in model file would drift out of step with the code
that reads it, and nobody would notice.

**Read this before quoting any number this script prints.** The training data is
synthetic and its labels were written by hand in ``ai/ml/dataset.py``. A high
accuracy here means the model recovered a distinction that file drew; it does
not mean the model can identify suspicious vehicles. See
``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ai.ml.dataset import generate_dataset  # noqa: E402
from ai.ml.model import (  # noqa: E402
    ALGORITHM_LOGISTIC,
    ALGORITHMS,
    MODEL_VERSION,
    PROTOTYPE_DISCLAIMER,
    SecurityClassifier,
)

DEFAULT_OUTPUT = BACKEND_ROOT / "storage" / "models" / "security_classifier.json"


def _print_metrics(label: str, metrics) -> None:
    print(f"\n{label}")
    print("-" * len(label))
    print(f"  rows evaluated : {metrics.rows}")
    print(f"  accuracy       : {metrics.accuracy:.4f}")
    print(
        f"  precision      : {metrics.precision:.4f}   "
        "(of those flagged, how many were suspicious)"
    )
    print(f"  recall         : {metrics.recall:.4f}   (of those suspicious, how many were flagged)")
    print(f"  F1 score       : {metrics.f1:.4f}")
    print(
        f"  baseline       : {metrics.baseline_accuracy:.4f}   "
        "(a model that always answered NORMAL)"
    )
    matrix = metrics.confusion_matrix
    labels = metrics.labels
    print("\n  confusion matrix (rows: actual, columns: predicted)")
    print(f"    {'':<14}" + "".join(f"{name:>12}" for name in labels))
    for row_index, actual in enumerate(labels):
        cells = "".join(f"{value:>12}" for value in matrix[row_index])
        print(f"    {actual:<14}{cells}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--algorithm",
        choices=ALGORITHMS,
        default=ALGORITHM_LOGISTIC,
        help="logistic regression is the default: its coefficients make the "
        "reason for a prediction exact rather than approximate",
    )
    parser.add_argument("--episodes", type=int, default=1200, help="episodes to generate")
    parser.add_argument("--seed", type=int, default=20260908, help="dataset seed")
    parser.add_argument(
        "--test-fraction", type=float, default=0.25, help="fraction of episodes held out"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="where to write the trained artifact",
    )
    parser.add_argument(
        "--class-weight",
        default="balanced",
        help="'balanced' weights the rarer class more heavily; 'none' does not",
    )
    args = parser.parse_args(argv)

    print("=" * 72)
    print("  Security classifier - PROTOTYPE training run")
    print("=" * 72)
    print(f"  model version : {MODEL_VERSION}")
    print(f"  algorithm     : {args.algorithm}")
    print(f"  seed          : {args.seed}")
    print()
    print("  The training data is synthetic and its labels were written by hand.")
    print("  Metrics below describe how well this model recovers that hand-drawn")
    print("  distinction. They say nothing about real-world detectability.")

    dataset = generate_dataset(
        episodes=args.episodes, seed=args.seed, test_fraction=args.test_fraction
    )
    summary = dataset.summary()
    print(f"\n  dataset: {summary['rows']} fixes across {summary['episodes']} episodes")
    print(
        f"    train {summary['train_rows']} "
        f"({summary['train_normal']} NORMAL / {summary['train_suspicious']} SUSPICIOUS)"
    )
    print(
        f"    test  {summary['test_rows']} "
        f"({summary['test_normal']} NORMAL / {summary['test_suspicious']} SUSPICIOUS)"
    )
    print("    split by episode, so no vehicle's fixes straddle the boundary")

    model = SecurityClassifier(
        args.algorithm,
        class_weight=None if args.class_weight == "none" else args.class_weight,
    )
    model.fit(dataset)

    _print_metrics("Held-out test set", model.metrics)

    print("\nFeature influence (largest first)")
    print("-" * 32)
    for name, weight in model.feature_importance():
        print(f"  {name:<30}{weight:>8.4f}")

    written = model.save(args.output)
    print(f"\nArtifact written to {written}")
    fmt = "JSON (no pickle)" if args.algorithm == ALGORITHM_LOGISTIC else "joblib (pickle)"
    print(f"  format: {fmt}")

    print("\n" + "!" * 72)
    print(f"  {PROTOTYPE_DISCLAIMER}")
    print("!" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
