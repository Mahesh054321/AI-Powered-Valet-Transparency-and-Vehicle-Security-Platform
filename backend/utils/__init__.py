"""Cross-cutting utilities: security, validation, responses, pagination, errors.

Import direction: ``routes`` -> ``services`` -> ``database``/``models``, with
``utils`` and ``config`` usable from anywhere. Nothing in ``utils`` imports
from ``routes`` or ``services``.
"""

from utils.exceptions import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitError,
    ServiceUnavailableError,
    UnsupportedMediaError,
    ValidationError,
)
from utils.pagination import PaginationParams, parse_pagination
from utils.responses import created, error_payload, no_content, ok, paginated
from utils.security import (
    AuthContext,
    Role,
    TokenType,
    capability_required,
    create_token,
    current_user,
    decode_token,
    hash_password,
    load_current_user,
    require_auth,
    roles_required,
    verify_password,
)

__all__ = [
    "ApiError",
    "AuthContext",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "NotFoundError",
    "PaginationParams",
    "PayloadTooLargeError",
    "RateLimitError",
    "Role",
    "ServiceUnavailableError",
    "TokenType",
    "UnsupportedMediaError",
    "ValidationError",
    "capability_required",
    "create_token",
    "created",
    "current_user",
    "decode_token",
    "error_payload",
    "hash_password",
    "load_current_user",
    "no_content",
    "ok",
    "paginated",
    "parse_pagination",
    "require_auth",
    "roles_required",
    "verify_password",
]
