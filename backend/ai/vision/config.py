"""Thresholds for the condition-comparison pipeline.

Every number here is a plain, human-readable constant. That is the point: the
platform has no trained model, so the entire behaviour of the detector is these
values plus the code in this package, and a reviewer must be able to read them.

Each :class:`PipelineConfig` instance is serialised into
``damage_detections.algorithm_params`` alongside the finding it produced, so
any finding in the database can be reproduced by re-running the pipeline with
the stored parameters. Changing a threshold here therefore never silently
reinterprets history.

These values were chosen by inspection on the synthetic demo photographs and a
handful of ordinary car photos. They are *not* tuned against a labelled dataset
of real damage, because no such dataset exists in this project, and they will
need adjusting for a real fleet. See ``docs/AI-INTEGRITY.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Longest edge images are resized to before comparison. Working at a fixed
#: resolution is what makes the area and threshold constants below meaningful:
#: without it, the same scratch would measure differently in a 4K phone photo
#: and a 640px webcam grab.
DEFAULT_WORKING_WIDTH = 1024

#: Below this, differencing is meaningless - contour areas collapse to a few
#: pixels and the detector would be reading noise.
MIN_IMAGE_DIMENSION = 96


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """The complete parameter set for one comparison run."""

    # -- preprocessing ------------------------------------------------------
    working_width: int = DEFAULT_WORKING_WIDTH
    min_image_dimension: int = MIN_IMAGE_DIMENSION

    # -- alignment ----------------------------------------------------------
    #: ORB features requested per image. More is slower but aligns better on
    #: texture-poor panels.
    orb_features: int = 2000
    #: Lowe's ratio test. Lower is stricter, so fewer but better matches.
    match_ratio: float = 0.75
    #: RANSAC reprojection tolerance in pixels, at working resolution.
    ransac_threshold: float = 4.0
    #: Fewer matches than this and alignment is abandoned: warping on a
    #: handful of accidental matches is worse than not warping at all.
    min_matches: int = 12
    #: Below this inlier fraction the warp is distrusted and every finding
    #: from the run is discounted and carries a warning.
    min_alignment_quality: float = 0.25

    # -- lighting normalisation --------------------------------------------
    #: How much of the measured exposure ratio to correct for. Less than 1.0 on
    #: purpose: fully equalising brightness would also flatten a genuine shadow
    #: cast by a new dent. 0.85 corrects most of an exposure change while
    #: leaving real shading intact.
    lighting_correction_strength: float = 0.85
    #: Hard cap on the gain correction as a deviation from 1.0, so a badly
    #: exposed photo cannot be stretched into something unrecognisable.
    max_gain_deviation: float = 0.45
    #: Lightness floor used when computing the exposure ratio. Without it a dark
    #: frame would divide by a near-zero median and produce an enormous gain.
    luminance_floor: float = 8.0

    # -- differencing -------------------------------------------------------
    #: Weights for the three evidence channels. They need not sum to 1; the
    #: combined map is normalised afterwards.
    luminance_weight: float = 1.0
    chroma_weight: float = 1.0
    edge_weight: float = 0.8
    #: The edge channel measures how much gradient *increased*, as a ratio over
    #: the gradient already present. This multiple saturates the channel: a
    #: tripling of local contrast scores 1.0.
    #:
    #: Measuring the increase rather than the absolute difference is what stops
    #: the channel from saturating at every pre-existing edge. A panel seam that
    #: shifts by a pixel has almost the same gradient before and after, so it
    #: scores near zero; a scratch across smooth paint has none before and a
    #: strong one after, so it scores high. An absolute difference scores both
    #: identically, which is a false-positive factory.
    new_edge_saturation: float = 2.0
    #: Percentile of the drop-off gradient used as the denominator floor, so
    #: flat regions do not divide by zero and amplify sensor noise.
    edge_floor_percentile: float = 99.0
    #: Absolute floor on the gradient denominator, in Sobel magnitude units.
    edge_floor_minimum: float = 3.0
    #: Gaussian blur applied to the difference map before thresholding. This is
    #: the first false-positive defence: isolated hot pixels from sensor noise
    #: cannot survive it.
    difference_blur_kernel: int = 5
    #: Sobel aperture for the gradient channel.
    sobel_kernel: int = 3

    # -- region extraction --------------------------------------------------
    #: Mean combined difference, on the 0-255 working scale, above which a pixel
    #: counts as changed.
    difference_threshold: float = 22.0
    #: A region must cover at least this fraction of the frame.
    min_region_area_fraction: float = 0.0006
    #: Morphological opening kernel; removes salt-and-pepper speckle.
    morph_kernel_size: int = 5
    #: Overlapping boxes with an intersection-over-union above this are merged,
    #: so one long scratch is not reported as four segments.
    merge_iou: float = 0.30
    #: Border margin excluded from detection. Alignment warping invents content
    #: at the frame edges, and edges are where false positives concentrate.
    border_margin_fraction: float = 0.015
    #: Regions wider than this fraction of the frame with a weak mean signal are
    #: treated as illumination change, not damage.
    diffuse_region_fraction: float = 0.35
    #: Mean difference below which a very large region is dismissed as diffuse
    #: lighting rather than a defect.
    diffuse_mean_ceiling: float = 12.0
    #: Mean pre-existing gradient above which a region is treated as sitting on
    #: structure that was already there -- the panel seams, window lines and
    #: badge edges where misalignment shows up first.
    #:
    #: Measured on the demo photographs, genuine damage across smooth paint
    #: scores single digits here while registration residue on a panel seam
    #: scores 50-170, so there is a wide margin. The cost is explicit: damage
    #: lying directly on top of an existing edge can be suppressed.
    pre_existing_edge_ceiling: float = 40.0
    #: A region on a pre-existing edge is still kept if it carries this much
    #: chroma change relative to `significant_chroma`, because a colour change
    #: is not something a one-pixel misregistration can invent.
    residue_chroma_fraction: float = 0.5

    # -- confidence ---------------------------------------------------------
    #: Mean difference that maps to the top of the confidence range.
    saturation_difference: float = 70.0
    #: Multiplier applied to confidence when the alignment was poor. Findings
    #: are kept rather than dropped - a reviewer can still look - but they are
    #: ranked lower and carry a warning.
    low_alignment_penalty: float = 0.6
    #: Floor, so a reported finding never claims zero confidence.
    min_confidence: float = 0.05

    # -- labelling ----------------------------------------------------------
    #: Aspect ratio above which a region is considered elongated (scratch-like).
    elongated_aspect_ratio: float = 3.0
    #: Aspect ratio below which a region is treated as blob-like rather than
    #: line-like. Used only to keep scratches out of the blob rules; solidity is
    #: deliberately not part of it, because a dent's signature is a ring.
    bloblike_aspect_ratio: float = 2.5
    #: Chroma distance above which a colour change is considered significant.
    significant_chroma: float = 14.0
    #: Mean absolute lightness change above which a region counts as having
    #: real shading structure rather than measurement noise.
    significant_luminance_change: float = 10.0
    #: Bipolarity above which a compact, achromatic region reads as a dent.
    #: Bipolarity is ``1 - |mean delta L| / mean |delta L|``: 1.0 means the
    #: region brightens and darkens equally, the signature of a depression
    #: catching light on one side and shadowing on the other.
    dent_bipolarity: float = 0.70

    #: Bumped whenever the algorithm changes in a way that alters output, so a
    #: stored ``algorithm_params`` can be told apart from a current run.
    version: str = "classical-cv/1.0.0"

    extra: dict[str, Any] = field(default_factory=dict)

    def as_params(self) -> dict[str, Any]:
        """Serialise for ``algorithm_params`` - reproducibility, not decoration."""
        payload = asdict(self)
        payload.pop("extra", None)
        payload.update(self.extra)
        return payload

    def with_overrides(self, **overrides: Any) -> PipelineConfig:
        """Return a copy with selected fields changed.

        Used by the tests and by any caller that wants to demonstrate how a
        different threshold changes the result - which is the honest way to
        show a heuristic's sensitivity.
        """
        current = asdict(self)
        unknown = set(overrides) - set(current)
        if unknown:
            raise ValueError(f"Unknown pipeline parameters: {', '.join(sorted(unknown))}")
        current.update(overrides)
        return PipelineConfig(**current)


#: The parameters every run uses unless the caller says otherwise.
DEFAULT_CONFIG = PipelineConfig()


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_WORKING_WIDTH",
    "MIN_IMAGE_DIMENSION",
    "PipelineConfig",
]
