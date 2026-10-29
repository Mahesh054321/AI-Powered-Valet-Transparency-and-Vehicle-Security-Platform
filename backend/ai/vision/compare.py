"""Steps 7-10: compare, threshold, suppress false positives, extract regions.

The comparison uses three deliberately different channels, because a single
difference metric cannot tell the failure modes apart:

* **luminance** (LAB L) -- catches dents and deformations, which change how a
  panel catches light;
* **chroma** (LAB a/b distance) -- catches rust, paint mismatch and dirt, which
  change colour without necessarily changing shape;
* **relative edge change** -- catches scratches and cracks, which are thin
  enough to move very few pixels but create a new gradient where there was none.

The edge channel is expressed as a *relative* change
(``|g_after - g_before| / max(g_before, g_after)``) rather than an absolute one.
That makes it immune to exposure differences, at the cost of not being able to
tell a weakened edge from a vanished one -- an acceptable trade for a heuristic.

**False positives are the whole difficulty here.** Four independent defences
are applied in order, each targeting a specific artefact, and each is a
documented constant rather than a learned parameter. See
``_suppress_lighting_artefacts`` and the docstring on ``significant_regions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ai.vision.config import PipelineConfig
from ai.vision.preprocessing import (
    correct_luminance,
    estimate_luminance_gain,
    grayscale,
    to_lab,
)


@dataclass(frozen=True, slots=True)
class DifferenceMap:
    """Every channel the comparison produced, kept for the rendered report."""

    luminance: np.ndarray
    chroma: np.ndarray
    #: Signed chroma deltas, kept so rust (a warm shift) can be told apart from
    #: a colour change that happens to be the same distance in the a/b plane.
    delta_a: np.ndarray
    delta_b: np.ndarray
    #: Signed lightness delta: positive means the region got brighter.
    delta_l: np.ndarray
    edge: np.ndarray
    combined: np.ndarray
    #: Gradient already present in the drop-off photo, kept because a region
    #: sitting on a pre-existing edge is far more likely to be registration
    #: residue than damage.
    pre_existing_edge: np.ndarray
    #: The combined map after blurring -- the one actually thresholded.
    smoothed: np.ndarray
    #: Binary change mask after morphological cleanup and border exclusion.
    mask: np.ndarray
    #: Exposure gain applied to the pickup frame before differencing.
    luminance_gain: float
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RegionCandidate:
    """One contiguous changed area, with the statistics that justify flagging it."""

    #: ``(x, y, width, height)`` in working-resolution pixels.
    bbox: tuple[int, int, int, int]
    area: int
    #: Fraction of the whole frame, so it survives resolution changes.
    area_fraction: float
    mean_difference: float
    peak_difference: float
    mean_chroma: float
    mean_edge: float
    #: Signed means, used by the labeller: warm-and-chromatic reads as rust,
    #: dark-and-achromatic reads as a dent.
    mean_delta_a: float
    mean_delta_b: float
    mean_delta_l: float
    #: Mean of the ABSOLUTE lightness change. Compared against ``mean_delta_l``
    #: this reveals bipolar shading: a dent brightens on one side and darkens on
    #: the other, so its mean is near zero while its mean absolute change is
    #: large. Any rule built on the signed mean alone is blind to dents.
    mean_abs_delta_l: float
    #: Mean gradient that was ALREADY in the drop-off photo at this region. High
    #: values mean the region sits on structure that existed before, which is the
    #: signature of registration residue rather than new damage.
    mean_pre_existing_edge: float
    #: Contour area over bounding-box area. Low means thin and wispy; high means
    #: solid. This is what separates a scratch from a patch.
    fill_ratio: float
    aspect_ratio: float
    #: Number of merged sub-regions, which is itself weak evidence: a real
    #: scratch often breaks into collinear fragments.
    fragments: int
    #: Frame size the box was measured in, so `normalised_bbox` can divide by it.
    frame_width: int = 0
    frame_height: int = 0

    @property
    def normalised_bbox(self) -> dict[str, float]:
        """Bounding box in ``[0, 1]`` image coordinates.

        Stored normalised so the region stays correct if the image is later
        re-rendered at another resolution -- the schema's stated contract.
        """
        x, y, width, height = self.bbox
        return {
            "x": round(x / max(1, self.frame_width), 6),
            "y": round(y / max(1, self.frame_height), 6),
            "width": round(width / max(1, self.frame_width), 6),
            "height": round(height / max(1, self.frame_height), 6),
        }

    def describe(self) -> dict[str, Any]:
        return {
            "bbox": self.bbox,
            "normalised_bbox": self.normalised_bbox,
            "area": self.area,
            "area_fraction": round(self.area_fraction, 6),
            "mean_difference": round(self.mean_difference, 3),
            "peak_difference": round(self.peak_difference, 3),
            "mean_chroma": round(self.mean_chroma, 3),
            "mean_edge": round(self.mean_edge, 3),
            "mean_delta_a": round(self.mean_delta_a, 3),
            "mean_delta_b": round(self.mean_delta_b, 3),
            "mean_delta_l": round(self.mean_delta_l, 3),
            "mean_abs_delta_l": round(self.mean_abs_delta_l, 3),
            "mean_pre_existing_edge": round(self.mean_pre_existing_edge, 3),
            "fill_ratio": round(self.fill_ratio, 4),
            "aspect_ratio": round(self.aspect_ratio, 3),
            "fragments": self.fragments,
        }


def _gradient_magnitude(channel: np.ndarray, kernel: int) -> np.ndarray:
    """Sobel gradient magnitude, as a float map."""
    horizontal = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=kernel)
    vertical = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=kernel)
    return cv2.magnitude(horizontal, vertical)


def _odd(value: int) -> int:
    """Gaussian and morphological kernels must be odd and positive."""
    return max(1, value if value % 2 == 1 else value + 1)


def compare(
    before: np.ndarray,
    after: np.ndarray,
    *,
    config: PipelineConfig,
) -> DifferenceMap:
    """Steps 4, 5, 7: normalise lighting, convert to LAB, difference.

    ``before`` and ``after`` must already be the same shape -- alignment runs
    first precisely so this can be a plain per-pixel subtraction.
    """
    if before.shape != after.shape:
        raise ValueError(
            f"Cannot difference images of different shapes: "
            f"{before.shape} vs {after.shape}. Align them first."
        )

    lab_before = to_lab(before)
    lab_after = to_lab(after)

    gain = estimate_luminance_gain(lab_before, lab_after, config=config)
    lab_after = correct_luminance(lab_after, gain)

    delta_l = lab_after[:, :, 0] - lab_before[:, :, 0]
    delta_a = lab_after[:, :, 1] - lab_before[:, :, 1]
    delta_b = lab_after[:, :, 2] - lab_before[:, :, 2]

    luminance = np.abs(delta_l)
    chroma = cv2.magnitude(delta_a, delta_b)

    gradient_before = _gradient_magnitude(grayscale(before), config.sobel_kernel)
    gradient_after = _gradient_magnitude(grayscale(after), config.sobel_kernel)

    # How much local contrast INCREASED, as a ratio over what was already there.
    #
    # This is the single most consequential line in the pipeline. The obvious
    # formulation, |g_after - g_before|, saturates at every strong edge in the
    # frame: a panel seam that shifts by one pixel between shots produces a huge
    # absolute difference, indistinguishable from a scratch. Measuring the
    # increase instead separates them, because a seam that merely moved has
    # nearly the same gradient on both sides of the ratio while a scratch across
    # smooth paint goes from none to a lot.
    floor = max(
        config.edge_floor_minimum,
        float(np.percentile(gradient_before, config.edge_floor_percentile)) * 0.05,
    )
    ratio = gradient_after / (gradient_before + floor)
    edge = (
        np.clip(
            (ratio - 1.0) / config.new_edge_saturation,
            0.0,
            1.0,
        )
        * 255.0
    )

    total_weight = config.luminance_weight + config.chroma_weight + config.edge_weight
    combined = (
        config.luminance_weight * luminance
        + config.chroma_weight * chroma
        + config.edge_weight * edge
    ) / max(total_weight, 1e-6)

    kernel = _odd(config.difference_blur_kernel)
    smoothed = cv2.GaussianBlur(combined, (kernel, kernel), 0)

    mask = _threshold_to_mask(smoothed, config=config)
    height, width = combined.shape[:2]

    return DifferenceMap(
        luminance=luminance.astype(np.float32),
        chroma=chroma.astype(np.float32),
        delta_a=delta_a.astype(np.float32),
        delta_b=delta_b.astype(np.float32),
        delta_l=delta_l.astype(np.float32),
        pre_existing_edge=gradient_before.astype(np.float32),
        edge=edge.astype(np.float32),
        combined=combined.astype(np.float32),
        smoothed=smoothed.astype(np.float32),
        mask=mask,
        luminance_gain=gain,
        width=int(width),
        height=int(height),
    )


def _threshold_to_mask(smoothed: np.ndarray, *, config: PipelineConfig) -> np.ndarray:
    """Steps 8-9: threshold, then remove speckle and border artefacts.

    Three cleanup operations, in the order that matters:

    1. **Morphological opening** removes isolated bright pixels. Sensor noise
       and JPEG blocking artefacts are typically one to three pixels across and
       do not survive a 5x5 opening; a real scratch does.
    2. **Border exclusion** zeroes a margin around the frame. Perspective warping
       invents content at the edges (``BORDER_REPLICATE`` smears the outermost
       column across), and edges are exactly where that shows up.
    3. **A second opening** after the border cut, so the cut does not leave a
       one-pixel line that the contour step would trace.
    """
    thresholded = (smoothed >= config.difference_threshold).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_odd(config.morph_kernel_size),) * 2
    )
    cleaned = cv2.morphologyEx(thresholded, cv2.MORPH_OPEN, kernel)

    height, width = cleaned.shape
    margin_x = max(1, int(width * config.border_margin_fraction))
    margin_y = max(1, int(height * config.border_margin_fraction))
    cleaned[:margin_y, :] = 0
    cleaned[-margin_y:, :] = 0
    cleaned[:, :margin_x] = 0
    cleaned[:, -margin_x:] = 0

    return cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    """Intersection over union of two ``(x, y, w, h)`` boxes."""
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right

    overlap_x = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    overlap_y = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    overlap = overlap_x * overlap_y
    if overlap == 0:
        return 0.0

    union = lw * lh + rw * rh - overlap
    return overlap / union if union else 0.0


def _merge_boxes(
    boxes: list[tuple[int, int, int, int]], *, threshold: float
) -> list[list[int]]:
    """Group box indices whose bounding boxes overlap enough to be one defect.

    A single scratch across a door frequently breaks into several collinear
    fragments where the paint caught the light differently. Reporting each as a
    separate finding would overstate the damage, so overlapping fragments are
    merged and reported once with ``fragments`` recording how many went in.
    """
    groups: list[list[int]] = [[index] for index in range(len(boxes))]

    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if not groups[i] or not groups[j]:
                    continue
                box_i = _union_box([boxes[k] for k in groups[i]])
                box_j = _union_box([boxes[k] for k in groups[j]])
                if _iou(box_i, box_j) >= threshold:
                    groups[i] = [*groups[i], *groups[j]]
                    groups[j] = []
                    changed = True
        groups = [group for group in groups if group]

    return groups


def _union_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """Smallest box containing all of ``boxes``."""
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return left, top, right - left, bottom - top


def significant_regions(
    difference: DifferenceMap, *, config: PipelineConfig
) -> list[RegionCandidate]:
    """Step 10: turn the change mask into a small list of candidate regions.

    A connected component becomes a candidate only if it clears every gate. The
    gates, and the artefact each one exists to reject:

    * **minimum area fraction** -- sensor noise and compression blocking;
    * **diffuse-region rejection** -- a change covering a third of the frame
      with a weak mean signal is an illumination or shadow change, not a defect.
      This is the single most valuable filter: it is what stops a cloud passing
      over the car from being reported as damage;
    * **fragment merging** -- one defect reported once rather than as the
      handful of pieces it broke into.

    Regions that fail a gate are dropped, not scored zero. A reviewer who wants
    to see them can lower the thresholds via
    :meth:`PipelineConfig.with_overrides` -- which is the honest way to expose
    a heuristic's sensitivity.
    """
    mask = difference.mask
    height, width = mask.shape
    frame_area = float(width * height)

    # WithStats returns every component's box and area in a single pass. Doing
    # this instead of scanning the label image once per component matters: a
    # noisy mask can hold hundreds of components, and the naive version is
    # quadratic in the number of them.
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    if count <= 1:
        return []

    raw: list[tuple[int, tuple[int, int, int, int], int]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area / frame_area < config.min_region_area_fraction:
            continue
        box = (
            int(stats[label, cv2.CC_STAT_LEFT]),
            int(stats[label, cv2.CC_STAT_TOP]),
            int(stats[label, cv2.CC_STAT_WIDTH]),
            int(stats[label, cv2.CC_STAT_HEIGHT]),
        )
        raw.append((label, box, area))

    if not raw:
        return []

    boxes = [entry[1] for entry in raw]
    groups = _merge_boxes(boxes, threshold=config.merge_iou)

    candidates: list[RegionCandidate] = []
    for group in groups:
        member_labels = [raw[index][0] for index in group]
        region_mask = np.isin(labels, member_labels)
        if not region_mask.any():
            continue

        area = int(region_mask.sum())
        box = _union_box([raw[index][1] for index in group])
        box_width, box_height = box[2], box[3]

        values = difference.smoothed[region_mask]
        mean_difference = float(values.mean())

        # -- diffuse-region rejection ---------------------------------------
        box_fraction = (box_width * box_height) / frame_area
        if (
            box_fraction > config.diffuse_region_fraction
            and mean_difference < config.diffuse_mean_ceiling
        ):
            continue

        # -- registration-residue rejection --------------------------------
        # A region lying on structure that was already in the drop-off photo,
        # with no colour change to corroborate it, is most likely an existing
        # edge that alignment failed to line up exactly. Real damage across
        # smooth paint has little pre-existing gradient to sit on.
        pre_edge = float(difference.pre_existing_edge[region_mask].mean())
        if (
            pre_edge >= config.pre_existing_edge_ceiling
            and float(difference.chroma[region_mask].mean())
            < config.significant_chroma * config.residue_chroma_fraction
        ):
            continue

        if area / frame_area < config.min_region_area_fraction:
            continue

        candidate = RegionCandidate(
            bbox=box,
            area=area,
            area_fraction=area / frame_area,
            mean_difference=mean_difference,
            peak_difference=float(values.max()),
            mean_chroma=float(difference.chroma[region_mask].mean()),
            mean_edge=float(difference.edge[region_mask].mean()),
            mean_delta_a=float(difference.delta_a[region_mask].mean()),
            mean_delta_b=float(difference.delta_b[region_mask].mean()),
            mean_delta_l=float(difference.delta_l[region_mask].mean()),
            mean_abs_delta_l=float(np.abs(difference.delta_l[region_mask]).mean()),
            mean_pre_existing_edge=float(difference.pre_existing_edge[region_mask].mean()),
            fill_ratio=area / float(box_width * box_height),
            aspect_ratio=max(box_width, box_height) / max(1, min(box_width, box_height)),
            fragments=len(group),
            frame_width=width,
            frame_height=height,
        )
        candidates.append(candidate)

    candidates.sort(key=lambda item: item.mean_difference, reverse=True)
    return candidates


__all__ = [
    "DifferenceMap",
    "RegionCandidate",
    "compare",
    "significant_regions",
]
