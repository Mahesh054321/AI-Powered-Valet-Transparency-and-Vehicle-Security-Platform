"""Security primitives: password hashing, JWT issuing/verification, RBAC.

* Passwords are hashed with Werkzeug's PBKDF2-HMAC-SHA256 (salted, 600k
  iterations by default). No plaintext password is ever stored or logged.
* Tokens are signed HS256 JWTs carrying ``sub``, ``role``, ``jti``, ``iss``,
  ``aud``, ``iat``, ``nbf``, ``exp`` and a ``typ`` claim that separates access
  tokens from refresh tokens.
* Authorization is enforced by decorators that compare the token role against a
  required-role set. Role hierarchy is explicit, not implicit.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
from flask import current_app, g, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from utils.exceptions import AuthenticationError, AuthorizationError


class Role(StrEnum):
    """The four platform roles, ordered least to most privileged."""

    CUSTOMER = "CUSTOMER"
    VALET_OPERATOR = "VALET_OPERATOR"
    VALET_MANAGER = "VALET_MANAGER"
    ADMIN = "ADMIN"

    @classmethod
    def values(cls) -> list[str]:
        return [role.value for role in cls]


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# Explicit capability matrix. Adding a role grants nothing by accident: a role
# must be listed for the capability it needs.
ROLE_CAPABILITIES: dict[str, set[str]] = {
    Role.CUSTOMER: {
        "vehicle:create",
        "vehicle:read_own",
        "ticket:read_own",
        "custody:read_own",
        "retrieval:request",
        "alert:read_own",
        "report:read_own",
    },
    Role.VALET_OPERATOR: {
        "ticket:read_assigned",
        "ticket:update_status",
        "custody:read_assigned",
        "custody:check_in",
        "custody:check_out",
        "inspection:create",
        "telemetry:read_assigned",
        "alert:read_assigned",
    },
    Role.VALET_MANAGER: {
        "ticket:read_all",
        "ticket:update_status",
        "custody:read_all",
        "custody:check_in",
        "custody:check_out",
        "inspection:create",
        "inspection:review",
        "telemetry:read_all",
        "alert:read_all",
        "alert:acknowledge",
        "zone:manage",
        "report:read_all",
        "user:read_all",
    },
    Role.ADMIN: {
        "ticket:read_all",
        "ticket:update_status",
        "custody:read_all",
        "custody:check_in",
        "custody:check_out",
        "inspection:create",
        "inspection:review",
        "telemetry:read_all",
        "alert:read_all",
        "alert:acknowledge",
        "zone:manage",
        "report:read_all",
        "user:read_all",
        "user:manage",
        "audit:read_all",
        "config:manage",
    },
}

# A manager implicitly holds operator rights; an admin holds manager rights.
_ROLE_INHERITS: dict[str, str | None] = {
    Role.CUSTOMER: None,
    Role.VALET_OPERATOR: None,
    Role.VALET_MANAGER: Role.VALET_OPERATOR,
    Role.ADMIN: Role.VALET_MANAGER,
}


def capabilities_for(role: str) -> set[str]:
    """Return the effective capability set for ``role``, following inheritance."""
    caps: set[str] = set()
    current: str | None = role
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        caps |= ROLE_CAPABILITIES.get(current, set())
        current = _ROLE_INHERITS.get(current)
    return caps


def has_capability(role: str, capability: str) -> bool:
    return capability in capabilities_for(role)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Return a salted PBKDF2-SHA256 hash string (algorithm$iterations$salt$hash)."""
    if not password:
        raise ValueError("Password must not be empty.")
    cfg = current_app.config if current_app else None
    method = (cfg or {}).get("PASSWORD_HASH_SCHEME", "pbkdf2:sha256")
    iterations = int((cfg or {}).get("PASSWORD_HASH_ITERATIONS", 600_000))
    if method == "pbkdf2:sha256":
        method = f"{method}:{iterations}"
    return generate_password_hash(password, method=method)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time comparison of a candidate password against a stored hash."""
    if not password or not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthContext:
    """Decoded, validated identity carried by a request."""

    user_id: str
    role: str
    email: str | None
    token_id: str
    token_type: str
    expires_at: datetime

    def can(self, capability: str) -> bool:
        return has_capability(self.role, capability)


def _jwt_settings() -> tuple[str, str, str, str]:
    cfg = current_app.config
    return (
        cfg["JWT_SECRET_KEY"],
        cfg["JWT_ALGORITHM"],
        cfg["JWT_ISSUER"],
        cfg["JWT_AUDIENCE"],
    )


def create_token(
    *,
    user_id: str,
    role: str,
    email: str | None = None,
    token_type: TokenType | str = TokenType.ACCESS,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Sign an access or refresh token. Returns ``(token, expires_at)``."""
    secret, algorithm, issuer, audience = _jwt_settings()
    now = datetime.now(tz=UTC)
    token_type = TokenType(token_type)

    if token_type is TokenType.ACCESS:
        ttl = timedelta(minutes=int(current_app.config["JWT_ACCESS_TOKEN_TTL_MINUTES"]))
    else:
        ttl = timedelta(days=int(current_app.config["JWT_REFRESH_TOKEN_TTL_DAYS"]))

    expires_at = now + ttl
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "typ": token_type.value,
        "jti": uuid.uuid4().hex,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if email:
        claims["email"] = email
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(claims, secret, algorithm=algorithm), expires_at


