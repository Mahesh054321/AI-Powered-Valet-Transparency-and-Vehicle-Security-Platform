"""Step 13: render the evidence a reviewer actually looks at.

A stored bounding box and a confidence number are not enough for a human to
judge a finding. Two images are produced per comparison:

* **the comparison composite** -- drop-off, aligned pickup, and the difference
  heatmap side by side, with every flagged region outlined on both. This is the
  image that makes or breaks the review: if the reviewer cannot see the change
  in it, the finding is not worth their time;
* **an evidence crop** per finding -- the same region zoomed from both photos,
  for the close look that decides whether it is a scratch or a shadow.

Both are rendered server-side with OpenCV's built-in Hershey fonts, so no font
files ship with the project and the output is identical on every platform.

The composite is labelled **POTENTIAL DAMAGE - NOT VERIFIED**. That wording is
not decoration: these images are generated the moment a pickup inspection is
captured, before any human has looked at them, and a reviewer who forgets that
will read a heuristic's guess as a fact.
"""

from __future__ import annotations

import cv2
import numpy as np

from ai.vision.compare import DifferenceMap
from ai.vision.damage import Finding

#: Panel height in the composite. Chosen so three panels side by side stay
#: readable in a browser without being a large file.
PANEL_HEIGHT = 420
HEADER_HEIGHT = 34
FOOTER_HEIGHT = 30
PANEL_GAP = 8

_COLOUR_DROP_OFF = (200, 200, 200)
_COLOUR_PICKUP = (120, 220, 120)
_COLOUR_HEAT = (120, 180, 255)
_BOX_COLOUR = (40, 40, 255)
_HEADER_BG = (30, 30, 30)
_FOOTER_BG = (24, 24, 24)
_TEXT_LIGHT = (235, 235, 235)


def encode_png(image: np.ndarray) -> bytes:
    """Encode a BGR array as PNG bytes.

    Raises rather than returning empty bytes: ``cv2.imencode`` signals failure
    through a boolean that is easy to ignore, and a silently empty evidence
    image would be stored as if it were real.
    """
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("OpenCV could not encode the rendered image as PNG.")
    return buffer.tobytes()


def _fit_to_height(image: np.ndarray, height: int) -> np.ndarray:
    """Scale to an exact height, preserving aspect ratio."""
    current = image.shape[0]
    if current == height:
        return image
    scale = height / max(1, current)
    width = max(1, round(image.shape[1] * scale))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(image, (width, height), interpolation=interpolation)


def _heatmap(difference: DifferenceMap, size: tuple[int, int]) -> np.ndarray:
    """The smoothed difference map as a colour heatmap at ``size``."""
    smoothed = difference.smoothed
    peak = float(smoothed.max())
    if peak <= 0:
        scaled = np.zeros_like(smoothed, dtype=np.uint8)
    else:
        scaled = np.clip(smoothed / peak * 255.0, 0, 255).astype(np.uint8)
    coloured = cv2.applyColorMap(scaled, cv2.COLORMAP_JET)
    return cv2.resize(coloured, size, interpolation=cv2.INTER_NEAREST)


def _panel(image: np.ndarray, title: str, colour: tuple[int, int, int]) -> np.ndarray:
    """One titled panel: dark header strip above the image."""
    width = image.shape[1]
    header = np.full((HEADER_HEIGHT, width, 3), _HEADER_BG, dtype=np.uint8)
    cv2.rectangle(header, (0, HEADER_HEIGHT - 3), (width, HEADER_HEIGHT), colour, -1)
    cv2.putText(
        header,
        title,
        (10, 23),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        colour,
        1,
        cv2.LINE_AA,
    )
    return np.vstack([header, image])


