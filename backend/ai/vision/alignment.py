"""Step 6: align the pickup photo onto the drop-off photo.

Two photographs of the same car panel taken minutes apart are never pixel-
aligned. The phone moved, the valet stood half a step to the left, the car
settled. Comparing them pixel-for-pixel without correcting for that would flag
every edge in the image -- the panel gaps, the window line, the badge -- as
damage, and the result would be a wall of false positives.

So the pipeline finds features common to both frames and warps one onto the
other. ORB plus RANSAC is used rather than a learned matcher because it is
deterministic, needs no training data, and its failure mode is legible: it
either finds enough consistent matches to compute a homography, or it does not.

**Failing loudly is the important part.** When alignment is not possible the
result says so explicitly and the run carries a warning, rather than quietly
comparing misaligned frames. A comparison that cannot be trusted should never
look like one that can.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ai.vision.config import PipelineConfig


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Outcome of an alignment attempt, including why it did or did not apply."""

    #: The ``after`` image transformed into the ``before`` image's frame.
    image: np.ndarray
    applied: bool
    #: Inliers as a fraction of accepted matches, in ``[0, 1]``. Zero when no
    #: usable matches were found. This is a *ranking* signal for how much to
    #: trust the run, not a probability.
    quality: float
    matches: int
    inliers: int
    #: Populated when alignment was skipped, so the reason is never a mystery.
    reason: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "quality": round(self.quality, 4),
            "matches": self.matches,
            "inliers": self.inliers,
            "reason": self.reason,
        }


def _good_matches(
    descriptor_before: np.ndarray,
    descriptor_after: np.ndarray,
    *,
    config: PipelineConfig,
) -> list[cv2.DMatch]:
    """Match descriptors, keeping only those that pass Lowe's ratio test.

    The ratio test asks whether a feature's best match is clearly better than
    its second best. On a car body -- flat paint, repeated panel lines, four
    near-identical wheels -- many features have several plausible matches, and
    accepting all of them would feed RANSAC mostly noise.
    """
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = matcher.knnMatch(descriptor_after, descriptor_before, k=2)

    kept: list[cv2.DMatch] = []
    for pair in raw:
        # knnMatch can return fewer than k results near the image border.
        if len(pair) < 2:
            continue
        best, second = pair
        if best.distance < config.match_ratio * second.distance:
            kept.append(best)
    return kept


def align(
    before: np.ndarray,
    after: np.ndarray,
    *,
    config: PipelineConfig,
) -> AlignmentResult:
    """Warp ``after`` into ``before``'s frame, or explain why that was not done.

    Always returns a usable image: when alignment is not possible the original
    ``after`` is returned unmodified. That keeps the pipeline able to produce a
    best-effort result, while ``applied`` and ``reason`` make clear that the
    result is best-effort.
    """
    height, width = before.shape[:2]

    orb = cv2.ORB_create(nfeatures=config.orb_features)
    keypoints_before, descriptor_before = orb.detectAndCompute(before, None)
    keypoints_after, descriptor_after = orb.detectAndCompute(after, None)

    if descriptor_before is None or descriptor_after is None:
        return AlignmentResult(
            image=after,
            applied=False,
            quality=0.0,
            matches=0,
            inliers=0,
            reason=(
                "No features could be detected in one of the images. Plain, "
                "uniformly lit panels give ORB nothing to match."
            ),
        )

    matches = _good_matches(descriptor_before, descriptor_after, config=config)
    if len(matches) < config.min_matches:
        return AlignmentResult(
            image=after,
            applied=False,
            quality=0.0,
            matches=len(matches),
            inliers=0,
            reason=(
                f"Only {len(matches)} feature match(es) passed the ratio test; "
                f"{config.min_matches} are needed to fit a reliable transform. "
                "The frames were compared unaligned, so edge-aligned false "
                "positives are more likely."
            ),
        )

    source = np.float32([keypoints_after[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    target = np.float32([keypoints_before[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(
        source, target, cv2.RANSAC, config.ransac_threshold
    )
    if homography is None or mask is None:
        return AlignmentResult(
            image=after,
            applied=False,
            quality=0.0,
            matches=len(matches),
            inliers=0,
            reason="RANSAC could not fit a consistent transform to the matches.",
        )

    inliers = int(mask.ravel().sum())
    quality = inliers / len(matches) if matches else 0.0

    warped = cv2.warpPerspective(
        after,
        homography,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return AlignmentResult(
        image=warped,
        applied=True,
        quality=quality,
        matches=len(matches),
        inliers=inliers,
        reason=(
            None
            if quality >= config.min_alignment_quality
            else (
                f"Alignment applied but only {inliers} of {len(matches)} matches "
                f"were consistent (quality {quality:.2f} < "
                f"{config.min_alignment_quality}). Confidence in every finding "
                "from this run is reduced."
            )
        ),
    )


__all__ = ["AlignmentResult", "align"]
