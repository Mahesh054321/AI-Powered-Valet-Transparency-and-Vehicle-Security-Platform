"""Input validation helpers shared by every API blueprint.

These are deliberately dependency-free (no marshmallow) so the validation logic
is visible and testable in a small file, which is what an academic reviewer
wants to read.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage

from utils.exceptions import PayloadTooLargeError, UnsupportedMediaError, ValidationError

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,18}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Signature bytes for the image types we accept. Relying on the client-declared
# MIME type alone is unsafe, so uploads are sniffed as well.
IMAGE_SIGNATURES: tuple[tuple[bytes, set[str]], ...] = (
    (b"\xff\xd8\xff", {"jpg", "jpeg"}),
    (b"\x89PNG\r\n\x1a\n", {"png"}),
    (b"RIFF", {"webp"}),
)


def require_fields(payload: dict[str, Any] | None, *fields: str) -> dict[str, Any]:
    """Ensure ``payload`` is a dict containing every listed field."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    missing = [f for f in fields if f not in payload or payload[f] in (None, "")]
    if missing:
        raise ValidationError("Missing required field(s).", details={"missing_fields": missing})
    return payload


def validate_email(value: Any, *, field: str = "email") -> str:
    text = str(value or "").strip().lower()
    if len(text) > 320 or not EMAIL_RE.match(text):
        raise ValidationError(f"'{field}' must be a valid email address.", details={"field": field})
    return text


def validate_phone(value: Any, *, field: str = "phone", required: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValidationError(f"'{field}' is required.", details={"field": field})
        return None
    if not PHONE_RE.match(text):
        raise ValidationError(f"'{field}' must be a valid phone number.", details={"field": field})
    return text


def validate_password(value: Any, *, field: str = "password", min_length: int = 8) -> str:
    text = str(value or "")
    problems: list[str] = []
    if len(text) < min_length:
        problems.append(f"at least {min_length} characters")
    if not any(ch.isalpha() for ch in text):
        problems.append("at least one letter")
    if not any(ch.isdigit() for ch in text):
        problems.append("at least one digit")
    if problems:
        raise ValidationError(
            f"'{field}' is too weak: it needs {', '.join(problems)}.",
            details={"field": field, "requirements": problems},
        )
    return text


def validate_uuid(value: Any, *, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"'{field}' must be a valid UUID.", details={"field": field}) from exc


PLATE_HELP = (
    "does not look like a valid Indian registration number "
    "(e.g. UP85CA1234, MH12DE1234 or 25BH0987AB)"
)


def validate_plate_number(value: Any, *, field: str = "plate_number") -> str:
    """Validate an Indian vehicle registration number.

    Accepted shapes, after stripping spaces and hyphens:

    * ``<2 state letters><1-2 RTO digits><1-3 series letters><4 digits>``
      e.g. ``UP85CA1234`` - the standard format.
    * ``<2 digits>BH<4 digits><1-2 letters>``
      e.g. ``25BH0987AB`` - the portable "Bharat" series.

    This is expressed as explicit length/segment checks rather than one large
    regular expression, because a greedy ``[A-Z]{1,3}`` group silently rejects
    valid two-letter-series plates such as ``DL01AB0001``.
    """
    text = re.sub(r"[\s\-]+", "", str(value or "").upper())
    if not text:
        raise ValidationError(f"'{field}' is required.", details={"field": field})

    valid = False
    if 8 <= len(text) <= 12:
        if text[2:4] == "BH" and text[:2].isdigit():
            # Bharat series: <2 digits>BH<4 digits><1-2 letters>
            tail = text[4:]
            valid = len(tail) in {5, 6} and tail[:4].isdigit() and tail[4:].isalpha()
        elif text[:2].isalpha() and text[-4:].isdigit():
            # Standard series: <2 letters><1-2 digits><1-3 letters><4 digits>
            middle = text[2:-4]
            digits = ""
            letters = ""
            for char in middle:
                if char.isdigit() and not letters:
                    digits += char
                elif char.isalpha() and digits:
                    letters += char
                else:
                    digits = letters = ""
                    break
            valid = 1 <= len(digits) <= 2 and len(letters) <= 3 and digits + letters == middle

    if not valid:
        raise ValidationError(
            f"'{field}' {PLATE_HELP}.",
            details={"field": field, "received": text},
        )
    return text


def validate_latitude(value: Any, *, field: str = "latitude") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"'{field}' must be a number.", details={"field": field}) from exc
    if not -90.0 <= number <= 90.0:
        raise ValidationError(f"'{field}' must be between -90 and 90.", details={"field": field})
    return number