def _draw_boxes(
    image: np.ndarray,
    findings: list[Finding],
    *,
    with_labels: bool = True,
) -> np.ndarray:
    """Outline every finding, converting normalised boxes back to pixels."""
    annotated = image.copy()
    height, width = annotated.shape[:2]

    for index, finding in enumerate(findings, start=1):
        box = finding.bounding_box
        x = int(box["x"] * width)
        y = int(box["y"] * height)
        box_width = max(2, int(box["width"] * width))
        box_height = max(2, int(box["height"] * height))

        cv2.rectangle(annotated, (x, y), (x + box_width, y + box_height), _BOX_COLOUR, 2)

        if not with_labels:
            continue

        label = f"{index}"
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
        )
        label_y = max(0, y - 6)
        cv2.rectangle(
            annotated,
            (x, label_y - text_height - baseline),
            (x + text_width + 8, label_y + baseline),
            _BOX_COLOUR,
            -1,
        )
        cv2.putText(
            annotated,
            label,
            (x + 4, label_y - baseline),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    return annotated


def _footer(
    width: int, findings: list[Finding], warnings: list[str]
) -> np.ndarray:
    """A strip stating plainly what this image is and is not."""
    footer = np.full((FOOTER_HEIGHT, width, 3), _FOOTER_BG, dtype=np.uint8)

    if findings:
        text = (
            f"POTENTIAL DAMAGE - NOT VERIFIED  |  {len(findings)} region(s) flagged "
            "by classical OpenCV differencing"
        )
        colour = (60, 200, 255)
    else:
        text = "No significant difference detected - not a guarantee of no damage"
        colour = (150, 210, 150)

    cv2.putText(
        footer, text, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA
    )
    if warnings:
        cv2.putText(
            footer,
            f"! {warnings[0][:70]}",
            (10, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (80, 180, 255),
            1,
            cv2.LINE_AA,
        )
        # The warning goes on the right when there is room, otherwise it would
        # overwrite the summary.
    return footer


def render_comparison(
    before: np.ndarray,
    after: np.ndarray,
    difference: DifferenceMap,
    findings: list[Finding],
    *,
    warnings: list[str] | None = None,
) -> bytes:
    """The three-panel before/after/difference composite, as PNG bytes."""
    left = _fit_to_height(before, PANEL_HEIGHT)
    middle = _fit_to_height(after, PANEL_HEIGHT)

    left_panel = _panel(_draw_boxes(left, findings), "DROP-OFF", _COLOUR_DROP_OFF)
    middle_panel = _panel(
        _draw_boxes(middle, findings), "PICKUP (aligned)", _COLOUR_PICKUP
    )

    heat = _heatmap(difference, (middle.shape[1], PANEL_HEIGHT))
    right_panel = _panel(_draw_boxes(heat, findings), "DIFFERENCE", _COLOUR_HEAT)

    gap = np.full((PANEL_HEIGHT + HEADER_HEIGHT, PANEL_GAP, 3), 60, dtype=np.uint8)
    body = np.hstack([left_panel, gap, middle_panel, gap, right_panel])

    width = body.shape[1]
    footer = _footer(width, findings, warnings or [])
    return encode_png(np.vstack([body, footer]))


def render_overlay(
    after: np.ndarray,
    difference: DifferenceMap,
    findings: list[Finding],
    *,
    alpha: float = 0.52,
) -> bytes:
    """Render a difference heatmap overlay blended onto the aligned pickup frame."""
    h, w = after.shape[:2]
    heat = _heatmap(difference, (w, h))
    blended = cv2.addWeighted(after, 1.0 - alpha, heat, alpha, 0)
    annotated = _draw_boxes(blended, findings, with_labels=True)
    return encode_png(annotated)


def render_evidence(
    before: np.ndarray,
    after: np.ndarray,
    finding: Finding,
    *,
    padding: float = 0.6,
) -> bytes:
    """A zoomed before/after crop around one finding, as PNG bytes.

    ``padding`` expands the crop beyond the bounding box. A scratch viewed at
    exactly its own bounds is two pixels of context and tells a reviewer
    nothing; the surrounding panel is what makes a defect recognisable.
    """
    box = finding.bounding_box
    height, width = after.shape[:2]

    x = box["x"] * width
    y = box["y"] * height
    box_width = box["width"] * width
    box_height = box["height"] * height

    pad_x = box_width * padding
    pad_y = box_height * padding

    left = max(0, int(x - pad_x))
    top = max(0, int(y - pad_y))
    right = min(width, int(x + box_width + pad_x))
    bottom = min(height, int(y + box_height + pad_y))

    # Guard against a degenerate box collapsing the crop to nothing.
    if right - left < 8 or bottom - top < 8:
        right = min(width, left + 8)
        bottom = min(height, top + 8)

    crop_before = _draw_boxes(before[top:bottom, left:right], [finding])
    crop_after = _draw_boxes(after[top:bottom, left:right], [finding])

    crop_before = _fit_to_height(crop_before, 260)
    crop_after = _fit_to_height(crop_after, 260)

    left_panel = _panel(crop_before, "DROP-OFF (detail)", _COLOUR_DROP_OFF)
    right_panel = _panel(crop_after, "PICKUP (detail)", _COLOUR_PICKUP)

    gap = np.full((left_panel.shape[0], PANEL_GAP, 3), 60, dtype=np.uint8)
    body = np.hstack([left_panel, gap, right_panel])

    footer = _footer(
        body.shape[1],
        [finding],
        [f"{finding.damage_type} - confidence {finding.confidence:.2f}"],
    )
    return encode_png(np.vstack([body, footer]))


def render_dropoff_sheet(before: np.ndarray, *, angle: str) -> bytes:
    """A single annotated panel, used for drop-off sets with nothing to compare.

    Drop-off capture has no "after" yet, but the report page still needs a
    consistent thumbnail, and the angle label belongs on the image itself so a
    reviewer flipping through photos cannot lose track of which end of the car
    they are looking at.
    """
    fitted = _fit_to_height(before, PANEL_HEIGHT)
    panel = _panel(fitted, f"DROP-OFF - {angle.upper()}", _COLOUR_DROP_OFF)
    footer = _footer(panel.shape[1], [], ["Baseline capture - nothing compared yet"])
    return encode_png(np.vstack([panel, footer]))


__all__ = [
    "encode_png",
    "render_comparison",
    "render_dropoff_sheet",
    "render_evidence",
    "render_overlay",
]
