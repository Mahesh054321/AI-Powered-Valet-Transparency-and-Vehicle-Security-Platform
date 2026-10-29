"""Steps 1-5 of the comparison pipeline: load, validate, resize, normalise, colour.

Two decisions here shape everything downstream.

**Fixed working resolution.** Images are resized to a common longest edge
before any measurement. A scratch is a physical thing, but its *pixel* area
depends entirely on the camera, and the thresholds in :mod:`ai.vision.config`
are expressed in pixels. Without this step the same damage would score
differently on a flagship phone and a cheap webcam.

**EXIF orientation is applied, and has to be applied by hand.** Phone cameras
store the sensor's native landscape pixels plus a rotation tag. ``cv2.imread``
honours that tag, but ``cv2.imdecode`` -- which is what an upload gives us --
does not. Skipping it would compare a portrait drop-off photo against a rotated
pickup photo of the same panel and report the entire vehicle as damaged.

Nothing in this module invents content: it resamples and re-expresses what the
camera recorded.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from ai.vision.config import PipelineConfig
from utils.exceptions import ValidationError
from utils.image_probe import probe_image_size

#: OpenCV reads BGR; PIL is RGB. Getting this backwards would swap the a and b
#: chroma axes and make rust look blue.
_PIL_TO_OPENCV = (2, 1, 0)


class ImageNotDecodable(ValidationError):
    """Raised when bytes cannot be turned into an image at all."""


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """An image ready for comparison, with the provenance that explains it."""

    #: BGR, uint8, at working resolution.
    image: np.ndarray
    width: int
    height: int
    #: True when an EXIF tag caused a rotation. Recorded because it explains why
    #: the stored bytes and the compared pixels are not oriented the same way.
    orientation_applied: bool
    #: Original pixel dimensions before resizing.
    source_width: int
    source_height: int

    def describe(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "orientation_applied": self.orientation_applied,
        }


def decode(blob: bytes, *, field: str = "image") -> np.ndarray:
    """Turn raw bytes into a BGR array, or raise.

    ``cv2.imdecode`` returns ``None`` rather than throwing for anything it
    cannot parse, so the ``None`` check is the actual validation -- without it
    the next line raises an unhelpful ``AttributeError``.
    """
    # Header check BEFORE decoding. cv2.imdecode allocates the buffer implied
    # by the header before reading a single pixel, so a file claiming absurd
    # dimensions kills the process. See utils.image_probe for the details.
    probe_image_size(blob, field=field)

    buffer = np.frombuffer(blob, dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if decoded is None:
        raise ImageNotDecodable(
            "The image could not be decoded. It may be truncated, or not a "
            "format OpenCV supports (JPEG, PNG and WebP are).",
            details={"field": field},
        )
    return decoded


def _apply_exif_orientation(blob: bytes, image: np.ndarray) -> tuple[np.ndarray, bool]:
    """Rotate the pixels to match the camera's EXIF orientation tag.

    Falls back to the untouched array if Pillow cannot read the tag: an image
    with no orientation tag is already correctly oriented, and a corrupt tag is
    not a reason to reject an otherwise usable photo.
    """
    try:
        with Image.open(io.BytesIO(blob)) as opened:
            exif = opened.getexif()
            orientation = exif.get(0x0112) if exif else None
            if orientation in (None, 1):
                return image, False
            corrected = ImageOps.exif_transpose(opened)
            if corrected is None:
                return image, False
    except Exception:  # noqa: BLE001 - a bad tag must not reject the photo
        return image, False

    rgb = np.asarray(corrected.convert("RGB"))
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        return image, False
    return np.ascontiguousarray(rgb[:, :, _PIL_TO_OPENCV]), True


def resize_to_working_size(image: np.ndarray, working_width: int) -> np.ndarray:
    """Scale so the longest edge is ``working_width``, preserving aspect ratio.

    Images already at or below that size are left alone: upscaling a small
    photo manufactures detail that the differencing step would then read as
    real.
    """
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= working_width:
        return image

    scale = working_width / longest
    target = (
        max(1, round(width * scale)),
        max(1, round(height * scale)),
    )
    # INTER_AREA for downscaling only; INTER_LINEAR upscales more pleasantly,
    # but this branch never upscales.
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA)


def load_image(blob: bytes, *, config: PipelineConfig, field: str = "image") -> PreparedImage:
    """Steps 1-3: decode, validate dimensions, resize.

    Raises ``ValidationError`` (HTTP 422) rather than returning ``None``: a
    caller that forgets to check a ``None`` would produce a confusing crash
    three modules later, and an unusable photo is a client error worth
    reporting precisely.
    """
    image = decode(blob, field=field)
    source_height, source_width = image.shape[:2]

    if min(source_width, source_height) < config.min_image_dimension:
        raise ValidationError(
            f"The image is too small to assess: {source_width}x{source_height} "
            f"pixels. At least {config.min_image_dimension}x"
            f"{config.min_image_dimension} is required, because difference "
            "regions at this size are indistinguishable from sensor noise.",
            details={
                "field": field,
                "width": source_width,
                "height": source_height,
                "min_dimension": config.min_image_dimension,
            },
        )

    image, orientation_applied = _apply_exif_orientation(blob, image)
    # Orientation can swap width and height, so measure again afterwards.
    source_height, source_width = image.shape[:2]
    resized = resize_to_working_size(image, config.working_width)
    height, width = resized.shape[:2]

    return PreparedImage(
        image=resized,
        width=int(width),
        height=int(height),
        orientation_applied=orientation_applied,
        source_width=int(source_width),
        source_height=int(source_height),
    )


def to_lab(image: np.ndarray) -> np.ndarray:
    """Step 5: convert to CIELAB as ``float32`` with a and b centred on zero.

    LAB is used because it separates lightness from colour the way human vision
    roughly does. That split is what lets the pipeline treat "this got darker"
    and "this changed colour" as different evidence, which in turn is what
    distinguishes a dent from a patch of rust.

    The ``-128`` centring matters: in OpenCV's 8-bit LAB the chroma axes sit at
    128, so raw values are all positive and a Euclidean distance between them
    would be dominated by that offset.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 1:] -= 128.0
    return lab


