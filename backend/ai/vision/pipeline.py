"""The 13-step condition comparison, assembled.

This module is the only entry point the service layer uses. Each stage lives in
its own module so a reviewer can read one step at a time, and so a stage can be
substituted later -- a learned alignment model, say -- without touching the
rest.

The order matters and is not arbitrary:

    1-3  load, validate, resize          ai.vision.preprocessing
    4-5  normalise lighting, to LAB      ai.vision.preprocessing / compare
    6    align the two frames            ai.vision.alignment
    7-9  difference, threshold, clean    ai.vision.compare
    10   candidate regions               ai.vision.compare
    11   label and score                 ai.vision.damage
    12   persist                         services.inspections
    13   render evidence                 ai.vision.report

Step 12 is deliberately *not* here. This package knows nothing about Flask,
SQLAlchemy or the storage layout, which is what makes it testable in isolation
and keeps the import direction one-way.

**What the result means.** ``findings`` is a ranked list of regions that
changed, each labelled *potential* damage. An empty list means no region cleared
the thresholds -- it does not mean the vehicle is undamaged, and every consumer
of this result says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ai.vision.alignment import AlignmentResult, align
from ai.vision.compare import DifferenceMap, compare, significant_regions
from ai.vision.config import DEFAULT_CONFIG, PipelineConfig
from ai.vision.damage import Finding, assess
from ai.vision.preprocessing import PreparedImage, load_image
from ai.vision.regions import normalise_viewpoint
from ai.vision.report import render_comparison, render_evidence

#: Evidence crops beyond this are not rendered. A pathological comparison could
#: otherwise write dozens of PNGs for one inspection, and the top few by
#: confidence are the ones a reviewer will look at anyway.
MAX_EVIDENCE_CROPS = 8


@dataclass(frozen=True, slots=True)
class EvidenceImage:
    """One rendered evidence crop, paired with the finding it illustrates."""

    index: int
    png: bytes


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Everything one comparison produced, ready for the service layer to store."""

    findings: list[Finding]
    #: The three-panel before/after/difference composite.
    comparison_png: bytes
    #: Per-finding zoomed crops, capped at :data:`MAX_EVIDENCE_CROPS`.
    evidence: list[EvidenceImage]
    alignment: AlignmentResult
    #: Non-fatal problems a reviewer should know about.
    warnings: list[str]
    #: Frame and preprocessing facts, stored with the findings.
    statistics: dict[str, Any]
    #: The exact parameters used, for reproducibility.
    parameters: dict[str, Any]
    #: Which camera angle the region mapping assumed.
    viewpoint: str

    @property
    def damage_detected(self) -> bool:
        """True when at least one region cleared the thresholds.

        Named to match the API contract, but every response pairs it with the
        caveat that these are unverified candidates.
        """
        return bool(self.findings)


def _match_shape(
    before: np.ndarray, after: np.ndarray
) -> np.ndarray:
    """Ensure ``after`` has ``before``'s dimensions so differencing is valid.

    Alignment normally guarantees this. It does not when alignment was skipped,
    and two phones will rarely produce identical resolutions, so a plain resize
    is the fallback. That fallback is recorded as a warning by the caller, since
    a resized-but-unaligned pair is the least trustworthy comparison there is.
    """
    if before.shape[:2] == after.shape[:2]:
        return after
    height, width = before.shape[:2]
    return cv2.resize(after, (width, height), interpolation=cv2.INTER_AREA)


def compare_images(
    before_blob: bytes,
    after_blob: bytes,
    *,
    angle: str | None = None,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> ComparisonResult:
    """Run the full pipeline on two encoded images.

    ``angle`` is the camera viewpoint captured with the photos. It only affects
    how a changed region is *named* -- a missing angle still produces a full
    comparison, it just reports ``OTHER`` instead of guessing a panel.

    Raises ``ValidationError`` (HTTP 422) if either image cannot be decoded or is
    too small to assess.
    """
    viewpoint = normalise_viewpoint(angle)
    warnings: list[str] = []

    # -- steps 1-3: load, validate, resize ----------------------------------
    before: PreparedImage = load_image(
        before_blob, config=config, field="drop_off_image"
    )
    after: PreparedImage = load_image(after_blob, config=config, field="pickup_image")

    if before.orientation_applied or after.orientation_applied:
        warnings.append(
            "At least one photo carried an EXIF orientation tag and was rotated "
            "before comparison."
        )

    # -- step 6: align -------------------------------------------------------
    alignment: AlignmentResult = align(
        before.image, after.image, config=config
    )
    aligned = _match_shape(before.image, alignment.image)

    if not alignment.applied:
        warnings.append(
            f"Alignment was not applied: {alignment.reason} Regions may reflect "
            "camera movement rather than changes to the vehicle."
        )
    elif alignment.reason:
        warnings.append(alignment.reason)

    # -- steps 4, 5, 7: normalise, convert, difference ----------------------
    difference: DifferenceMap = compare(
        before.image, aligned, config=config
    )

    # -- steps 8-10: threshold, suppress artefacts, extract regions ----------
    candidates = significant_regions(difference, config=config)

    # -- step 11: label and score -------------------------------------------
    height, width = before.image.shape[:2]
    findings = assess(
        candidates,
        config=config,
        alignment_quality=alignment.quality,
        viewpoint=viewpoint,
        frame_width=width,
        frame_height=height,
    )

    if not findings:
        warnings.append(
            "No region exceeded the detection thresholds. This is not a "
            "guarantee that the vehicle is undamaged -- it means nothing stood "
            "out against these parameters."
        )

    # -- step 13: render the evidence ---------------------------------------
    comparison_png = render_comparison(
        before.image, aligned, difference, findings, warnings=warnings
    )
    evidence = [
        EvidenceImage(
            index=index,
            png=render_evidence(before.image, aligned, finding),
        )
        for index, finding in enumerate(findings[:MAX_EVIDENCE_CROPS])
    ]

    return ComparisonResult(
        findings=findings,
        comparison_png=comparison_png,
        evidence=evidence,
        alignment=alignment,
        warnings=warnings,
        statistics={
            "frame": {"width": width, "height": height},
            "before": before.describe(),
            "after": after.describe(),
            "alignment": alignment.describe(),
            "luminance_gain_applied": round(difference.luminance_gain, 4),
            "changed_pixel_fraction": round(
                float(np.count_nonzero(difference.mask)) / float(width * height), 6
            ),
            "viewpoint": str(viewpoint),
            "evidence_crops_rendered": len(evidence),
            "evidence_crops_skipped": max(0, len(findings) - len(evidence)),
        },
        parameters=config.as_params(),
        viewpoint=str(viewpoint),
    )


__all__ = [
    "MAX_EVIDENCE_CROPS",
    "ComparisonResult",
    "EvidenceImage",
    "compare_images",
]
