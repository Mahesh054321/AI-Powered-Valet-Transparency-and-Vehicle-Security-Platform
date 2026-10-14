"""Base application errors and the canonical API error envelope.

Every failure that reaches the client is rendered as::

    {
      "success": false,
      "error": {"code": "VALIDATION_ERROR", "message": "...", "details": {...}},
      "meta": {"request_id": "...", "timestamp": "..."}
    }

Keeping one shape means the React error interceptor never has to guess.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Base class for all domain/API errors that map to an HTTP status."""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    default_message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(ApiError):
    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "One or more fields failed validation."


class AuthenticationError(ApiError):
    status_code = 401
    code = "AUTHENTICATION_ERROR"
    default_message = "Authentication is required or the credentials are invalid."


class AuthorizationError(ApiError):
    status_code = 403
    code = "AUTHORIZATION_ERROR"
    default_message = "You do not have permission to perform this action."


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


class RateLimitError(ApiError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "Too many requests. Please try again later."


class UnsupportedMediaError(ApiError):
    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"
    default_message = "The uploaded file type is not supported."


class PayloadTooLargeError(ApiError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    default_message = "The uploaded file exceeds the maximum allowed size."


class ServiceUnavailableError(ApiError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "A required service is currently unavailable."
