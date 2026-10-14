"""Image references: paths and object-storage keys, never binary payloads.

Vehicle photographs are written to ``storage/media/`` (or, in production, to
object storage) by the upload pipeline. The relational schema stores **only**
the reference to that blob plus the metadata needed to render and audit it:

* a relative path or ``s3://``-style key,
* byte size, pixel dimensions, MIME type,
* a SHA-256 digest, so an audit trail can prove the bytes behind a finding
  have not been swapped since it was recorded.

Nothing in this module, and nothing in the models, accepts ``bytes``. That rule
is enforced here rather than left to convention: :func:`assert_not_binary`
rejects byte payloads, and :func:`normalise_image_ref` validates and canonical-
ises a reference before it reaches the database.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from utils.exceptions import ValidationError

#: Accepted reference shapes: a POSIX-style relative path, or an object-storage
#: URI (``s3://bucket/key``, ``gs://bucket/key``, ``https://host/key``).
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/\-]*$")
_OBJECT_URI_RE = re.compile(r"^(?:s3|gs|https?)://[A-Za-z0-9._\-]+/[A-Za-z0-9._/\-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Absolute paths would tie a row to one machine's filesystem layout.
_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^\.\./", "must not escape the storage root"),
    (r"^/", "must be relative to the storage root, not absolute"),
    (r"//", "must not contain an empty path segment"),
    (r"\.\.", "must not contain a parent-directory segment"),
    (r"[\x00-\x1f]", "must not contain control characters"),
)

MAX_REFERENCE_LENGTH = 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB per reference target


@dataclass(frozen=True)
class ImageRef:
    """A validated reference to one stored image, with its metadata.

    ``as_dict()`` is what gets persisted into a JSONB column or a set of
    columns; there is deliberately no field for the image bytes.
    """

    path: str
    byte_size: int
    width: int
    height: int
    mime_type: str
    sha256: str
    captured_at: str | None = None
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ImageRef:
        """Re-validate a persisted reference (defends against hand-edited JSON)."""
        return cls(
            path=normalise_image_ref(payload["path"]),
            byte_size=int(payload["byte_size"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            mime_type=str(payload["mime_type"]),
            sha256=str(payload["sha256"]).lower(),
            captured_at=payload.get("captured_at"),
            label=payload.get("label"),
        )


def assert_not_binary(value: Any, *, field: str = "image") -> None:
    """Reject binary payloads outright.

    The schema never stores image bytes, so a ``bytes``/``bytearray``/
    ``memoryview`` arriving here is a bug in the caller. Failing loudly keeps
    the invariant true instead of silently coercing it.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValidationError(
            f"{field} must be a path or object-storage key, not image bytes. "
            "Store the file in storage/media/ (or object storage) and persist "
            "the reference instead.",
            details={"field": field},
        )


def normalise_image_ref(
    value: Any,
    *,
    field: str = "image_path",
    allow_object_uri: bool = True,
) -> str:
    """Validate and canonicalise an image reference.

    Raises :class:`ValidationError` for anything that is not a safe relative
    path or an object-storage URI. Backslashes are normalised to ``/`` so a
    reference written on Windows resolves identically on Linux.
    """
    assert_not_binary(value, field=field)

    if value is None:
        raise ValidationError(f"{field} is required.", details={"field": field})

    candidate = str(value).strip().replace("\\", "/")

    if not candidate:
        raise ValidationError(f"{field} must not be empty.", details={"field": field})
    if len(candidate) > MAX_REFERENCE_LENGTH:
        raise ValidationError(
            f"{field} must be at most {MAX_REFERENCE_LENGTH} characters.",
            details={"field": field, "max_length": MAX_REFERENCE_LENGTH},
        )

    # An object-storage URI is judged on its own shape: its `://` separator is
    # legitimate, so the "no empty path segment" rule must not be applied to it.
    if allow_object_uri and _OBJECT_URI_RE.match(candidate):
        return candidate

    for pattern, reason in _FORBIDDEN_PATTERNS:
        if re.search(pattern, candidate):
            raise ValidationError(
                f"{field} {reason}.",
                details={"field": field, "value": candidate},
            )

    if _RELATIVE_PATH_RE.match(candidate):
        return candidate

    raise ValidationError(
        f"{field} must be a relative path or an object-storage URI "
        "(s3://, gs://, https://).",
        details={"field": field, "value": candidate},
    )


def validate_image_ref_dict(payload: Any, *, field: str = "image") -> dict[str, Any]:
    """Validate a full :class:`ImageRef` payload and return its canonical dict."""
    if not isinstance(payload, dict):
        raise ValidationError(
            f"{field} must be an object describing the stored image.",
            details={"field": field},
        )

    missing = [
        key
        for key in ("path", "byte_size", "width", "height", "mime_type", "sha256")
        if key not in payload
    ]
    if missing:
        raise ValidationError(
            f"{field} is missing required key(s): {', '.join(missing)}.",
            details={"field": field, "missing_keys": missing},
        )

    reference = ImageRef.from_dict(payload)

    if reference.byte_size <= 0 or reference.byte_size > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"{field}.byte_size must be between 1 and {MAX_IMAGE_BYTES}.",
            details={"field": field, "byte_size": reference.byte_size},
        )
    if reference.width <= 0 or reference.height <= 0:
        raise ValidationError(
            f"{field} dimensions must be positive.",
            details={"field": field, "width": reference.width, "height": reference.height},
        )
    if not _SHA256_RE.match(reference.sha256):
        raise ValidationError(
            f"{field}.sha256 must be 64 lowercase hexadecimal characters.",
            details={"field": field},
        )

    return reference.as_dict()


def validate_image_ref_list(value: Any, *, field: str = "images") -> list[dict[str, Any]]:
    """Validate a list of image references, as stored on an inspection."""
    assert_not_binary(value, field=field)

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(
            f"{field} must be a list of image references.",
            details={"field": field},
        )

    return [
        validate_image_ref_dict(item, field=f"{field}[{index}]")
        for index, item in enumerate(value)
    ]


__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_REFERENCE_LENGTH",
    "ImageRef",
    "assert_not_binary",
    "normalise_image_ref",
    "validate_image_ref_dict",
    "validate_image_ref_list",
]
