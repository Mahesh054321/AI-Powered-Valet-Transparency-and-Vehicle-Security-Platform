"""Request-reading helpers shared by the API blueprints.

Kept in ``utils`` so every blueprint raises the *same* 422 for a malformed body
instead of each one inventing its own wording.
"""

from __future__ import annotations

from typing import Any

from flask import request

from utils.exceptions import ValidationError


def json_payload(*, required: bool = True) -> dict[str, Any]:
    """The request body as a dict, or a clear 422.

    ``silent=True`` means a body that is not JSON yields ``None`` rather than a
    Werkzeug 400, so the caller controls the message. Endpoints that accept an
    empty body (``DELETE``-style actions) pass ``required=False``.
    """
    body = request.get_json(silent=True)
    if body is None:
        if not required:
            return {}
        raise ValidationError(
            "Request body must be JSON with a Content-Type of application/json."
        )
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


def query_flag(name: str, *, default: bool = False) -> bool:
    """A boolean query parameter, tolerant of the common spellings."""
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["json_payload", "query_flag"]
