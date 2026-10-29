"""Synthetic labelled telemetry for training and evaluating the classifier.

**This data is not real, and the labels are not observations.** Every sample is
drawn from one of two hand-written generative distributions -- "ordinary yard
behaviour" and "behaviour we would want a human to look at" -- and the label is
simply which distribution produced it. So the accuracy this dataset yields
measures one thing: how well a model recovers *the distinction this file drew*.
It says nothing about how suspicious real vehicle movement is. See
``docs/AI-INTEGRITY.md`` for the full statement.

Two methodological points that are easy to get wrong and matter here:

**The split is by episode, not by row.** An episode is a short coherent sequence
of fixes from one vehicle. Successive fixes in an episode are heavily
correlated, so a random row-level split would put near-duplicates of a test
sample into the training set and report an accuracy that could not survive
contact with a new vehicle. Every fix from one episode lands in the same split.

**Labels are per fix, not per episode.** A suspicious episode typically begins
with the vehicle parked, and those early fixes are genuinely unremarkable.
Labelling the whole episode suspicious would inject label noise and inflate the
apparent difficulty; labelling each fix by the generator's intent at that moment
gives a signal that means what it says.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ai.ml.features import FEATURE_COLUMNS

#: The two classes. Deliberately a binary problem: the rule engine already
#: distinguishes WARNING from BREACH by measurement, and asking a prototype
#: classifier to reproduce that ordering would only add a way to be wrong.
CLASS_NORMAL = "NORMAL"
CLASS_SUSPICIOUS = "SUSPICIOUS"
CLASSES: tuple[str, ...] = (CLASS_NORMAL, CLASS_SUSPICIOUS)

#: Proportion of episodes that are suspicious. Real breaches are rare; a
#: balanced set is used for training because it is the honest way to learn the
#: boundary at all, and the imbalance is reported rather than hidden.
SUSPICIOUS_EPISODE_RATE = 0.45

EPISODE_LENGTH_RANGE = (6, 14)


@dataclass(frozen=True)
class Dataset:
    """A generated dataset, already split by episode."""

    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    split: np.ndarray
    classes: tuple[str, ...] = CLASSES

    def __len__(self) -> int:
        return int(self.X.shape[0])

    @property
    def train_mask(self) -> np.ndarray:
        return self.split == "train"

    @property
    def test_mask(self) -> np.ndarray:
        return self.split == "test"

    def summary(self) -> dict:
        """Counts a reviewer needs in order to judge the reported metrics."""
        return {
            "rows": len(self),
            "episodes": int(len(np.unique(self.groups))),
            "features": list(FEATURE_COLUMNS),
            "classes": list(self.classes),
            "train_rows": int(self.train_mask.sum()),
            "test_rows": int(self.test_mask.sum()),
            "train_normal": int(((self.y == 0) & self.train_mask).sum()),
            "train_suspicious": int(((self.y == 1) & self.train_mask).sum()),
            "test_normal": int(((self.y == 0) & self.test_mask).sum()),
            "test_suspicious": int(((self.y == 1) & self.test_mask).sum()),
        }


def _time_pair(rng: np.random.Generator, night: bool) -> tuple[float, float]:
    """A time of day on the 24-hour circle, biased to night or to the working day."""
    hour = rng.uniform(0.0, 5.0) if night else rng.uniform(7.0, 22.0)
    angle = 2.0 * math.pi * (hour / 24.0)
    return math.sin(angle), math.cos(angle)


def _normal_episode(
    rng: np.random.Generator, episode_id: int
) -> list[tuple[list[float], int, int]]:
    """Ordinary behaviour: parked, or manoeuvring inside the zone.

    Returns rows of ``(features, label, group)``.

    **The ranges deliberately overlap the suspicious generator.** Ordinary valet
    work sometimes does cross the boundary by a few metres, and a car being
    driven to a far bay does reach 25 km/h. If the two classes were perfectly
    separable the classifier would score 1.0 and the evaluation would prove
    nothing at all - a single threshold on one feature would do the same job.
    """
    length = int(rng.integers(*EPISODE_LENGTH_RANGE))
    rows: list[tuple[list[float], int, int]] = []
    for _ in range(length):
        speed = float(rng.uniform(0.0, 26.0))
        # Mostly inside, occasionally just outside the boundary - which the
        # buffer treats as tolerable and which a naive detector would flag.
        distance = float(-rng.uniform(3.0, 120.0))
        if rng.random() < 0.12:
            distance = float(rng.uniform(0.0, 22.0))
        time_sin, time_cos = _time_pair(rng, night=bool(rng.random() < 0.15))
        features = [
            speed + float(rng.normal(0.0, 1.5)),
            distance + float(rng.normal(0.0, 4.0)),
            float(rng.uniform(0.0, 260.0)),  # movement_duration_seconds
            float(rng.uniform(0.0, 75.0)),  # direction_change_degrees
            float(rng.uniform(0.0, 60.0)),  # distance_travelled_metres
            0.0,  # zone_breach: never beyond the tolerance band
            time_sin,
            time_cos,
        ]
        rows.append((features, 0, episode_id))
    return rows


def _suspicious_episode(
    rng: np.random.Generator, episode_id: int
) -> list[tuple[list[float], int, int]]:
    """Behaviour a human should look at: a departure, a joyride, or an odd-hours move.

    The first couple of fixes are the vehicle sitting still, and they are
    labelled NORMAL because they genuinely are. Only the fixes that express the
    suspicious behaviour carry the suspicious label.
    """
    length = int(rng.integers(*EPISODE_LENGTH_RANGE))
    lead_in = int(rng.integers(1, 3))
    night = bool(rng.random() < 0.7)

    # Which of the suspicious signatures this episode expresses. Drawing two or
    # three at once is what makes the problem non-trivial for a linear model.
    # Roughly half the suspicious episodes never leave the zone at all: a
    # vehicle being driven around the yard at speed with no task is exactly the
    # case a geofence-only rule misses, and it is the reason to have a second
    # signal. Keeping those in the data is what stops `zone_breach` from being
    # a perfect shortcut.
    signatures = {
        "leaves_zone": bool(rng.random() < 0.5),
        "fast": bool(rng.random() < 0.6),
        "erratic": bool(rng.random() < 0.55),
        "sustained": bool(rng.random() < 0.55),
    }
    if not any(signatures.values()):
        signatures["fast"] = True
    # How strongly this episode expresses its signature. Marginal cases sit
    # close to the ordinary ranges, which is what makes the boundary learnable
    # rather than trivial.
    intensity = float(rng.uniform(0.35, 1.0))

    rows: list[tuple[list[float], int, int]] = []
    for index in range(length):
        if index < lead_in:
            rows.extend(_normal_episode_rows(rng, episode_id, count=1))
            continue

        speed = (
            float(rng.uniform(22.0, 90.0) * (0.55 + 0.45 * intensity))
            if signatures["fast"]
            else float(rng.uniform(0.0, 24.0))
        )
        if signatures["leaves_zone"]:
            # Scaled by intensity, so a marginal case sits just past the
            # tolerance band where ordinary behaviour also reaches.
            distance = float(rng.uniform(12.0, 900.0) * intensity)
            zone_breach = 1.0 if distance > 25.0 else 0.0
        else:
            distance = float(-rng.uniform(2.0, 95.0))
            zone_breach = 0.0
        direction_change = (
            float(rng.uniform(45.0, 180.0) * (0.5 + 0.5 * intensity))
            if signatures["erratic"]
            else float(rng.uniform(0.0, 70.0))
        )
        travelled = (
            float(rng.uniform(25.0, 400.0) * (0.5 + 0.5 * intensity))
            if signatures["fast"]
            else float(rng.uniform(0.0, 45.0))
        )
        duration = (
            float(rng.uniform(250.0, 3000.0) * (0.5 + 0.5 * intensity))
            if signatures["sustained"]
            else float(rng.uniform(0.0, 230.0))
        )
        time_sin, time_cos = _time_pair(rng, night=night)
        features = [
            speed + float(rng.normal(0.0, 2.0)),
            distance + float(rng.normal(0.0, 6.0)),
            duration,
            direction_change,
            travelled,
            zone_breach,
            time_sin,
            time_cos,
        ]
        rows.append((features, 1, episode_id))
    return rows


def _normal_episode_rows(
    rng: np.random.Generator, episode_id: int, count: int = 1
) -> list[tuple[list[float], int, int]]:
    """A small number of unremarkable fixes, used for a suspicious episode's lead-in."""
    rows: list[tuple[list[float], int, int]] = []
    for _ in range(count):
        time_sin, time_cos = _time_pair(rng, night=False)
        rows.append(
            (
                [
                    float(rng.uniform(0.0, 8.0)),
                    float(-rng.uniform(8.0, 100.0)),
                    0.0,
                    float(rng.uniform(0.0, 45.0)),
                    float(rng.uniform(0.0, 10.0)),
                    0.0,
                    time_sin,
                    time_cos,
                ],
                0,
                episode_id,
            )
        )
    return rows


