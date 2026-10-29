"""Evaluate the saved retrieval model against actual retrieval times.

Usage (from the ``backend`` directory, with the virtual environment active):

    python utils/scripts/evaluate_retrieval_model.py
    python utils/scripts/evaluate_retrieval_model.py --episodes 6000 --fresh-seed 7
    python utils/scripts/evaluate_retrieval_model.py --model storage/models/retrieval_model.json

**Mean Absolute Error is the primary metric**, as the brief specifies. Every
number here is produced by comparing what the model predicted against the actual
retrieval time recorded for each episode, on a time period the model never saw.

Two things this script does that a bare MAE does not:

**It reports the baselines alongside.** Predicting the training median, or the
training mean, or a naive "3.2 minutes per vehicle in the queue" rule all score
an MAE too. The model is only worth keeping if it beats the best of them, and
the verdict at the end says so in words.

**It breaks the error down.** A single MAE hides whether the model is uniformly
mediocre or badly wrong for one situation. Zone, queue depth and time of day are
each scored separately, because those are exactly the cases a valet manager will
ask about.

Exits 1 when the model fails to beat the median baseline, so a regression that
makes the predictor useless cannot slip through unnoticed.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402

from ai.retrieval.dataset import QUEUE_SERVICE_MINUTES, generate_history  # noqa: E402
from ai.retrieval.features import RETRIEVAL_FEATURE_COLUMNS  # noqa: E402
from ai.retrieval.model import RetrievalModel, mean_absolute_error  # noqa: E402

DEFAULT_MODEL = BACKEND_ROOT / "storage" / "models" / "retrieval_model.json"


def _naive_queue_rule(X: np.ndarray, columns: tuple[str, ...]) -> np.ndarray:
    """A rule a manager could write by hand: a flat service time per place in
    queue, plus a short walk. Deliberately simple -- if a learned model cannot
    beat arithmetic this obvious, it is not earning its complexity."""
    position = X[:, columns.index("queue_position")]
    return np.maximum(position * QUEUE_SERVICE_MINUTES + 2.4, 1.0)


def _print_headline(metrics) -> None:
    print("\n  PRIMARY METRIC")
    print("  --------------")
    print(f"  Mean Absolute Error        : {metrics.mae:.3f} minutes")
    print(f"  Median Absolute Error      : {metrics.median_absolute_error:.3f} minutes")
    print(f"  RMSE                       : {metrics.rmse:.3f} minutes")
    print(f"  R-squared                  : {metrics.r2:.4f}")
    print(f"  Worst single error         : {metrics.worst_error_minutes:.2f} minutes")
    print(f"  Within 5 minutes of actual : {metrics.within_5_minutes:.1%}")
    print(f"  Within 10 minutes          : {metrics.within_10_minutes:.1%}")
    print(f"  Evaluated on               : {metrics.rows} held-out retrievals")
    print(f"  Mean actual / predicted    : {metrics.mean_actual:.2f} / "
          f"{metrics.mean_predicted:.2f} minutes")


def _print_baselines(metrics, naive_mae: float) -> None:
    rows = [
        ("median predictor (ignores all features)", metrics.baseline_mae),
        ("mean predictor (ignores all features)", metrics.mean_baseline_mae),
        (f"naive rule ({QUEUE_SERVICE_MINUTES:.1f} min per queue place + walk)", naive_mae),
        ("this model", metrics.mae),
    ]
    best_baseline = min(metrics.baseline_mae, metrics.mean_baseline_mae, naive_mae)

    print("\n  AGAINST THE BASELINES")
    print("  ---------------------")
    print(f"  {'predictor':<48}{'MAE (min)':>11}")
    print(f"  {'-' * 48}{'-' * 11}")
    for label, value in rows:
        marker = "  <-- best" if value == best_baseline and label != "this model" else ""
        if label == "this model":
            marker = "  <-- this model"
        print(f"  {label:<48}{value:>11.3f}{marker}")
    print(f"\n  Best baseline MAE : {best_baseline:.3f} minutes")
    print(f"  Model MAE         : {metrics.mae:.3f} minutes")
    delta = best_baseline - metrics.mae
    if delta > 0:
        print(f"  Verdict           : model is {delta:.3f} min better "
              f"({delta / best_baseline:.1%} lower MAE than the best baseline)")
    elif delta == 0:
        print("  Verdict           : model matches the best baseline - it adds nothing")
    else:
        print(f"  Verdict           : model is {abs(delta):.3f} min WORSE than a "
              "hand-written rule. Do not ship it.")


def _print_breakdown(dataset, model, test_mask: np.ndarray) -> None:
    episodes = [episode for episode, keep in zip(dataset.episodes, test_mask, strict=True) if keep]
    X = dataset.X[test_mask]
    predicted = model.predict_array(X)

    buckets: dict[str, dict[str, list[float]]] = {
        "by parking zone": defaultdict(list),
        "by queue position": defaultdict(list),
        "by time of day": defaultdict(list),
        "by vehicle status": defaultdict(list),
    }

    def _period(hour: int) -> str:
        if 0 <= hour < 6:
            return "00-05 night"
        if 6 <= hour < 11:
            return "06-10 morning"
        if 11 <= hour < 16:
            return "11-15 afternoon"
        if 16 <= hour < 21:
            return "16-20 evening rush"
        return "21-23 late"

    for index, episode in enumerate(episodes):
        row = episode.as_row()
        error = float(abs(row["actual_retrieval_minutes"] - predicted[index]))
        buckets["by parking zone"][f"zone {row['zone_index']}"].append(error)
        buckets["by queue position"][f"position {int(row['queue_position'])}"].append(error)
        # ``episode.requested_at`` is the datetime; ``row["requested_at"]`` is
        # its ISO string form, so the hour has to come from the episode.
        buckets["by time of day"][_period(episode.requested_at.hour)].append(error)
        status = "flagged" if row["vehicle_flagged"] else "clear"
        buckets["by vehicle status"][status].append(error)

    def _natural(label: str):
        # Sort "position 2" before "position 11"; a plain string sort does not,
        # and a table whose rows are out of order invites misreading.
        digits = "".join(ch for ch in label if ch.isdigit())
        return (int(digits) if digits else 0, label)

    print("\n  MAE BROKEN DOWN")
    print("  ---------------")
    for title, groups in buckets.items():
        print(f"\n  {title}:")
        for label in sorted(groups, key=_natural):
            errors = groups[label]
            # The mean of a list of absolute errors IS the MAE for that segment.
            mae = float(np.mean(errors))
            print(f"    {label:<26}{mae:.3f} min   (n={len(errors)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--episodes", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20260909, help="seed used for training")
    parser.add_argument(
        "--fresh-seed",
        type=int,
        default=None,
        help="evaluate on a differently-generated history as well",
    )
    parser.add_argument("--span-days", type=int, default=90)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    args = parser.parse_args(argv)

    print("=" * 74)
    print("  Retrieval model evaluation - MAE against ACTUAL retrieval times")
    print("=" * 74)
    print(f"  artifact: {args.model}")

    if not args.model.exists():
        print(f"\n  No artifact at {args.model}.")
        print("  Train one first:")
        print("      python utils/scripts/train_retrieval_model.py")
        return 1

    model = RetrievalModel.load(args.model)
    described = model.describe()
    print(f"  version : {described['model_version']}")
    print(f"  trained : {described['trained_at']}")
    print(f"  trained on synthetic data: {described['trained_on_synthetic_data']}")

    dataset = generate_history(
        episodes=args.episodes,
        seed=args.seed,
        span_days=args.span_days,
        test_fraction=args.test_fraction,
    )
    test_mask = dataset.test_mask
    X_test, y_test = dataset.X[test_mask], dataset.y[test_mask]
    metrics = model.score(X_test, y_test)
    naive_mae = mean_absolute_error(y_test, _naive_queue_rule(X_test, RETRIEVAL_FEATURE_COLUMNS))

    _print_headline(metrics)
    _print_baselines(metrics, naive_mae)
    _print_breakdown(dataset, model, test_mask)

    print("\n  ERROR SIGN (is the estimate optimistic?)")
    print("  ----------------------------------------")
    predicted = model.predict_array(X_test)
    signed = predicted - y_test
    print(f"  mean signed error   : {float(np.mean(signed)):+.3f} minutes")
    print(f"  under-predicts      : {float(np.mean(signed < 0)):.1%} of retrievals")
    print(f"  over-predicts       : {float(np.mean(signed > 0)):.1%} of retrievals")
    if float(np.mean(signed)) < -0.3:
        print("  note: systematically optimistic. The estimate promises a shorter")
        print("        wait than reality delivers - the more visible failure mode.")
    elif float(np.mean(signed)) > 0.3:
        print("  note: systematically pessimistic - customers are told to wait")
        print("        longer than needed.")

    if args.fresh_seed is not None:
        fresh = generate_history(
            episodes=args.episodes, seed=args.fresh_seed,
            span_days=args.span_days, test_fraction=args.test_fraction,
        )
        fresh_metrics = model.score(fresh.X[fresh.test_mask], fresh.y[fresh.test_mask])
        print(f"\n  HELD-OUT HISTORY FROM A DIFFERENT GENERATOR SEED ({args.fresh_seed})")
        print(f"  {'-' * 52}")
        print(f"  MAE             : {fresh_metrics.mae:.3f} minutes")
        print(f"  median baseline : {fresh_metrics.baseline_mae:.3f} minutes")
        print(f"  improvement     : {fresh_metrics.improvement_over_baseline:.1%}")
        print("  A stable MAE across seeds means the model learned a relationship,")
        print("  not one generator's noise.")

    print("\n" + "!" * 74)
    print("  These figures describe a model fitted to synthetic data generated by")
    print("  ai/retrieval/dataset.py. They measure how well it recovers that")
    print("  generator. They are not evidence of accuracy in a real valet yard.")
    print("!" * 74)

    if metrics.improvement_over_baseline <= 0:
        print("\nFAILED: the model does not beat the median baseline. Not shipped.")
        return 1

    print(f"\nPASSED: MAE {metrics.mae:.2f} min vs best baseline "
          f"{min(metrics.baseline_mae, metrics.mean_baseline_mae, naive_mae):.2f} min.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
