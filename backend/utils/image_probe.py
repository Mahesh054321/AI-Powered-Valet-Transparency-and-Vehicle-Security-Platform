"""Read an image's declared dimensions from its header, before decoding it.

**Why this module exists.** ``cv2.imdecode`` allocates the pixel buffer implied
by the file header before it has read any pixels. A file whose PNG ``IHDR``
claims a width and height of a few billion therefore makes OpenCV attempt a
multi-gigabyte allocation, and the process is killed by the OOM reaper. This is
reachable with 28 bytes:

    b"\\x89PNG\\r\\n\\x1a\\n" + b"but not really a png"

Those trailing bytes land exactly where the width and height fields sit, so the
parser reads them as dimensions. The file is a syntactically plausible PNG, it
passes a signature check, and it is tiny -- so a byte-size limit does not help.

Both :func:`services.media._image_dimensions` and
:func:`ai.vision.preprocessing.decode` call ``imdecode`` on client-supplied
bytes, so both need this guard. Without it, an unauthenticated-shape upload can
take down the API worker.

The probe uses Pillow, which parses only the header when a file is opened: the
pixel buffer is not allocated until the image is loaded. Pillow's own
decompression-bomb limit is also tightened here, so a file that survives the
explicit check still cannot trigger an enormous allocation further down.
"""

from __future__ import annotations

import io

from PIL import Image

from utils.exceptions import ValidationError

#: Longest side accepted, in pixels. Far beyond any phone camera, well below
#: anything that would threaten the process.
MAX_IMAGE_DIMENSION = 8192

#: Total pixels accepted. 40 MP covers real camera output comfortably; at three
#: bytes per channel a BGR decode of the maximum is roughly 120 MB, which is
#: survivable where a header-claimed 4-gigapixel image is not.
MAX_IMAGE_PIXELS = 40_000_000

# Pillow warns at its own default (~178 MP) and only raises at twice that, which
# is still far more than this application should decode. Tightening the global
# means any path that reaches Pillow's decoder is bounded too.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def probe_image_size(blob: bytes, *, field: str = "image") -> tuple[int, int]:
    """Return ``(width, height)`` from the header, without decoding pixels.

    Raises ``ValidationError`` (HTTP 422) when the bytes are not a readable
    image, or when the declared dimensions exceed the limits above. The second
    case is reported distinctly, because it is almost always either a corrupt
    file or an attempt to exhaust memory, and a reviewer should be able to tell
    those apart from an ordinary bad upload.
    """
    if not blob:
        raise ValidationError(
            "The image is empty.", details={"field": field}
        )

    try:
        with Image.open(io.BytesIO(blob)) as opened:
            # Reading `.size` parses the header only; no pixels are allocated.
            width, height = opened.size
    except Image.DecompressionBombError as error:
        raise ValidationError(
            "The image declares more pixels than this service will decode. This "
            "is rejected before any pixel data is read.",
            details={
                "field": field,
                "max_pixels": MAX_IMAGE_PIXELS,
                "reason": "declared_pixel_count",
            },
        ) from error
    except Exception as error:  # noqa: BLE001 - Pillow raises many types here
        raise ValidationError(
            "The image header could not be read. The file may be truncated or "
            "not a supported image format.",
            details={"field": field, "reason": "unreadable_header"},
        ) from error

    if width <= 0 or height <= 0:
        raise ValidationError(
            "The image declares a non-positive size.",
            details={"field": field, "width": width, "height": height},
        )

    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValidationError(
            f"The image is {width}x{height} pixels. Each side must be at most "
            f"{MAX_IMAGE_DIMENSION} pixels. The declared size is rejected before "
            "decoding, because allocating a buffer for it could exhaust memory.",
            details={
                "field": field,
                "width": width,
                "height": height,
                "max_dimension": MAX_IMAGE_DIMENSION,
                "reason": "dimension_too_large",
            },
        )

    if width * height > MAX_IMAGE_PIXELS:
        raise ValidationError(
            f"The image is {width}x{height} = {width * height:,} pixels. The "
            f"maximum is {MAX_IMAGE_PIXELS:,}.",
            details={
                "field": field,
                "width": width,
                "height": height,
                "pixels": width * height,
                "max_pixels": MAX_IMAGE_PIXELS,
                "reason": "too_many_pixels",
            },
        )

    return int(width), int(height)


def is_supported_format(blob: bytes) -> bool:
    """Whether the header names a format this service can decode."""
    try:
        with Image.open(io.BytesIO(blob)) as opened:
            return opened.format in {"PNG", "JPEG", "WEBP"}
    except Exception:  # noqa: BLE001 - any failure means "not supported"
        return False


__all__ = [
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "is_supported_format",
    "probe_image_size",
]
