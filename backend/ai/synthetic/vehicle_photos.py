"""Synthetic vehicle photographs, for demos and tests.

The platform has no vehicle cameras, no labelled damage dataset, and no
photograph library committed to git. Rather than ship binary assets or fake
results, this module *draws* photographs with OpenCV and then runs the real
pipeline over them.

**This is the honest version of a demo mode.** The input images are synthetic
and say so. The findings are not: they are produced by the same
``ai.vision.compare_images`` the production path uses, on pixels that genuinely
differ. A scratch drawn at a known location either is or is not detected, so the
demo doubles as a regression test. Nothing here inserts a canned finding, and
nothing here bypasses a threshold.

What the images are: a stylised body panel -- paint gradient, panel gaps, a
window, a wheel arch -- with enough high-frequency texture for ORB to find
features. They are not photorealistic, and the report page labels them as
synthetic so no one mistakes them for a real car.

What the demo proves: that the pipeline detects a change it is pointed at,
ignores an image pair that has not changed, tolerates a camera that moved
between shots, and does not report a purely lighting change as damage. Those are
the behaviours worth demonstrating, and they are testable without a car.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

#: Panel size for generated photos. Large enough that the pipeline's default
#: working resolution does not upscale, small enough to stay fast in tests.
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 640

#: Paint colours in BGR, per scenario, so demos are visually distinguishable.
PAINT_COLOURS: dict[str, tuple[int, int, int]] = {
    "silver": (178, 178, 182),
    "midnight": (58, 40, 30),
    "racing_red": (44, 44, 168),
    "pearl_white": (232, 232, 236),
}


@dataclass(frozen=True, slots=True)
class SyntheticDefect:
    """A defect to draw, in normalised frame coordinates."""

    kind: str
    #: Centre as fractions of ``(width, height)``.
    centre: tuple[float, float]
    #: Extent as fractions of ``(width, height)``.
    extent: tuple[float, float]
    #: The region the grid map *should* produce, so tests can assert on it.
    expected_region: str | None = None
    #: What the labeller is expected to guess. Advisory only -- the labeller is
    #: a heuristic and tests assert on detection, not on the label.
    expected_type: str | None = None


@dataclass(frozen=True, slots=True)
class SyntheticPair:
    """A matched drop-off/pickup image pair and the ground truth behind it."""

    drop_off_png: bytes
    pickup_png: bytes
    defects: tuple[SyntheticDefect, ...]
    angle: str
    width: int
    height: int
    #: True when the pickup frame was shifted, so a demo can show alignment
    #: doing real work rather than comparing two identical bitmaps.
    camera_jitter: bool
    #: Global brightness multiplier applied to the pickup frame.
    lighting_shift: float
    paint: str
    label: str
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """Ground truth, for the demo page to show what was planted."""
        return {
            "label": self.label,
            "angle": self.angle,
            "paint": self.paint,
            "synthetic": True,
            "camera_jitter": self.camera_jitter,
            "lighting_shift": self.lighting_shift,
            "planted_defects": [
                {
                    "kind": defect.kind,
                    "centre": defect.centre,
                    "expected_region": defect.expected_region,
                    "expected_type": defect.expected_type,
                }
                for defect in self.defects
            ],
        }


def encode_png(image: np.ndarray) -> bytes:
    """Encode a BGR array to PNG bytes, raising on failure."""
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Could not encode the synthetic image as PNG.")
    return buffer.tobytes()


def _texture(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """High-frequency speckle.

    Not decoration: ORB needs local contrast to find features, and a flat colour
    field would make alignment impossible. Real paint has micro-texture from
    clear coat and reflected environment; this stands in for it.
    """
    noise = rng.normal(0.0, 4.5, size=(height, width)).astype(np.float32)
    # GaussianBlur drops the trailing axis on single-channel input, so the blur
    # is done in 2D and broadcast explicitly rather than assuming the shape
    # survives.
    smoothed = cv2.GaussianBlur(noise, (3, 3), 0)
    return np.repeat(smoothed[:, :, None], 3, axis=2)


def _paint_gradient(
    width: int, height: int, colour: tuple[int, int, int]
) -> np.ndarray:
    """A vertical gradient, standing in for sky reflection on a body panel."""
    ramp = np.linspace(0.82, 1.16, height, dtype=np.float32)[:, None, None]
    base = np.full((height, width, 3), colour, dtype=np.float32)
    return np.clip(base * ramp, 0, 255)


def _draw_panel_furniture(image: np.ndarray) -> np.ndarray:
    """Panel gaps, a window and a wheel arch.

    These give the scene structure and, more importantly, strong edges that the
    gradient channel can lock onto -- the same features a real photograph offers.
    """
    height, width = image.shape[:2]
    drawn = image.copy()

    # Panel gap: a dark vertical seam.
    seam_x = int(width * 0.42)
    cv2.line(drawn, (seam_x, 0), (seam_x, height), (70, 70, 72), 3)
    cv2.line(drawn, (seam_x + 3, 0), (seam_x + 3, height), (120, 120, 122), 1)

    # Window: a darker rectangle with a highlight edge.
    window = (
        int(width * 0.48),
        int(height * 0.08),
        int(width * 0.90),
        int(height * 0.38),
    )
    cv2.rectangle(drawn, window[:2], window[2:], (96, 84, 68), -1)
    cv2.rectangle(drawn, window[:2], window[2:], (150, 150, 150), 2)

    # Wheel arch: a filled dark circle at the lower left.
    centre = (int(width * 0.18), int(height * 0.86))
    radius = int(min(width, height) * 0.16)
    cv2.circle(drawn, centre, radius, (40, 40, 42), -1)
    cv2.circle(drawn, centre, int(radius * 0.55), (110, 110, 114), -1)
    cv2.circle(drawn, centre, radius, (24, 24, 26), 3)

    # Door handle: a small bright bar, a useful landmark for alignment.
    cv2.rectangle(
        drawn,
        (int(width * 0.60), int(height * 0.50)),
        (int(width * 0.72), int(height * 0.545)),
        (205, 205, 208),
        -1,
    )
    return drawn


def render_panel(
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    paint: str = "silver",
    seed: int = 1234,
) -> np.ndarray:
    """A clean synthetic body panel."""
    rng = np.random.default_rng(seed)
    colour = PAINT_COLOURS.get(paint, PAINT_COLOURS["silver"])

    image = _paint_gradient(width, height, colour) + _texture(width, height, rng)
    image = np.clip(image, 0, 255).astype(np.uint8)
    return _draw_panel_furniture(image)


def _pixel_point(
    centre: tuple[float, float], extent: tuple[float, float], width: int, height: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Convert normalised centre/extent into pixel centre and half-extent."""
    cx = int(centre[0] * width)
    cy = int(centre[1] * height)
    half_w = max(2, int(extent[0] * width / 2))
    half_h = max(2, int(extent[1] * height / 2))
    return (cx, cy), (half_w, half_h)


