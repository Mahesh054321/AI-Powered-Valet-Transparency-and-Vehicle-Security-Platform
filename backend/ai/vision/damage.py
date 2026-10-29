"""Step 11: score each candidate region, and guess what kind of change it is.

Two different numbers come out of here, and keeping them apart is the honest
part.

**``confidence``** answers "did this region really change?" It is derived from
measured quantities -- mean and peak difference, region area -- so it is a
meaningful ranking signal, though still not a calibrated probability. Nothing in
this project has been validated against a labelled set, so no number it produces
can claim to be one.

**``type_confidence``** answers "is that change a scratch rather than a dent?"
That is a much weaker claim, made by comparing a handful of shape and colour
statistics against fixed thresholds. It is capped well below 1.0
(:data:`TYPE_CONFIDENCE_CEILING`) precisely because the heuristic is crude: a
shadow across a door and a scuff along a door produce similar statistics, and no
amount of threshold tuning changes that without real training data.

Every finding also carries ``detection_reasoning`` in plain language, naming the
statistics that produced it, so a reviewer can disagree with the number and see
why it was chosen.

**Nothing here is a damage classifier.** It ranks regions that changed and offers
a label for a human to accept or reject. The finding is stored as *potential*
damage until a manager verifies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from ai.vision.compare import RegionCandidate
from ai.vision.config import PipelineConfig
from ai.vision.regions import Viewpoint, map_region
from models.damage_detection import DamageType, DetectionMethod
from models.enums import DamageSeverity

#: Hard ceiling on labelling confidence. A shape-and-colour heuristic should
#: never be able to report 0.99 that something is a scratch: that number would
#: imply a validation this project has not done.
TYPE_CONFIDENCE_CEILING = 0.78

#: Region area fraction treated as "large" when scaling the area term.
REFERENCE_AREA_FRACTION = 0.01


@dataclass(frozen=True, slots=True)
class Finding:
    """One assessed region: what changed, where, how sure, and why."""

    #: Normalised bounding box, ``{"x","y","width","height"}`` each in ``[0, 1]``.
    bounding_box: dict[str, float]
    region: str
    damage_type: str
    severity: str
    confidence: float
    type_confidence: float
    reasoning: str
    pixel_area: int
    #: The candidate's own statistics, stored for reproducibility.
    statistics: dict[str, Any]
    #: How the region name was derived - viewpoint, grid cell, basis.
    region_mapping: dict[str, Any]
    method: str = "CLASSICAL_CV"
    model_version: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "bounding_box": self.bounding_box,
            "region": self.region,
            "damage_type": self.damage_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "type_confidence": self.type_confidence,
            "reasoning": self.reasoning,
            "pixel_area": self.pixel_area,
            "statistics": self.statistics,
            "region_mapping": self.region_mapping,
            "method": self.method,
            "model_version": self.model_version,
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(np.clip(value, low, high))


def score_confidence(
    candidate: RegionCandidate,
    *,
    alignment_quality: float,
    config: PipelineConfig,
) -> float:
    """Rank how likely this region is a real change rather than an artefact.

    A weighted blend of three measured terms:

    * **mean difference** (55%) -- the dominant signal; a region that barely
      differs from its surroundings is probably noise;
    * **peak difference** (25%) -- a genuine defect usually has a hot core, so
      this rewards real structure over flat noise;
    * **area** (20%), square-root scaled -- larger regions are less likely to be
      noise, but square-rooted so a big faint shadow cannot out-rank a small
      sharp scratch.

    When alignment was poor every finding is multiplied by
    ``config.low_alignment_penalty``. They are still reported -- a reviewer can
    look -- but they rank lower, because a misaligned comparison genuinely is
    less trustworthy.
    """
    magnitude = _clamp(candidate.mean_difference / config.saturation_difference)
    peak = _clamp(candidate.peak_difference / (config.saturation_difference * 1.6))
    area = _clamp(np.sqrt(candidate.area_fraction / REFERENCE_AREA_FRACTION))

    score = 0.55 * magnitude + 0.25 * peak + 0.20 * area

    if alignment_quality < config.min_alignment_quality:
        score *= config.low_alignment_penalty

    return round(_clamp(score, config.min_confidence, 1.0), 3)


def _severity_for(candidate: RegionCandidate) -> DamageSeverity:
    """Ordered severity from the same measured statistics.

    Deliberately crude and deliberately disclosed: it combines how strong the
    change is with how much of the panel it covers. It exists so findings can be
    sorted and triaged, not to quantify repair cost.
    """
    strength = candidate.mean_difference
    coverage = candidate.area_fraction

    if strength >= 55 or (strength >= 40 and coverage >= 0.02):
        return DamageSeverity.SEVERE
    if strength >= 38 or (strength >= 28 and coverage >= 0.015):
        return DamageSeverity.MODERATE
    if strength >= 26 or coverage >= 0.004:
        return DamageSeverity.MINOR
    return DamageSeverity.COSMETIC


def _is_elongated(candidate: RegionCandidate, config: PipelineConfig) -> bool:
    """Stretched along one axis: the shape signature of a scratch or crack.

    Aspect ratio alone, deliberately. An earlier version also required a low
    bounding-box fill ratio, on the reasoning that a thin line leaves most of its
    box empty. Measured against real output that was wrong: after the difference
    map is blurred and morphologically opened, a 238x17 scratch mask fills 62% of
    its box. The fill gate rejected every genuine scratch while admitting
    nothing, so the discriminator is the aspect ratio and nothing else.
    """
    return candidate.aspect_ratio >= config.elongated_aspect_ratio


def _is_bloblike(candidate: RegionCandidate, config: PipelineConfig) -> bool:
    """Roughly as wide as it is tall: not a line.

    Deliberately says nothing about how much of the bounding box is filled. An
    earlier version required a fill ratio above 0.45, on the assumption that a
    dent is a solid patch. Measured against real output that is false: a dent
    brightens on the side facing the light and darkens on the other, so its
    difference signature is a *ring*, which fills only ~28% of its box. Gating
    on solidity therefore rejected every genuine dent.

    The only shape claim this makes is the one that actually separates the
    classes: a scratch is a line, everything else is not.
    """
    return candidate.aspect_ratio < config.bloblike_aspect_ratio


def _is_warm_shift(candidate: RegionCandidate, config: PipelineConfig) -> bool:
    """Rust and oxidised paint shift towards red and yellow in LAB."""
    return (
        candidate.mean_delta_a > 2.0
        and candidate.mean_delta_b > 3.0
        and candidate.mean_chroma >= config.significant_chroma
    )


def classify(
    candidate: RegionCandidate, *, config: PipelineConfig
) -> tuple[DamageType, float, str]:
    """Guess the damage type from shape and colour statistics.

    Returns the label, a deliberately capped confidence in that label, and the
    reasoning. The rules are ordered from most specific to least, and the first
    match wins -- which is itself a limitation worth stating: a region matching
    two patterns is labelled by whichever rule comes first, not by weighing
    both.

    These rules were written by looking at what the statistics do on the demo
    photographs. They are **not** fitted to a labelled damage dataset, because
    this project has none.
    """
    elongated = _is_elongated(candidate, config)
    bloblike = _is_bloblike(candidate, config)
    chromatic = candidate.mean_chroma >= config.significant_chroma
    edge_led = candidate.mean_edge > candidate.mean_chroma

    # Bipolar shading: how evenly the region brightens and darkens. A dent does
    # both, so its signed mean is near zero while its absolute mean is large; a
    # stain or a paint chip changes brightness in one direction only.
    bipolarity = _clamp(
        1.0 - abs(candidate.mean_delta_l) / (candidate.mean_abs_delta_l + 1e-6)
    )

    if elongated and edge_led:
        basis = (
            f"the region is thin and stretched (aspect ratio "
            f"{candidate.aspect_ratio:.1f}, fill "
            f"{candidate.fill_ratio:.2f}) and the change is concentrated in new "
            f"edges rather than in colour"
        )
        label = DamageType.SCRATCH
        # Confidence rises with how far past the thresholds it sits.
        margin = _clamp(
            (candidate.aspect_ratio - config.elongated_aspect_ratio) / 4.0
        )
        confidence = 0.45 + 0.25 * margin + 0.10 * _clamp(candidate.mean_edge / 40.0)

    elif elongated and chromatic:
        basis = (
            f"the region is thin and stretched (aspect ratio "
            f"{candidate.aspect_ratio:.1f}) and carries a colour shift of "
            f"{candidate.mean_chroma:.1f}, consistent with paint scraped along a "
            "line"
        )
        label = DamageType.SCRATCH
        confidence = 0.42 + 0.18 * _clamp(candidate.mean_chroma / 40.0)

    elif bloblike and _is_warm_shift(candidate, config):
        basis = (
            f"the region shifted towards red and yellow in LAB "
            f"(delta a {candidate.mean_delta_a:+.1f}, delta b "
            f"{candidate.mean_delta_b:+.1f}) over a compact area, the colour "
            "signature of oxidation"
        )
        label = DamageType.RUST
        confidence = 0.40 + 0.25 * _clamp(candidate.mean_chroma / 45.0)

    elif bloblike and chromatic and candidate.mean_delta_l > 6.0:
        basis = (
            f"a compact region became noticeably brighter "
            f"(delta L {candidate.mean_delta_l:+.1f}) with a colour shift of "
            f"{candidate.mean_chroma:.1f}, consistent with bare substrate showing "
            "through paint"
        )
        label = DamageType.PAINT_CHIP
        confidence = 0.38 + 0.22 * _clamp(candidate.mean_delta_l / 40.0)

    elif (
        bloblike
        and not chromatic
        and candidate.mean_abs_delta_l >= config.significant_luminance_change
    ):
        basis = (
            f"a localised region changed brightness substantially (mean absolute "
            f"delta L {candidate.mean_abs_delta_l:.1f}) with no colour change "
            f"(chroma {candidate.mean_chroma:.1f}), which is how a depression "
            "reads: it alters how the panel catches light without altering the "
            f"paint. Bipolar shading scores {bipolarity:.2f}"
            + (
                ", meaning it brightens and darkens within the same region as a "
                "depression would"
                if bipolarity >= config.dent_bipolarity
                else ", which is lower than a textbook depression would give, so "
                "this label is weak"
            )
        )
        label = DamageType.DENT
        # Bipolarity raises confidence but does not gate the label: measured
        # dents do not always shade symmetrically, so requiring it would drop
        # real findings.
        confidence = (
            0.38
            + 0.18 * _clamp(candidate.mean_abs_delta_l / 45.0)
            + 0.16 * bipolarity
        )

    elif candidate.area_fraction >= 0.02 and candidate.mean_difference < 26.0:
        basis = (
            f"a large area ({candidate.area_fraction * 100:.1f}% of the frame) "
            "changed only weakly and diffusely, which is more consistent with "
            "surface contamination than with a discrete defect"
        )
        label = DamageType.DIRT_STAIN
        confidence = 0.34 + 0.16 * _clamp(candidate.area_fraction / 0.05)

    else:
        basis = (
            "the region changed significantly but its shape and colour statistics "
            f"(aspect {candidate.aspect_ratio:.1f}, chroma "
            f"{candidate.mean_chroma:.1f}) do not match any specific "
            "pattern the labeller recognises"
        )
        label = DamageType.OTHER
        # An explicit "I do not know" must not be dressed up as certainty.
        confidence = 0.20

    reasoning = (
        f"Labelled {label.value} because {basis}. This label is a heuristic "
        "reading of shape and colour statistics, not a classification by a "
        "trained model, and needs human confirmation."
    )
    capped = min(confidence, TYPE_CONFIDENCE_CEILING)
    return label, round(_clamp(capped, 0.05, 1.0), 3), reasoning


def assess(
    candidates: list[RegionCandidate],
    *,
    config: PipelineConfig,
    alignment_quality: float,
    viewpoint: Viewpoint,
    frame_width: int,
    frame_height: int,
) -> list[Finding]:
    """Score, label and map every candidate region into a :class:`Finding`.

    The presence reasoning and the label reasoning are concatenated on purpose:
    a reviewer reading one field sees both what was measured and how it was
    interpreted, and can accept one while rejecting the other.
    """
    findings: list[Finding] = []

    for candidate in candidates:
        confidence = score_confidence(
            candidate, alignment_quality=alignment_quality, config=config
        )
        damage_type, type_confidence, label_reasoning = classify(
            candidate, config=config
        )
        mapping = map_region(
            candidate.bbox,
            frame_width=frame_width,
            frame_height=frame_height,
            viewpoint=viewpoint,
        )
        severity = _severity_for(candidate)

        presence_reasoning = (
            f"Region flagged as a potential change: mean difference "
            f"{candidate.mean_difference:.1f} (threshold "
            f"{config.difference_threshold:.1f}), peak "
            f"{candidate.peak_difference:.1f}, covering "
            f"{candidate.area_fraction * 100:.2f}% of the frame"
            + (
                f" across {candidate.fragments} merged fragments"
                if candidate.fragments > 1
                else ""
            )
            + "."
        )
        if alignment_quality < config.min_alignment_quality:
            presence_reasoning += (
                " Alignment between the two photos was weak, so this finding may "
                "be a registration artefact rather than a real change."
            )

        findings.append(
            Finding(
                bounding_box=candidate.normalised_bbox,
                region=str(mapping["region"]),
                damage_type=str(damage_type),
                severity=str(severity),
                confidence=confidence,
                type_confidence=type_confidence,
                reasoning=f"{presence_reasoning} {label_reasoning} {mapping['basis']}",
                pixel_area=candidate.area,
                statistics=candidate.describe(),
                region_mapping=mapping,
                method=str(DetectionMethod.CLASSICAL_CV),
                model_version=None,
            )
        )

    # Strongest first: this ordering is what the reviewer works down.
    findings.sort(key=lambda item: item.confidence, reverse=True)
    return findings


__all__ = [
    "REFERENCE_AREA_FRACTION",
    "TYPE_CONFIDENCE_CEILING",
    "Finding",
    "assess",
    "classify",
    "score_confidence",
]