def decode_token(token: str, *, expected_type: TokenType | str = TokenType.ACCESS) -> AuthContext:
    """Verify signature, expiry, issuer, audience and token type."""
    secret, algorithm, issuer, audience = _jwt_settings()
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthenticationError("Token audience is invalid.") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthenticationError("Token issuer is invalid.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Token is invalid or malformed.") from exc

    if claims.get("typ") != TokenType(expected_type).value:
        raise AuthenticationError(
            f"Expected a {TokenType(expected_type).value} token but received '{claims.get('typ')}'."
        )

    role = claims.get("role")
    if role not in Role.values():
        raise AuthenticationError("Token carries an unknown role.")

    return AuthContext(
        user_id=str(claims["sub"]),
        role=role,
        email=claims.get("email"),
        token_id=str(claims["jti"]),
        token_type=str(claims["typ"]),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )


# ---------------------------------------------------------------------------
# Request-scoped auth helpers
# ---------------------------------------------------------------------------
def extract_bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


def load_current_user(required: bool = True) -> AuthContext | None:
    """Decode the bearer token for this request, caching the result on ``g``.

    A cryptographically valid token is not automatically accepted. Three further
    checks turn a stateless JWT into a real session:

    1. **revocation** -- a token whose ``jti`` is in ``revoked_tokens`` was
       signed out and is rejected, which is what makes logout meaningful;
    2. **existence** -- the subject must still be a row in ``users``;
    3. **status** -- a suspended or deactivated account is rejected even while
       its token is unexpired.

    Steps 2 and 3 reject only when the account is *found and not usable*. A
    subject that does not exist is allowed through, because tokens are minted
    for non-account principals too (the seeded audit history references actors
    that were never accounts), and the endpoints that need a real user load it
    explicitly.

    If the database is unreachable the cryptographic checks still stand and the
    account checks are skipped, so a database outage degrades authentication
    rather than disabling it outright.
    """
    cached = g.get("auth_context")
    if cached is not None:
        return cached

    token = extract_bearer_token()
    if token is None:
        if required:
            raise AuthenticationError("Missing Authorization: Bearer <token> header.")
        return None

    context = decode_token(token)

    try:
        from app.extensions import db
        from models import RevokedToken, User

        revoked = (
            db.session.query(RevokedToken.id)
            .filter(RevokedToken.jti == context.token_id)
            .first()
        )
        if revoked is not None:
            raise AuthenticationError(
                "This session has been signed out. Please sign in again."
            )

        user = db.session.get(User, uuid.UUID(context.user_id))
        if user is not None and not user.can_authenticate:
            raise AuthenticationError(
                f"This account is {user.status.lower()} and cannot sign in."
            )
    except AuthenticationError:
        raise
    except (ValueError, SQLAlchemyError) as exc:
        # No database, or a subject id that is not a UUID. The signature,
        # expiry, issuer, audience and type checks above already passed.
        current_app.logger.debug("Skipping account checks during auth: %s", exc)

    g.auth_context = context
    return context


def current_user() -> AuthContext:
    context = g.get("auth_context")
    if context is None:
        raise AuthenticationError("This endpoint requires authentication.")
    return context


def require_authentication(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: the request must carry a valid, unrevoked access token.

    This is the primary authentication guard and the single implementation;
    ``require_auth`` is a shorter alias for it. Keeping one body means the two
    names cannot drift apart in behaviour.
    """

    from functools import wraps

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        load_current_user(required=True)
        return func(*args, **kwargs)

    return wrapper


def require_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Shorter alias for :func:`require_authentication`."""
    return require_authentication(func)


def roles_required(*roles: str | Role) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: allow only the listed roles (role inheritance applied)."""

    from functools import wraps

    allowed = {str(role) for role in roles}
    if not allowed:
        raise ValueError("roles_required() needs at least one role.")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_current_user(required=True)
            permitted = set(allowed)
            # Include roles that inherit into the allowed set.
            for role in Role.values():
                if role in permitted:
                    continue
                parent = _ROLE_INHERITS.get(role)
                while parent:
                    if parent in permitted:
                        permitted.add(role)
                        break
                    parent = _ROLE_INHERITS.get(parent)
            if context.role not in permitted:
                raise AuthorizationError(
                    f"Role '{context.role}' is not permitted here. "
                    f"Required: {', '.join(sorted(allowed))}."
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_role(*roles: str | Role) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: the caller must hold one of ``roles``.

    Role inheritance is applied, so ``require_role(VALET_MANAGER)`` also admits
    ``ADMIN``. Inheritance is declared explicitly in ``_ROLE_INHERITS``; adding
    a role grants nothing unless it is listed there.
    """
    return roles_required(*roles)


def capability_required(*capabilities: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: allow any principal holding all listed capabilities."""

    from functools import wraps

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_current_user(required=True)
            missing = [cap for cap in capabilities if not context.can(cap)]
            if missing:
                raise AuthorizationError(
                    f"Missing required capability: {', '.join(missing)}.",
                    details={"missing": missing},
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def constant_time_equals(left: str, right: str) -> bool:
    """Timing-safe string comparison (used for static API keys, not passwords)."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def parse_roles(raw: Iterable[str] | Sequence[str] | None) -> list[str]:
    """Validate a list of role strings, raising ``ValueError`` on unknown roles."""
    known = set(Role.values())
    out: list[str] = []
    for item in raw or []:
        value = str(item).upper()
        if value not in known:
            raise ValueError(f"Unknown role {item!r}. Expected one of {sorted(known)}.")
        if value not in out:
            out.append(value)
    return out
