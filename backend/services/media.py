"""Media storage: write uploaded images and QR renders to disk, reference them.

The schema never stores image bytes -- every table holds a validated
:class:`~utils.image_ref.ImageRef` instead. This module is the only place that
touches the filesystem, so the invariants live in one reviewable file:

* the stored filename is generated here and never taken from the client, so a
  crafted ``filename`` cannot write outside the storage root;
* the declared extension is checked against the file's own signature bytes
  (``utils.validation.validate_image_upload``), so a renamed executable is
  rejected rather than served back as an image;
* dimensions come from decoding the pixels with OpenCV rather than trusting a
  client-supplied header, which is what makes the before/after comparison and
  the ``width``/``height`` fields trustworthy;
* ``resolve_media_path`` re-validates a stored reference and confirms the
  resolved path is still inside the storage root before anything is served.

Nothing here imports Flask request state; callers pass in what they read.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import current_app
from werkzeug.datastructures import FileStorage

from utils.exceptions import ValidationError
from utils.image_probe import probe_image_size
from utils.image_ref import ImageRef, normalise_image_ref
from utils.validation import validate_image_upload

MIME_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def storage_root() -> Path:
    """Absolute root that every stored reference is relative to."""
    return Path(current_app.config["UPLOAD_FOLDER"])


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _relative_directory(*parts: str) -> str:
    """Build a relative subdirectory from trusted, caller-supplied segments."""
    return "/".join(part.strip("/") for part in parts if part)


def _image_dimensions(blob: bytes) -> tuple[int, int]:
    """Decode ``blob`` with OpenCV and return ``(width, height)``.

    Reading the actual pixels is deliberate: a client-declared size would let a
    caller store a reference that misrepresents the image, and the damage
    comparison step relies on these numbers being real.

    The header is probed first. ``imdecode`` allocates the buffer the header
    asks for before decoding anything, so a file whose header claims a few
    billion pixels would take the worker down with a few dozen bytes. The probe
    reads the header without allocating, and rejects anything out of range.
    """
    probe_image_size(blob, field="image")
    decoded = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValidationError(
            "The uploaded image could not be decoded. It may be truncated or "
            "not a supported image format.",
            details={"field": "image"},
        )
    height, width = decoded.shape[:2]

    # The upper bounds are enforced inside ``probe_image_size`` because they
    # guard memory allocation and must hold on the ``ai/vision`` path too, which
    # cannot import Flask. The *lower* bound is a policy about what counts as a
    # usable photograph rather than a resource guard, so it lives here where the
    # configured value is reachable. It was declared in settings and documented
    # in .env.example but never read -- a 2x2 pixel file was accepted.
    minimum = int(current_app.config.get("MIN_IMAGE_PIXELS", 0) or 0)
    if minimum and (int(width) < minimum or int(height) < minimum):
        raise ValidationError(
            f"The image is {int(width)}x{int(height)} pixels. Each side must be "
            f"at least {minimum} pixels; smaller frames are not usable for a "
            "condition assessment.",
            details={
                "field": "image",
                "width": int(width),
                "height": int(height),
                "min_pixels": minimum,
                "reason": "dimension_too_small",
            },
        )

    return int(width), int(height)


def build_image_ref(
    *,
    path: str,
    blob: bytes,
    mime_type: str,
    label: str | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical reference dict for bytes already on disk."""
    width, height = _image_dimensions(blob)
    return ImageRef(
        path=normalise_image_ref(path),
        byte_size=len(blob),
        width=width,
        height=height,
        mime_type=mime_type,
        sha256=hashlib.sha256(blob).hexdigest(),
        captured_at=(captured_at or _now()).isoformat(),
        label=label,
    ).as_dict()


def store_upload(
    file: FileStorage,
    *,
    subdir: str,
    label: str | None = None,
    field: str = "image",
) -> dict[str, Any]:
    """Validate an uploaded image and write it under ``UPLOAD_FOLDER/subdir``.

    Returns the validated ``ImageRef`` dict ready to be persisted in a JSONB
    column. The caller never chooses the stored filename.
    """
    validated, extension = validate_image_upload(file, field=field)

    blob = validated.stream.read()
    validated.stream.seek(0)

    stored_name = f"{uuid.uuid4().hex}.{extension}"
    relative_path = f"{_relative_directory(subdir)}/{stored_name}"

    absolute = storage_root() / relative_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(blob)

    return build_image_ref(
        path=relative_path,
        blob=blob,
        mime_type=MIME_BY_EXTENSION[extension],
        label=label,
    )


def store_bytes(
    blob: bytes,
    *,
    subdir: str,
    extension: str,
    label: str | None = None,
) -> dict[str, Any]:
    """Write server-generated bytes (a QR render, say) and reference them."""
    if extension not in MIME_BY_EXTENSION:
        raise ValidationError(
            f"Unsupported image extension '.{extension}'.",
            details={"extension": extension, "allowed": sorted(MIME_BY_EXTENSION)},
        )

    stored_name = f"{uuid.uuid4().hex}.{extension}"
    relative_path = f"{_relative_directory(subdir)}/{stored_name}"

    absolute = storage_root() / relative_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(blob)

    return build_image_ref(
        path=relative_path,
        blob=blob,
        mime_type=MIME_BY_EXTENSION[extension],
        label=label,
    )


def store_dated_upload(file: FileStorage, *, subdir: str, **kwargs: Any) -> dict[str, Any]:
    """``store_upload`` with a ``YYYY/MM/DD`` partition, for busy folders."""
    today = _now()
    return store_upload(
        file,
        subdir=f"{subdir}/{today:%Y}/{today:%m}/{today:%d}",
        **kwargs,
    )


def resolve_media_path(reference: str) -> Path:
    """Turn a stored reference into an absolute path inside the storage root.

    Re-validates the reference and then confirms containment, because a
    reference could have been hand-edited in the database since it was written.
    Two independent checks is deliberate: the first rejects the obvious escape
    patterns, the second catches anything they missed.
    """
    relative = normalise_image_ref(reference)
    if "://" in relative:
        raise ValidationError(
            "Object-storage references cannot be served from local storage.",
            details={"path": relative},
        )

    root = storage_root().resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValidationError(
            "The stored path escapes the storage root.",
            details={"path": relative},
        )
    if not candidate.is_file():
        raise ValidationError(
            "The stored image is missing from storage.",
            details={"path": relative},
        )
    return candidate


def media_url(reference: str) -> str:
    """The URL the SPA uses to display a stored reference."""
    return f"/media/{normalise_image_ref(reference)}"


__all__ = [
    "MIME_BY_EXTENSION",
    "build_image_ref",
    "media_url",
    "resolve_media_path",
    "storage_root",
    "store_bytes",
    "store_dated_upload",
    "store_upload",
]
