"""Evaluate a trained security classifier.

Usage (from the ``backend`` directory, with the virtual environment active):

    python utils/scripts/evaluate_security_model.py
    python utils/scripts/evaluate_security_model.py \
        --artifact storage/models/security_classifier.json
    python utils/scripts/evaluate_security_model.py --fresh-seed 999

By default this reports metrics on the held-out test split of the *same* seed
the model was trained on, which is the number the training script printed.

``--fresh-seed`` is the more interesting run: it generates a brand-new dataset
the model has never seen and scores against that. The two numbers together say
something the first one alone cannot - whether the model learned the behaviour,
or memorised one particular draw of it.

Both are still synthetic. Neither licenses a claim about real vehicles.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ai.ml.dataset import generate_dataset  # noqa: E402
from ai.ml.model import PROTOTYPE_DISCLAIMER, SecurityClassifier  # noqa: E402

DEFAULT_ARTIFACT = BACKEND_ROOT / "storage" / "models" / "security_classifier.json"


def _report(title: str, metrics) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"  rows       : {metrics.rows}")
    print(f"  accuracy   : {metrics.accuracy:.4f}")
    print(f"  precision  : {metrics.precision:.4f}")
    print(f"  recall     : {metrics.recall:.4f}")
    print(f"  F1 score   : {metrics.f1:.4f}")
    print(f"  baseline   : {metrics.baseline_accuracy:.4f}  (always answering NORMAL)")

    false_negatives = metrics.confusion_matrix[1][0]
    false_positives = metrics.confusion_matrix[0][1]
    print(f"\n  false negatives (missed suspicious fixes) : {false_negatives}")
    print(f"  false positives (normal fixes flagged)    : {false_positives}")
    print(
        "  A missed breach is the costlier error here, so recall matters more\n"
        "  than precision - but the rule engine still catches a boundary breach\n"
        "  on its own, which is why a miss is not a catastrophe."
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
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--episodes", type=int, default=1200)
    parser.add_argument(
        "--seed", type=int, default=20260908, help="seed matching the training run"
    )
    parser.add_argument(
        "--fresh-seed",
        type=int,
        default=None,
        help="also score against a newly generated dataset the model never saw",
    )
    parser.add_argument("--test-fraction", type=float, default=0.25)
    args = parser.parse_args(argv)

    print("=" * 72)
    print("  Security classifier - evaluation")
    print("=" * 72)

    try:
        model = SecurityClassifier.load(args.artifact)
    except FileNotFoundError:
        print(f"\nNo artifact at {args.artifact}.")
        print("Train one first:\n\n    python utils/scripts/train_security_model.py\n")
        return 1

    print(f"  artifact      : {args.artifact}")
    print(f"  model version : {model.describe()['model_version']}")
    print(f"  algorithm     : {model.algorithm}")
    print(f"  trained at    : {model.trained_at}")

    stored = model.training_summary
    if stored:
        print(
            f"  trained on    : {stored.get('rows', '?')} synthetic fixes across "
            f"{stored.get('episodes', '?')} episodes"
        )

    dataset = generate_dataset(
        episodes=args.episodes, seed=args.seed, test_fraction=args.test_fraction
    )
    test_X = dataset.X[dataset.test_mask]
    test_y = dataset.y[dataset.test_mask]
    _report(f"Held-out test split (seed {args.seed})", model.score(test_X, test_y))

    if args.fresh_seed is not None:
        fresh = generate_dataset(
            episodes=args.episodes,
            seed=args.fresh_seed,
            test_fraction=args.test_fraction,
        )
        fresh_X = fresh.X[fresh.test_mask]
        fresh_y = fresh.y[fresh.test_mask]
        _report(
            f"Unseen dataset (fresh seed {args.fresh_seed})",
            model.score(fresh_X, fresh_y),
        )
        print(
            "\n  Comparing the two: a large drop on the fresh seed means the model\n"
            "  fitted one particular draw of the synthetic process rather than the\n"
            "  behaviour it was meant to learn."
        )

    print("\nFeature influence (largest first)")
    print("-" * 32)
    for name, weight in model.feature_importance():
        print(f"  {name:<30}{weight:>8.4f}")

    print("\n" + "!" * 72)
    print(f"  {PROTOTYPE_DISCLAIMER}")
    print("!" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
