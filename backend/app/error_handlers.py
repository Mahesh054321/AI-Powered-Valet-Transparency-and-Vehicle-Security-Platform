"""Error handlers: every failure leaves the API in the same JSON envelope.

Unhandled exceptions are logged with a traceback but never leak internals - the
response carries only a request id, which is what support needs in order to find
the server-side log entry.
"""

from __future__ import annotations

import logging
import re

from flask import Flask
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from utils.exceptions import ApiError, ConflictError, ValidationError
from utils.responses import error_payload

logger = logging.getLogger(__name__)

#: Pulls the constraint name out of a PostgreSQL integrity violation, e.g.
#: ``duplicate key value violates unique constraint "uq_users_email"``.
#: Only the quoted identifier is kept -- the surrounding text on a unique
#: violation carries a DETAIL line that quotes the offending row's values.
_CONSTRAINT_RE = re.compile(r'constraint\s+"([^"]+)"')


def _constraint_name(detail: str) -> str | None:
    """The violated constraint's name, or ``None`` if it cannot be parsed.

    Returns just the identifier. A caller needs enough to know *which* rule it
    tripped; it never needs the row, and the row is exactly what the driver text
    offers on a duplicate-key error.
    """
    match = _CONSTRAINT_RE.search(detail)
    return match.group(1) if match else None


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error_payload(
            error.code,
            error.message,
            status=error.status_code,
            details=error.details or None,
        )

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return error_payload(error.code, error.message, status=422, details=error.details or None)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        code = (error.name or "HTTP_ERROR").upper().replace(" ", "_")
        return error_payload(code, error.description or error.name, status=error.code or 500)

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(error: IntegrityError):
        # A unique/FK violation is a conflict. The full driver text is logged
        # server-side but is *not* echoed to the caller: on a duplicate-key
        # error PostgreSQL's DETAIL line quotes the offending row, e.g.
        # `Key (email)=(someone@example.com) already exists.` Sending that back
        # would confirm which addresses exist and leak another customer's data.
        # The constraint *name* is schema, never data, so that is all the client
        # gets -- and it is parsed out rather than sliced out of the whole
        # message, which is what previously dragged the DETAIL line along.
        detail = str(getattr(error, "orig", error))
        lowered = detail.lower()
        message = "The request conflicts with existing data."
        if "duplicate key" in lowered:
            message = "A record with these unique values already exists."
        elif "foreign key" in lowered:
            message = "The request references a record that does not exist."
        logger.warning("IntegrityError: %s", detail)
        return error_payload(
            ConflictError.code,
            message,
            status=409,
            details={"constraint": _constraint_name(detail)},
        )

    @app.errorhandler(SQLAlchemyError)
    def handle_sqlalchemy_error(error: SQLAlchemyError):
        logger.exception("Database error")
        return error_payload(
            "DATABASE_ERROR",
            "A database error occurred. The event has been logged.",
            status=500,
        )

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        logger.exception("Unhandled exception")
        return error_payload(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred. Reference the request id in support logs.",
            status=500,
        )