def generate_dataset(
    *,
    episodes: int = 1200,
    seed: int = 20260908,
    test_fraction: float = 0.25,
) -> Dataset:
    """Generate a labelled dataset, split by episode.

    ``seed`` makes the whole dataset reproducible, so a reviewer running the
    training script gets the same numbers the documentation quotes.
    """
    if episodes < 4:
        raise ValueError(f"episodes must be at least 4; got {episodes}")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError(f"test_fraction must be in (0, 1); got {test_fraction}")

    rng = np.random.default_rng(seed)

    # Assign the split to whole episodes before generating any rows. Doing it
    # afterwards, row by row, would split correlated fixes across the boundary.
    suspicious_count = int(round(episodes * SUSPICIOUS_EPISODE_RATE))
    is_suspicious = np.zeros(episodes, dtype=bool)
    is_suspicious[rng.choice(episodes, size=suspicious_count, replace=False)] = True
    episode_is_test = rng.random(episodes) < test_fraction

    rows: list[tuple[list[float], int, int]] = []
    splits: list[str] = []
    for episode_id in range(episodes):
        generated = (
            _suspicious_episode(rng, episode_id)
            if is_suspicious[episode_id]
            else _normal_episode(rng, episode_id)
        )
        rows.extend(generated)
        splits.extend(["test" if episode_is_test[episode_id] else "train"] * len(generated))

    X = np.array([row[0] for row in rows], dtype=float)
    y = np.array([row[1] for row in rows], dtype=int)
    groups = np.array([row[2] for row in rows], dtype=int)

    if X.shape[1] != len(FEATURE_COLUMNS):
        raise AssertionError(
            f"generated {X.shape[1]} features but FEATURE_COLUMNS declares "
            f"{len(FEATURE_COLUMNS)}: the generator and the feature module have "
            "drifted apart"
        )
    if len(y[y == 1]) == 0 or len(y[y == 0]) == 0:
        raise AssertionError("the generated dataset contains only one class")

    return Dataset(
        X=X,
        y=y,
        groups=groups,
        split=np.array(splits),
    )


__all__ = [
    "CLASS_NORMAL",
    "CLASS_SUSPICIOUS",
    "CLASSES",
    "SUSPICIOUS_EPISODE_RATE",
    "Dataset",
    "generate_dataset",
]