def apply_defect(image: np.ndarray, defect: SyntheticDefect) -> np.ndarray:
    """Draw one defect onto a copy of ``image``."""
    height, width = image.shape[:2]
    drawn = image.copy()
    (cx, cy), (half_w, half_h) = _pixel_point(
        defect.centre, defect.extent, width, height
    )

    if defect.kind == "scratch":
        # A thin bright line with a dark companion edge: a real scratch scatters
        # light along one side and shadows the other.
        start = (cx - half_w, cy + half_h // 3)
        end = (cx + half_w, cy - half_h // 3)
        cv2.line(drawn, start, end, (228, 228, 232), 3, cv2.LINE_AA)
        cv2.line(
            drawn,
            (start[0], start[1] + 3),
            (end[0], end[1] + 3),
            (52, 52, 56),
            2,
            cv2.LINE_AA,
        )

    elif defect.kind == "dent":
        # A depression: shading only. The paint is *scaled* rather than
        # overwritten with grey, because a dent does not change a panel's colour
        # -- it changes how the panel catches light. An earlier version painted a
        # grey ellipse, which on coloured paint produced an enormous chroma shift
        # and made the demo's dent read as a paint chip.
        mask = np.zeros((height, width), dtype=np.float32)
        cv2.ellipse(
            mask, (cx, cy), (half_w, half_h), 0, 0, 360, 1.0, -1, cv2.LINE_AA
        )
        cv2.ellipse(
            mask,
            (cx, cy - max(2, half_h // 4)),
            (max(1, int(half_w * 0.8)), max(1, int(half_h * 0.7))),
            0,
            0,
            360,
            -0.55,
            -1,
            cv2.LINE_AA,
        )
        mask = cv2.GaussianBlur(mask, (0, 0), 3.0)[:, :, None]
        drawn = np.clip(
            drawn.astype(np.float32) * (1.0 + 0.42 * mask), 0, 255
        ).astype(np.uint8)

    elif defect.kind == "rust":
        # Warm, mottled, irregular: oxidised metal reads as a red/yellow shift.
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.ellipse(
            mask, (cx, cy), (half_w, half_h), 12, 0, 360, 255, -1, cv2.LINE_AA
        )
        rng = np.random.default_rng(abs(hash(defect.kind)) % 10_000)
        mottle = rng.normal(0, 16, size=(height, width, 1))
        patch = np.zeros_like(drawn, dtype=np.float32)
        patch[:, :, 1] = 74.0  # +green
        patch[:, :, 2] = 128.0  # +red, dominant: rust is warm
        patch[:, :, 0] = 46.0
        patch = np.clip(patch + mottle, 0, 255)
        blended = drawn.astype(np.float32) * 0.30 + patch * 0.70
        selected = mask.astype(bool)[:, :, None]
        drawn = np.where(selected, blended, drawn.astype(np.float32)).astype(np.uint8)

    elif defect.kind == "paint_chip":
        # Small, bright, compact: bare primer showing through.
        radius = max(4, half_w // 2)
        cv2.circle(drawn, (cx, cy), radius, (214, 212, 206), -1, cv2.LINE_AA)
        cv2.circle(drawn, (cx, cy), radius, (88, 86, 84), 2, cv2.LINE_AA)

    elif defect.kind == "dirt":
        # Large, soft, low contrast: the case the diffuse filter must reject or
        # label as contamination rather than damage.
        overlay = drawn.astype(np.float32).copy()
        cv2.ellipse(
            overlay,
            (cx, cy),
            (half_w, half_h),
            0,
            0,
            360,
            (96, 92, 84),
            -1,
            cv2.LINE_AA,
        )
        drawn = cv2.addWeighted(overlay, 0.35, drawn.astype(np.float32), 0.65, 0).astype(
            np.uint8
        )

    else:
        raise ValueError(f"Unknown synthetic defect kind: {defect.kind!r}")

    return drawn


def _relight(image: np.ndarray, brightness: float) -> np.ndarray:
    """Simulate a change in exposure between the two shots."""
    if abs(brightness - 1.0) < 1e-6:
        return image
    return np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)


def _move_camera(
    image: np.ndarray,
    *,
    shift_pixels: int = 9,
    rotation_degrees: float = 1.4,
    seed: int = 99,
) -> np.ndarray:
    """Simulate a photographer who stood somewhere slightly different."""
    rng = np.random.default_rng(seed)
    height, width = image.shape[:2]

    centre = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(centre, rotation_degrees, 1.0)
    matrix[0, 2] += rng.uniform(-shift_pixels, shift_pixels)
    matrix[1, 2] += rng.uniform(-shift_pixels, shift_pixels)

    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def generate_pair(
    *,
    defects: tuple[SyntheticDefect, ...] = (),
    angle: str = "left_side",
    paint: str = "silver",
    seed: int = 1234,
    camera_jitter: bool = True,
    lighting_shift: float = 1.0,
    label: str = "synthetic",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> SyntheticPair:
    """Render a matched drop-off/pickup pair with the given defects planted.

    With no defects and ``camera_jitter=False`` the two images are byte-identical
    apart from encoding, which is the "same image" case: the pipeline must report
    nothing.
    """
    clean = render_panel(width=width, height=height, paint=paint, seed=seed)

    damaged = clean
    for defect in defects:
        damaged = apply_defect(damaged, defect)

    # Exposure and camera movement are applied independently. Bundling them
    # would make the "lighting change only" scenario move the camera too, which
    # quietly turns a test of the exposure correction into a test of alignment.
    pickup = _relight(damaged, lighting_shift)
    if camera_jitter:
        pickup = _move_camera(pickup, seed=seed + 7)

    return SyntheticPair(
        drop_off_png=encode_png(clean),
        pickup_png=encode_png(pickup),
        defects=defects,
        angle=angle,
        width=width,
        height=height,
        camera_jitter=camera_jitter,
        lighting_shift=lighting_shift,
        paint=paint,
        label=label,
    )


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

#: Named scenarios the demo endpoint can run. Each one exercises a different
#: part of the pipeline, which is what makes the set worth having.
DEMO_SCENARIOS: dict[str, dict[str, Any]] = {
    "clean": {
        "label": "No damage - identical conditions",
        "description": (
            "The same panel photographed twice with no change. The pipeline must "
            "report nothing; if it reports something here, it is producing false "
            "positives."
        ),
        "defects": (),
        "paint": "silver",
        "angle": "left_side",
        "camera_jitter": False,
    },
    "lighting_only": {
        "label": "Lighting change only - no damage",
        "description": (
            "The pickup photo is 18% brighter, as if a cloud moved. Nothing "
            "changed on the vehicle. This is the scenario the lighting "
            "normalisation and diffuse-region filters exist to survive."
        ),
        "defects": (),
        "paint": "silver",
        "angle": "left_side",
        "camera_jitter": False,
        "lighting_shift": 1.18,
    },
    "scratch": {
        "label": "Scratch along the front door",
        "description": (
            "A thin scuff drawn along the lower door. Exercises the elongated, "
            "edge-dominant branch of the labeller."
        ),
        "defects": (
            SyntheticDefect(
                kind="scratch",
                centre=(0.62, 0.66),
                extent=(0.24, 0.03),
                expected_region="LEFT_FRONT_DOOR",
                expected_type="SCRATCH",
            ),
        ),
        "paint": "midnight",
        "angle": "left_side",
    },
    "dent": {
        "label": "Dent on the front wing",
        "description": (
            "A shading depression with no colour change. Exercises the "
            "dark-and-achromatic branch."
        ),
        "defects": (
            SyntheticDefect(
                kind="dent",
                centre=(0.80, 0.62),
                extent=(0.13, 0.15),
                expected_region="FRONT_BUMPER",
                expected_type="DENT",
            ),
        ),
        "paint": "pearl_white",
        "angle": "right_side",
    },
    "rust": {
        "label": "Rust patch on the lower wing",
        "description": (
            "A warm, mottled colour shift. Exercises the chroma channel, which is "
            "the only one that can see this at all."
        ),
        "defects": (
            SyntheticDefect(
                kind="rust",
                centre=(0.18, 0.80),
                extent=(0.14, 0.13),
                expected_region="LEFT_FENDER",
                expected_type="RUST",
            ),
        ),
        "paint": "silver",
        "angle": "left_side",
    },
    "multiple": {
        "label": "Three defects, camera moved",
        "description": (
            "A scratch, a dent and a paint chip, with the pickup frame shifted and "
            "rotated so alignment has to do real work. The fullest demonstration "
            "of the pipeline."
        ),
        "defects": (
            SyntheticDefect(
                kind="scratch",
                centre=(0.58, 0.60),
                extent=(0.20, 0.025),
                expected_region="LEFT_FRONT_DOOR",
                expected_type="SCRATCH",
            ),
            SyntheticDefect(
                kind="dent",
                centre=(0.83, 0.70),
                extent=(0.11, 0.12),
                expected_region="WHEELS",
                expected_type="DENT",
            ),
            SyntheticDefect(
                kind="paint_chip",
                centre=(0.30, 0.42),
                extent=(0.09, 0.09),
                expected_region="FRONT_BUMPER",
                expected_type="PAINT_CHIP",
            ),
        ),
        "paint": "racing_red",
        "angle": "left_side",
    },
}


def scenario_names() -> list[str]:
    """Every demo scenario key, in catalogue order."""
    return list(DEMO_SCENARIOS)


def build_scenario(name: str) -> SyntheticPair:
    """Build a :class:`SyntheticPair` from a named demo scenario."""
    if name not in DEMO_SCENARIOS:
        raise KeyError(f"Unknown demo scenario: {name!r}")

    spec = DEMO_SCENARIOS[name]
    return generate_pair(
        defects=tuple(spec.get("defects", ())),
        angle=spec.get("angle", "left_side"),
        paint=spec.get("paint", "silver"),
        seed=spec.get("seed", 20260907),
        camera_jitter=spec.get("camera_jitter", True),
        lighting_shift=spec.get("lighting_shift", 1.0),
        label=spec["label"],
    )


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "DEMO_SCENARIOS",
    "PAINT_COLOURS",
    "SyntheticDefect",
    "SyntheticPair",
    "apply_defect",
    "build_scenario",
    "encode_png",
    "generate_pair",
    "render_panel",
    "scenario_names",
]
