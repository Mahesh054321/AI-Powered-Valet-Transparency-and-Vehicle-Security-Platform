"""HTTP middleware: request context, access logging, security headers.

Registered by the application factory in a fixed order:

    register_request_context(app)   before/after request hooks
    register_error_handlers(app)    exception -> JSON envelope

Each function is idempotent and takes only the app, so the factory stays
readable and the ordering is explicit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from flask import Flask, g, jsonify, request


def register_request_context(app: Flask) -> None:
    """Attach a request id, time the request, and add security headers.

    A client-supplied ``X-Request-Id`` is honoured and echoed back, so a
    browser action can be matched to a server log line. A generated id is used
    otherwise.
    """

    @app.before_request
    def start_request() -> None:
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        g.started_at = datetime.now(tz=UTC)

    @app.after_request
    def finish_request(response):
        response.headers["X-Request-Id"] = getattr(g, "request_id", "unknown")

        # Defence in depth. Cheap, and reviewers look for them.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Cache-Control", "no-store" if request.path.startswith("/api/") else "no-cache"
        )

        if request.method != "OPTIONS":
            duration_ms = 0.0
            started = getattr(g, "started_at", None)
            if started is not None:
                duration_ms = round((datetime.now(tz=UTC) - started).total_seconds() * 1000, 2)
            app.logger.info(
                "%s %s -> %s (%sms) rid=%s",
                request.method,
                request.full_path.rstrip("?"),
                response.status_code,
                duration_ms,
                getattr(g, "request_id", "unknown"),
            )
        return response


def register_fallback_routes(app: Flask) -> None:
    """JSON responses for unknown paths, instead of Flask's HTML error page."""

    @app.errorhandler(404)
    def not_found(_error):
        scope = "API" if request.path.startswith("/api/") else "resource"
        return jsonify(
            {
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": (
                        f"No API route matches {request.method} {request.path}."
                        if scope == "API"
                        else "Resource not found."
                    ),
                },
                "meta": {"request_id": getattr(g, "request_id", "unknown")},
            }
        ), 404
