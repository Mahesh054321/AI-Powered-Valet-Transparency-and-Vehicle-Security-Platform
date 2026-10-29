"""Train the baseline retrieval-time predictor.

Usage (from the ``backend`` directory, with the virtual environment active):

    python utils/scripts/train_retrieval_model.py
    python utils/scripts/train_retrieval_model.py --episodes 8000 --alpha 2.0
    python utils/scripts/train_retrieval_model.py --output storage/models/retrieval_model.json

Fits ridge regression on the earlier part of a synthetic history and scores the
later part, which it never saw. Writes a plain-JSON artifact -- coefficients,
intercept and scaler -- so loading it cannot execute anything and the numbers
can be read and diffed by hand.

**MAE is the headline metric, and it is always shown next to a baseline.** A
predictor that ignored every feature and answered the training median would also
score an MAE. If this model does not beat that number it is not worth keeping,
and the script says so rather than printing a figure that looks respectable on
its own.

The accuracy describes fit to a synthetic generator. See
``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ai.retrieval.dataset import generate_history, load_history  # noqa: E402
from ai.retrieval.model import MODEL_VERSION, PROTOTYPE_DISCLAIMER, RetrievalModel  # noqa: E402

DEFAULT_OUTPUT = BACKEND_ROOT / "storage" / "models" / "retrieval_model.json"


def _print_metrics(title: str, metrics) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"  rows evaluated            : {metrics.rows}")
    print(f"  MAE (primary metric)      : {metrics.mae:.2f} minutes")
    print(f"  median absolute error     : {metrics.median_absolute_error:.2f} minutes")
    print(f"  RMSE                      : {metrics.rmse:.2f} minutes")
    print(f"  R-squared                 : {metrics.r2:.4f}")
    print(f"  worst single error        : {metrics.worst_error_minutes:.1f} minutes")
    print(f"  within 5 minutes          : {metrics.within_5_minutes:.1%}")
    print(f"  within 10 minutes         : {metrics.within_10_minutes:.1%}")
    print(f"  mean actual / predicted   : {metrics.mean_actual:.2f} / {metrics.mean_predicted:.2f}")
    print()
    print(f"  baseline MAE (median)     : {metrics.baseline_mae:.2f} minutes")
    print(f"  baseline MAE (mean)       : {metrics.mean_baseline_mae:.2f} minutes")
    change = metrics.improvement_over_baseline
    if change > 0:
        print(f"  improvement over baseline : {change:.1%} lower MAE")
    elif change == 0:
        print("  improvement over baseline : none - no better than the median")
    else:
        print(
            f"  improvement over baseline : {abs(change):.1%} WORSE than the median. "
            "Do not ship this model."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--episodes", type=int, default=4000, help="history rows to generate")
    parser.add_argument("--seed", type=int, default=20260909, help="generator seed")
    parser.add_argument("--span-days", type=int, default=90)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--alpha", type=float, default=1.0, help="ridge regularisation")
    parser.add_argument(
        "--history",
        type=Path,
        default=None,
        help="train on a history file from generate_retrieval_history.py instead "
        "of regenerating from the seed",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    print("=" * 74)
    print("  Retrieval-time predictor - BASELINE training run")
    print("=" * 74)
    print(f"  model version : {MODEL_VERSION}")
    print(f"  algorithm     : ridge regression (alpha={args.alpha})")
    print(f"  seed          : {args.seed}")
    print()
    print("  The training history is synthetic and was generated for this MVP.")
    print("  The MAE below measures fit to that generator, not predictive power")
    print("  in a real valet operation.")

    if args.history:
        dataset = load_history(args.history)
        print(f"\n  history source: {args.history} (loaded, not regenerated)")
    else:
        dataset = generate_history(
            episodes=args.episodes,
            seed=args.seed,
            span_days=args.span_days,
            test_fraction=args.test_fraction,
        )
        print(f"\n  history source: regenerated from seed {args.seed}")
    summary = dataset.summary()
    print(f"  history: {summary['rows']} rows")
    print(f"    train {summary['train_rows']} (earlier period)")
    print(f"    test  {summary['test_rows']} (later period, never seen in training)")
    print(
        f"    actual retrieval time {summary['min_minutes']:.1f}"
        f"-{summary['max_minutes']:.1f} minutes"
    )

    model = RetrievalModel(alpha=args.alpha).fit(dataset)
    _print_metrics("Held-out test period", model.metrics)

    print("\nCoefficients (minutes of effect per standard deviation of the feature)")
    print("-" * 70)
    for name, value in model.feature_importance():
        print(f"  {name:<32}{value:+8.3f}")
    print(f"  {'intercept':<32}"
          f"{model.describe()['intercept']:+8.3f}")

    print("\nWorked examples")
    print("-" * 40)
    from datetime import datetime

    from ai.retrieval.features import RetrievalContext, extract_features

    for label, ctx in (
        (
            "empty queue, 2pm weekday",
            RetrievalContext(
                queue_size=1, queue_position=1, active_valets=6, zone_capacity=20,
                zone_occupancy=8, requested_at=datetime(2026, 9, 9, 14, 0),
                historical_zone_mean_minutes=7.0,
            ),
        ),
        (
            "4th in queue, 6pm rush",
            RetrievalContext(
                queue_size=6, queue_position=4, active_valets=4, zone_capacity=30,
                zone_occupancy=27, requested_at=datetime(2026, 9, 9, 18, 0),
                historical_zone_mean_minutes=9.0,
            ),
        ),
        (
            "2nd in queue, flagged car, 1am",
            RetrievalContext(
                queue_size=3, queue_position=2, active_valets=2, zone_capacity=45,
                zone_occupancy=12, requested_at=datetime(2026, 9, 9, 1, 0),
                historical_zone_mean_minutes=11.0, vehicle_flagged=True,
            ),
        ),
    ):
        prediction = model.predict(extract_features(ctx))
        print(f"  {label:<34}-> {prediction.estimated_minutes:5.1f} min "
              f"(confidence {prediction.confidence:.2f})")

    written = model.save(args.output)
    size_kb = written.stat().st_size / 1024.0
    print(f"\nArtifact written to {written} ({size_kb:.1f} kB, plain JSON, no pickle)")

    print("\n" + "!" * 74)
    print(f"  {PROTOTYPE_DISCLAIMER}")
    print("!" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
