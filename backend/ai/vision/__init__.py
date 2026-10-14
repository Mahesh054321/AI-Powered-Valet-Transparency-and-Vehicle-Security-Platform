"""Classical OpenCV pipeline for vehicle condition comparison.

Modules, in the order the pipeline uses them:

    config.py         every threshold, as plain data
    preprocessing.py  steps 1-5: load, validate, resize, normalise, LAB
    alignment.py      step 6:    ORB + RANSAC homography
    compare.py        steps 7-10: differencing, thresholds, region extraction
    regions.py        frame position -> named vehicle region
    damage.py         step 11:   scoring and labelling
    report.py         step 13:   the annotated evidence images
    pipeline.py       the assembled comparison, and the only public entry point

Nothing here is a trained model. There are no weights, no checkpoint and no
dataset in this repository; the behaviour is the constants in ``config.py`` plus
this code. See ``docs/AI-INTEGRITY.md`` for what that does and does not license
the project to claim.

Import direction is one-way: ``services`` may import ``ai.vision``, never the
reverse. This package therefore knows nothing about Flask, SQLAlchemy or where
files are stored, which is what makes it testable without an application.
"""

from __future__ import annotations

from ai.vision.config import DEFAULT_CONFIG, PipelineConfig
from ai.vision.pipeline import ComparisonResult, EvidenceImage, compare_images

__all__ = [
    "DEFAULT_CONFIG",
    "ComparisonResult",
    "EvidenceImage",
    "PipelineConfig",
    "compare_images",
]