def validate_longitude(value: Any, *, field: str = "longitude") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"'{field}' must be a number.", details={"field": field}) from exc
    if not -180.0 <= number <= 180.0:
        raise ValidationError(f"'{field}' must be between -180 and 180.", details={"field": field})
    return number


def validate_choice(value: Any, choices: Iterable[str], *, field: str = "value") -> str:
    text = str(value or "").upper()
    allowed = [str(choice) for choice in choices]
    if text not in allowed:
        raise ValidationError(
            f"'{field}' must be one of {allowed}.",
            details={"field": field, "allowed": allowed, "received": str(value)},
        )
    return text


def validate_text(
    value: Any,
    *,
    field: str,
    min_length: int = 1,
    max_length: int = 200,
    required: bool = True,
) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValidationError(f"'{field}' is required.", details={"field": field})
        return None
    if len(text) < min_length or len(text) > max_length:
        raise ValidationError(
            f"'{field}' must be between {min_length} and {max_length} characters.",
            details={"field": field},
        )
    return text


def validate_hex_color(value: Any, *, field: str = "color") -> str:
    text = str(value or "").strip()
    if not HEX_COLOR_RE.match(text):
        raise ValidationError(
            f"'{field}' must be a hex colour like #RRGGBB.", details={"field": field}
        )
    return text


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------
def _sniff_image_extension(head: bytes) -> set[str] | None:
    for signature, extensions in IMAGE_SIGNATURES:
        if head.startswith(signature):
            if signature == b"RIFF":
                # WEBP is "RIFF....WEBP"; a plain RIFF/AVI file is rejected.
                return extensions if head[8:12] == b"WEBP" else None
            return extensions
    return None


def validate_image_upload(
    file: FileStorage | None, *, field: str = "image"
) -> tuple[FileStorage, str]:
    """Validate an uploaded image's presence, extension, sniffed type and size.

    Returns the file and its normalised extension.
    """
    if file is None or not getattr(file, "filename", None):
        raise ValidationError(f"No file was uploaded in field '{field}'.", details={"field": field})

    defaults = {"jpg", "jpeg", "png", "webp"}
    allowed: set[str] = set(current_app.config.get("ALLOWED_IMAGE_EXTENSIONS", defaults))
    extension = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    if extension == "jpeg":
        extension = "jpg"

    if extension not in allowed:
        raise UnsupportedMediaError(
            f"File type '.{extension}' is not allowed. Accepted: {', '.join(sorted(allowed))}.",
            details={"field": field, "allowed": sorted(allowed)},
        )

    # Size check first: seek to the end to measure without loading into memory.
    position = file.stream.tell()
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(position)

    if size == 0:
        raise ValidationError("The uploaded image is empty.", details={"field": field})

    max_bytes = int(current_app.config.get("MAX_CONTENT_LENGTH", 15 * 1024 * 1024))
    if size > max_bytes:
        raise PayloadTooLargeError(
            f"Image is {size / 1024 / 1024:.1f} MB; "
            f"the maximum is {max_bytes / 1024 / 1024:.0f} MB.",
            details={"field": field, "size_bytes": size, "max_bytes": max_bytes},
        )

    # Read the leading bytes for signature sniffing, then rewind for the caller.
    head = file.stream.read(16)
    file.stream.seek(0)
    sniffed = _sniff_image_extension(head)
    if sniffed is None or extension not in sniffed:
        raise UnsupportedMediaError(
            "The file content does not match its declared image type.",
            details={"field": field, "declared": extension},
        )

    return file, extension