def estimate_luminance_gain(
    lab_before: np.ndarray,
    lab_after: np.ndarray,
    *,
    config: PipelineConfig,
) -> float:
    """Step 4: the exposure ratio between the two frames, as a multiplier.

    A camera exposing differently multiplies the whole scene; it does not add a
    constant. Correcting with a multiplier rather than an offset is what lets
    one number undo the change at every brightness level -- an additive shift
    fixes the midtones and leaves the highlights mismatched, which then shows up
    as a wall of false positives on the brightest panels.

    Estimated from the *median* lightness of each frame rather than the mean.
    The median is deliberately robust: if the pickup photo genuinely contains a
    large dark dent, the mean would be dragged down by it and the correction
    would partially erase the very thing being looked for.

    The correction is partial by design (see
    ``config.lighting_correction_strength``): equalising exposure completely
    would also remove the shading a real dent creates.
    """
    floor = config.luminance_floor
    before_median = float(np.median(lab_before[:, :, 0])) + floor
    after_median = float(np.median(lab_after[:, :, 0])) + floor

    raw_gain = before_median / max(after_median, 1e-6)

    # Damp towards 1.0, then cap, so a badly exposed frame is not stretched into
    # something that no longer resembles the original.
    damped = 1.0 + (raw_gain - 1.0) * config.lighting_correction_strength
    limit = config.max_gain_deviation
    return float(np.clip(damped, 1.0 - limit, 1.0 + limit))


def correct_luminance(lab: np.ndarray, gain: float) -> np.ndarray:
    """Scale the lightness channel by ``gain``, clipped to the valid range."""
    if abs(gain - 1.0) < 1e-4:
        return lab
    corrected = lab.copy()
    corrected[:, :, 0] = np.clip(corrected[:, :, 0] * gain, 0.0, 255.0)
    return corrected


def grayscale(image: np.ndarray) -> np.ndarray:
    """Weighted luminance, for the gradient channel."""
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)


__all__ = [
    "ImageNotDecodable",
    "PreparedImage",
    "correct_luminance",
    "decode",
    "estimate_luminance_gain",
    "grayscale",
    "load_image",
    "resize_to_working_size",
    "to_lab",
]
