"""Mapping a changed pixel region onto a named part of the vehicle.

The pipeline knows *where in the frame* something changed. Turning that into
"left front door" needs an assumption about what the camera was pointing at, and
that assumption has to be stated rather than buried.

**What this is.** A coarse 3x3 grid over the frame, selected by the ``angle``
label captured with the photo. It is not anatomy, not segmentation, and not
learned: it divides the picture into nine cells and names each cell.

**Why the grid and not something cleverer.** A real solution would detect the
vehicle and its parts first. That needs a trained segmentation model, which this
project does not have and will not pretend to have (see
``docs/AI-INTEGRITY.md``). A documented grid is a weaker answer, but it is an
honest one, and the reasoning string attached to every finding names the cell
and the viewpoint it came from so a reviewer can check the mapping in seconds.

**Left and right are the vehicle's own, and the mirror depends on viewpoint.**
Standing in front of a car looking at its nose, the car's left flank is on the
*viewer's right*. Standing behind it, the car's left is on the viewer's left.
Getting this backwards would blame the wrong door, so the two grids are mirrored
explicitly rather than sharing one.

When no angle is supplied the mapping returns ``OTHER``. Guessing a panel from
an unlabelled photo would be exactly the kind of confident overclaim this module
exists to avoid.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from models.damage_detection import VehicleRegion


class Viewpoint(StrEnum):
    """Camera angle, as labelled by whoever took the photo."""

    FRONT = "FRONT"
    REAR = "REAR"
    LEFT_SIDE = "LEFT_SIDE"
    RIGHT_SIDE = "RIGHT_SIDE"
    TOP = "TOP"
    WHEEL = "WHEEL"
    INTERIOR = "INTERIOR"
    #: Photo taken without a declared angle. Region mapping is refused.
    GENERAL = "GENERAL"

    @classmethod
    def values(cls) -> list[str]:
        return [item.value for item in cls]


#: Nine cells per viewpoint, row-major from the top-left of the frame as the
#: photographer saw it. Rows run top to bottom, columns left to right.
REGION_GRIDS: dict[Viewpoint, tuple[tuple[VehicleRegion, ...], ...]] = {
    # Looking at the nose: the vehicle's left flank appears on the viewer's
    # right, hence the mirrored fenders.
    Viewpoint.FRONT: (
        (VehicleRegion.WINDSHIELD, VehicleRegion.WINDSHIELD, VehicleRegion.WINDSHIELD),
        (VehicleRegion.HOOD, VehicleRegion.HOOD, VehicleRegion.HOOD),
        (VehicleRegion.RIGHT_FENDER, VehicleRegion.FRONT_BUMPER, VehicleRegion.LEFT_FENDER),
    ),
    # Looking at the boot from behind: the vehicle's left is on the viewer's
    # left, so this grid is *not* mirrored.
    Viewpoint.REAR: (
        (VehicleRegion.REAR_WINDSHIELD,) * 3,
        (VehicleRegion.BOOT, VehicleRegion.BOOT, VehicleRegion.BOOT),
        (VehicleRegion.LEFT_FENDER, VehicleRegion.REAR_BUMPER, VehicleRegion.RIGHT_FENDER),
    ),
    # Nose to the viewer's left, photographer facing the vehicle's left flank.
    Viewpoint.LEFT_SIDE: (
        (VehicleRegion.HOOD, VehicleRegion.ROOF, VehicleRegion.BOOT),
        (VehicleRegion.FRONT_BUMPER, VehicleRegion.LEFT_FRONT_DOOR, VehicleRegion.REAR_BUMPER),
        (VehicleRegion.LEFT_FENDER, VehicleRegion.LEFT_REAR_DOOR, VehicleRegion.WHEELS),
    ),
    # Mirror of LEFT_SIDE: nose to the viewer's right.
    Viewpoint.RIGHT_SIDE: (
        (VehicleRegion.BOOT, VehicleRegion.ROOF, VehicleRegion.HOOD),
        (VehicleRegion.REAR_BUMPER, VehicleRegion.RIGHT_FRONT_DOOR, VehicleRegion.FRONT_BUMPER),
        (VehicleRegion.WHEELS, VehicleRegion.RIGHT_REAR_DOOR, VehicleRegion.RIGHT_FENDER),
    ),
    # Overhead, nose towards the top of the frame.
    Viewpoint.TOP: (
        (VehicleRegion.FRONT_BUMPER, VehicleRegion.FRONT_BUMPER, VehicleRegion.FRONT_BUMPER),
        (VehicleRegion.HOOD, VehicleRegion.ROOF, VehicleRegion.HOOD),
        (VehicleRegion.BOOT, VehicleRegion.BOOT, VehicleRegion.BOOT),
    ),
    Viewpoint.WHEEL: (
        (VehicleRegion.WHEELS,) * 3,
        (VehicleRegion.WHEELS,) * 3,
        (VehicleRegion.WHEELS,) * 3,
    ),
    Viewpoint.INTERIOR: (
        (VehicleRegion.INTERIOR,) * 3,
        (VehicleRegion.INTERIOR,) * 3,
        (VehicleRegion.INTERIOR,) * 3,
    ),
}

#: Angles a client may send. ``GENERAL`` is accepted but disables mapping.
SUPPORTED_ANGLES: tuple[str, ...] = tuple(Viewpoint.values())

#: Spellings accepted on the wire, so "front-left" and "FRONT_LEFT" agree.
_ALIASES: dict[str, Viewpoint] = {
    "front": Viewpoint.FRONT,
    "nose": Viewpoint.FRONT,
    "rear": Viewpoint.REAR,
    "back": Viewpoint.REAR,
    "boot": Viewpoint.REAR,
    "left": Viewpoint.LEFT_SIDE,
    "left-side": Viewpoint.LEFT_SIDE,
    "leftside": Viewpoint.LEFT_SIDE,
    "driver": Viewpoint.LEFT_SIDE,
    "right": Viewpoint.RIGHT_SIDE,
    "right-side": Viewpoint.RIGHT_SIDE,
    "rightside": Viewpoint.RIGHT_SIDE,
    "passenger": Viewpoint.RIGHT_SIDE,
    "top": Viewpoint.TOP,
    "roof": Viewpoint.TOP,
    "overhead": Viewpoint.TOP,
    "wheel": Viewpoint.WHEEL,
    "tyre": Viewpoint.WHEEL,
    "tire": Viewpoint.WHEEL,
    "interior": Viewpoint.INTERIOR,
    "cabin": Viewpoint.INTERIOR,
    "inside": Viewpoint.INTERIOR,
    "general": Viewpoint.GENERAL,
    "other": Viewpoint.GENERAL,
    "unknown": Viewpoint.GENERAL,
}


def normalise_viewpoint(raw: str | None) -> Viewpoint:
    """Parse a free-form angle label into a :class:`Viewpoint`.

    Unknown or missing labels become ``GENERAL`` rather than raising: an
    unlabelled photo is still worth comparing, it just cannot be mapped to a
    named panel. Callers that need a strict angle can check for ``GENERAL``.
    """
    if not raw:
        return Viewpoint.GENERAL

    key = raw.strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in key:
        key = key.replace("--", "-")
    key = key.strip("-")

    if key in _ALIASES:
        return _ALIASES[key]
    # "front-left" / "left-front" both mean a three-quarter view of the left
    # flank; the dominant word is the side.
    if "left" in key:
        return Viewpoint.LEFT_SIDE
    if "right" in key:
        return Viewpoint.RIGHT_SIDE
    if "front" in key:
        return Viewpoint.FRONT
    if "rear" in key or "back" in key:
        return Viewpoint.REAR
    return Viewpoint.GENERAL


def cell_for(
    bbox: tuple[int, int, int, int],
    frame_width: int,
    frame_height: int,
) -> tuple[int, int]:
    """Return the ``(row, column)`` grid cell containing a box's centre.

    The centre is used rather than the box as a whole so that a region spanning
    two cells resolves to one answer deterministically.
    """
    x, y, width, height = bbox
    centre_x = x + width / 2
    centre_y = y + height / 2

    column = min(2, max(0, int(centre_x / max(1, frame_width) * 3)))
    row = min(2, max(0, int(centre_y / max(1, frame_height) * 3)))
    return row, column


def map_region(
    bbox: tuple[int, int, int, int],
    *,
    frame_width: int,
    frame_height: int,
    viewpoint: Viewpoint,
) -> dict[str, Any]:
    """Name the vehicle region a bounding box falls in.

    Returns the region plus everything needed to explain the choice, because an
    unmapped ``OTHER`` and a deliberately coarse mapping must be
    distinguishable to a reviewer.
    """
    row, column = cell_for(bbox, frame_width, frame_height)

    if viewpoint is Viewpoint.GENERAL:
        return {
            "region": str(VehicleRegion.OTHER),
            "viewpoint": str(viewpoint),
            "grid_cell": [row, column],
            "mapped": False,
            "basis": (
                "No camera angle was supplied with this photo, so the changed "
                "region could not be mapped to a named panel. Supply an 'angle' "
                "on upload to enable region mapping."
            ),
        }

    grid = REGION_GRIDS[viewpoint]
    region = grid[row][column]
    cell_names = ("upper", "middle", "lower")
    column_names = ("left", "centre", "right")

    return {
        "region": str(region),
        "viewpoint": str(viewpoint),
        "grid_cell": [row, column],
        "mapped": True,
        "basis": (
            f"Mapped from the {cell_names[row]}-{column_names[column]} third of a "
            f"{viewpoint.value.lower().replace('_', ' ')} view, using a 3x3 grid. "
            "This is a coarse geometric mapping, not part segmentation."
        ),
    }


__all__ = [
    "REGION_GRIDS",
    "SUPPORTED_ANGLES",
    "Viewpoint",
    "cell_for",
    "map_region",
    "normalise_viewpoint",
]
