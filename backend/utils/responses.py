"""Uniform JSON response builders.

The API always answers with the same envelope so that the React client can rely
on ``response.data.success`` and ``response.data.data`` everywhere.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import g, jsonify


def _meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "request_id": getattr(g, "request_id", None) or uuid.uuid4().hex,
        "server_time": time.time(),
    }
    if extra:
        meta.update(extra)
    return meta


def ok(data: Any = None, *, status: int = 200, meta: dict[str, Any] | None = None):
    """2xx success response."""
    return jsonify({"success": True, "data": data, "meta": _meta(meta)}), status


def created(data: Any = None, *, meta: dict[str, Any] | None = None):
    return ok(data, status=201, meta=meta)


def no_content():
    return "", 204


def paginated(
    items: list[Any],
    *,
    page: int,
    page_size: int,
    total: int,
    meta: dict[str, Any] | None = None,
):
    """List response with deterministic pagination metadata."""
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    payload_meta = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }
    return jsonify(
        {"success": True, "data": {"items": items, "pagination": payload_meta}, "meta": _meta(meta)}
    ), 200


def error_payload(
    code: str,
    message: str,
    *,
    status: int = 400,
    details: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
):
    body: dict[str, Any] = {
        "success": False,
        "error": {"code": code, "message": message},
        "meta": _meta(meta),
    }
    if details:
        body["error"]["details"] = details
    return jsonify(body), status
