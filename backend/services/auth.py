"""Authentication and account management.

This module owns every decision about who may sign in and what they get back.
Routes stay thin: they validate the payload, call a function here, and shape
the response.

Three properties are worth calling out, because they are the ones a security
review looks for:

**Passwords are never stored or logged in the clear.** ``hash_password`` salts
and hashes with PBKDF2-SHA256 at 600 000 iterations, and verification is
constant-time. No function here returns a password, and ``to_dict()`` on the
model omits the hash entirely.

**Login does not leak which emails exist.** A failed login always runs a hash
verification -- against a decoy hash when the address is unknown -- so the
response time does not differ between "no such account" and "wrong password",
and the message is identical in both cases.

**Public self-registration cannot create staff.** ``register()`` hard-codes the
role to ``CUSTOMER`` and never reads a role from the request. Staff accounts
are created only through ``create_user()``, which the route layer guards with
``require_role(ADMIN)``. The two paths are separate functions on purpose, so
there is no parameter that a crafted request could flip.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from models import (
    AuditAction,
    AuditOutcome,
    RevocationReason,
    RevokedToken,
    User,
    UserStatus,
)
from services.audit import record_event
from utils.exceptions import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from utils.rate_limit import clear_failed_logins, login_is_throttled, register_failed_login
from utils.security import (
    AuthContext,
    Role,
    TokenType,
    create_token,
    verify_password,
)
from utils.validation import validate_email, validate_password, validate_phone, validate_text, validate_uuid

logger = logging.getLogger(__name__)

#: Roles a member of the public may never self-assign. Enforced here as well as
#: in the route layer, so the rule holds even if a new route forgets it.
STAFF_ROLES: frozenset[str] = frozenset(
    {Role.VALET_OPERATOR.value, Role.VALET_MANAGER.value, Role.ADMIN.value}
)

#: A stable decoy so an unknown email costs the same time as a known one.
#: Not a credential: it is a fixed, publicly-visible hash string that matches
#: no password, used only to keep the timing of a failed login uniform.
_DECOY_PASSWORD_HASH = (  # noqa: S105
    "pbkdf2:sha256:600000$"
    "0123456789abcdef01234567$"
    "d1f2e3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2"
)

EMAIL_TAKEN_MESSAGE = (
    "An account with that email address already exists. "
    "If it is yours, try signing in instead."
)


class AccountDisabledError(AuthenticationError):
    """The account exists but may not authenticate right now."""


@dataclass(frozen=True)
class AuthResult:
    """What a successful sign-in hands back to the caller."""

    user: User
    access_token: str
    refresh_token: str
    access_expires_at: Any
    refresh_expires_at: Any

    def to_dict(self) -> dict:
        return {
            "user": self.user.to_dict(),
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": "Bearer",
            "expires_at": _iso(self.access_expires_at),
            "refresh_expires_at": _iso(self.refresh_expires_at),
        }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _audit(
    *,
    action: str,
    outcome: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor: User | None = None,
    failure_reason: str | None = None,
    new_values: dict | None = None,
    ip_address: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write an audit entry. Failures are logged too -- they are the interesting ones.

    A thin delegate to :func:`services.audit.record_event`, kept so this
    module's call sites stay readable. The IP address is passed explicitly here
    rather than harvested from the request because a failed login is recorded
    for an email that may not resolve to an account, and the caller has already
    worked out which address to attribute it to.
    """
    record_event(
        action=action,
        outcome=outcome,
        entity_type="users",
        entity_id=entity_id,
        description=description,
        actor=actor,
        failure_reason=failure_reason,
        new_values=new_values,
        metadata=metadata,
        ip_address=ip_address,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(
    *,
    email: str,
    password: str,
    full_name: str,
    phone: str | None = None,
    ip_address: str | None = None,
) -> User:
    """Create a customer account from a public registration request.

    The role is always ``CUSTOMER``. There is deliberately no ``role``
    parameter: a public caller cannot reach the staff-creation path by
    supplying a field.
    """
    clean_email = validate_email(email)
    clean_password = validate_password(password)
    clean_name = validate_text(full_name, field="full_name", min_length=2, max_length=120)
    clean_phone = validate_phone(phone, required=False)

    existing = db.session.query(User).filter(User.email == clean_email).one_or_none()
    if existing is not None:
        _audit(
            action=AuditAction.CREATE,
            outcome=str(AuditOutcome.FAILURE),
            entity_id=existing.id,
            description="Registration rejected: email already registered.",
            failure_reason=EMAIL_TAKEN_MESSAGE,
            ip_address=ip_address,
        )
        db.session.commit()
        raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"})

    user = User(
        email=clean_email,
        full_name=clean_name,
        phone=clean_phone,
        # Hard-coded. Never taken from the request.
        role=str(Role.CUSTOMER),
        status=str(UserStatus.ACTIVE),
        is_active=True,
    )
    user.set_password(clean_password)
    db.session.add(user)

    try:
        db.session.flush()
    except IntegrityError as exc:
        # Two concurrent registrations for the same address: the unique index
        # is the authority, so report the same conflict rather than a 500.
        db.session.rollback()
        logger.info("Concurrent registration for %s", clean_email)
        raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"}) from exc

    _audit(
        action=AuditAction.CREATE,
        outcome=str(AuditOutcome.SUCCESS),
        entity_id=user.id,
        description="Customer self-registered.",
        actor=user,
        new_values={"email": user.email, "role": user.role},
        ip_address=ip_address,
    )
    db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def authenticate(
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
) -> AuthResult:
    """Verify credentials and issue an access/refresh token pair.

    Always performs exactly one hash verification, so timing does not reveal
    whether the address exists, and always returns the same message on failure.
    """
    clean_email = validate_email(email)
    candidate = str(password or "")

    # Checked *before* the password hash runs. A throttle that ran after the
    # verification would still pay the full PBKDF2 cost on every guess, which is
    # exactly the expensive work the limiter exists to stop an attacker from
    # forcing us to repeat.
    throttled, retry_after = login_is_throttled(clean_email)
    if throttled:
        _audit(
            action=AuditAction.LOGIN_FAILED,
            outcome=str(AuditOutcome.DENIED),
            # No lookup has happened yet -- deliberately, so the throttle cannot
            # be used to probe whether an address exists. Nothing to reference.
            entity_id=None,
            description="Sign-in throttled: too many failed attempts.",
            failure_reason="rate_limited",
            ip_address=ip_address,
        )
        db.session.commit()
        raise RateLimitError(
            "Too many failed sign-in attempts. Please try again later.",
            details={"retry_after_seconds": retry_after},
        )

    user = db.session.query(User).filter(User.email == clean_email).one_or_none()
    stored_hash = user.password_hash if user is not None else _DECOY_PASSWORD_HASH

    # Runs in both branches: identical cost for a known and an unknown address.
    password_ok = verify_password(candidate, stored_hash)

    if user is None or not password_ok:
        register_failed_login(clean_email)
        _audit(
            action=AuditAction.LOGIN_FAILED,
            outcome=str(AuditOutcome.FAILURE),
            entity_id=user.id if user else None,
            actor=user,
            description="Sign-in rejected: unknown email or wrong password.",
            failure_reason="invalid_credentials",
            ip_address=ip_address,
        )
        db.session.commit()
        raise AuthenticationError("Email or password is incorrect.")

    if not user.can_authenticate:
        _audit(
            action=AuditAction.LOGIN_FAILED,
            outcome=str(AuditOutcome.DENIED),
            entity_id=user.id,
            actor=user,
            description=f"Sign-in denied: account is {user.status.lower()}.",
            failure_reason=f"account_{user.status.lower()}",
            ip_address=ip_address,
        )
        db.session.commit()
        raise AccountDisabledError(
            "This account is not able to sign in. Please contact the valet desk."
        )

    access_token, access_expiry = create_token(
        user_id=str(user.id), role=user.role, email=user.email
    )
    refresh_token, refresh_expiry = create_token(
        user_id=str(user.id),
        role=user.role,
        email=user.email,
        token_type=TokenType.REFRESH,
    )

    from datetime import UTC, datetime

    user.last_login_at = datetime.now(tz=UTC)
    _audit(
        action=AuditAction.USER_LOGIN,
        outcome=str(AuditOutcome.SUCCESS),
        entity_id=user.id,
        actor=user,
        description="Signed in.",
        new_values={"role": user.role},
        ip_address=ip_address,
    )
    db.session.commit()

    # A correct password retires the earlier misses: the owner who mistyped
    # twice should not stay one typo away from a lockout after getting it right.
    clear_failed_logins(clean_email)

    return AuthResult(
        user=user,
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expiry,
        refresh_expires_at=refresh_expiry,
    )


# ---------------------------------------------------------------------------
# Logout / revocation
# ---------------------------------------------------------------------------
def revoke_token(
    *,
    context: AuthContext,
    reason: str = RevocationReason.LOGOUT.value,
    ip_address: str | None = None,
) -> bool:
    """Denylist an access token's ``jti`` until it would have expired anyway.

    Returns ``True`` if a row was written, ``False`` if the token was already
    revoked -- so signing out twice is a no-op rather than a unique violation.
    """
    existing = (
        db.session.query(RevokedToken).filter(RevokedToken.jti == context.token_id).one_or_none()
    )
    if existing is not None:
        return False

    db.session.add(
        RevokedToken(
            jti=context.token_id,
            user_id=uuid.UUID(context.user_id),
            expires_at=context.expires_at,
            reason=reason,
            ip_address=ip_address,
        )
    )
    db.session.commit()
    return True


def is_token_revoked(jti: str) -> bool:
    """True if this token id has been signed out or otherwise revoked."""
    return (
        db.session.query(RevokedToken.id).filter(RevokedToken.jti == jti).first() is not None
    )


def logout(
    *,
    context: AuthContext,
    ip_address: str | None = None,
) -> None:
    """Sign the caller out: revoke the presented access token."""
    revoked = revoke_token(context=context, ip_address=ip_address)
    user = get_user_by_id(context.user_id)
    _audit(
        action=AuditAction.LOGOUT,
        outcome=str(AuditOutcome.SUCCESS),
        entity_id=user.id if user else None,
        actor=user,
        description=(
            "Signed out; access token revoked."
            if revoked
            else "Signed out (token was already revoked)."
        ),
        ip_address=ip_address,
    )
    db.session.commit()


def prune_revoked_tokens() -> int:
    """Delete denylist rows whose tokens have expired by their own ``exp``.

    Those tokens are already rejected by signature/expiry checks, so the rows
    are dead weight. Safe to run on a schedule.
    """
    from datetime import UTC, datetime

    deleted = (
        db.session.query(RevokedToken)
        .filter(RevokedToken.expires_at <= datetime.now(tz=UTC))
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return int(deleted or 0)


# ---------------------------------------------------------------------------
# Account lookup
# ---------------------------------------------------------------------------
def get_user_by_id(user_id: str | uuid.UUID) -> User | None:
    try:
        key = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    return db.session.get(User, key)


def get_current_profile(context: AuthContext) -> User:
    """Load the signed-in user for ``/auth/me``.

    Rejects a token whose subject no longer exists or has been deactivated, so
    a stale token cannot keep reading a profile.
    """
    user = get_user_by_id(context.user_id)
    if user is None:
        raise AuthenticationError("The account for this session no longer exists.")
    if not user.can_authenticate:
        raise AccountDisabledError("This account is not able to sign in.")
    return user


# ---------------------------------------------------------------------------
# Staff account management (ADMIN only - enforced in the route layer)
# ---------------------------------------------------------------------------
def create_user(
    *,
    email: str,
    password: str,
    full_name: str,
    role: str,
    phone: str | None = None,
    actor: User | None = None,
    ip_address: str | None = None,
) -> User:
    """Create an account with an explicit role. Restricted to administrators.

    This is the *only* path that can create a staff account, and the route that
    exposes it is guarded by ``require_role(ADMIN)``. The role is validated
    against the four-value vocabulary here as well, so an unknown role cannot
    reach the database.
    """
    if role not in Role.values():
        raise ValidationError(
            f"'role' must be one of {', '.join(Role.values())}.",
            details={"field": "role", "allowed": Role.values()},
        )

    clean_email = validate_email(email)
    clean_password = validate_password(password)
    clean_name = validate_text(full_name, field="full_name", min_length=2, max_length=120)
    clean_phone = validate_phone(phone, required=False)

    if db.session.query(User).filter(User.email == clean_email).one_or_none() is not None:
        raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"})

    user = User(
        email=clean_email,
        full_name=clean_name,
        phone=clean_phone,
        role=str(Role(role)),
        status=str(UserStatus.ACTIVE),
        is_active=True,
    )
    user.set_password(clean_password)
    db.session.add(user)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"}) from exc

    _audit(
        action=AuditAction.CREATE,
        outcome=str(AuditOutcome.SUCCESS),
        entity_id=user.id,
        actor=actor,
        description=f"Administrator created a {user.role} account.",
        new_values={"email": user.email, "role": user.role},
        ip_address=ip_address,
    )
    db.session.commit()
    return user



# Account management is audit-preserving: the UUID is immutable, and "delete"
# deactivates the account rather than breaking historical tickets/audit records.
MANAGEABLE_ROLES: frozenset[str] = frozenset({
    Role.CUSTOMER.value, Role.VALET_OPERATOR.value, Role.VALET_MANAGER.value,
})
EDITABLE_ACCOUNT_FIELDS: frozenset[str] = frozenset({"full_name", "email", "phone", "role"})


def _managed_account(user_id: str) -> User:
    key = validate_uuid(user_id, field="user_id")
    user = db.session.get(User, key)
    if user is None:
        raise NotFoundError("Account not found.")
    if user.role == Role.ADMIN.value:
        raise AuthorizationError("Administrator accounts cannot be edited from Staff Accounts.")
    return user


def list_accounts(
    *, actor: User | None, page: int = 1, page_size: int = 20,
    search: str = "", role: str | None = None, status: str | None = None,
) -> dict:
    """Paginated account directory; only admins can see user emails/phones."""
    assert_admin(actor)
    if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
        raise ValidationError("Invalid pagination parameters.")
    if role and role not in Role.values():
        raise ValidationError("Invalid role filter.", details={"field": "role"})
    if status and status not in [item.value for item in UserStatus]:
        raise ValidationError("Invalid status filter.", details={"field": "status"})
    if len(search) > 120:
        raise ValidationError("Search text is too long.")
    query = db.session.query(User)
    if search.strip():
        # Quote SQL wildcard characters so a literal search stays literal.
        term = search.strip().replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{term}%"
        query = query.filter(or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\")))
    if role:
        query = query.filter(User.role == role)
    if status:
        query = query.filter(User.status == status)
    total = query.count()
    accounts = (
        query.order_by(User.created_at.desc(), User.email.asc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return {
        "users": [public_profile(user) for user in accounts],
        "page": page, "page_size": page_size, "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def update_account(
    *, user_id: str, changes: dict, actor: User | None, ip_address: str | None = None,
) -> User:
    """Edit a customer/valet/manager; never accept password or UUID in a patch."""
    assert_admin(actor)
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("Select at least one account field to update.")
    unexpected = set(changes) - EDITABLE_ACCOUNT_FIELDS
    if unexpected:
        raise ValidationError("Unsupported account fields.", details={"fields": sorted(unexpected)})
    user = _managed_account(user_id)
    before = {key: getattr(user, key) for key in changes}
    if "full_name" in changes:
        user.full_name = validate_text(changes["full_name"], field="full_name", min_length=2, max_length=120)
    if "email" in changes:
        clean_email = validate_email(changes["email"])
        # Check before assigning to avoid autoflush turning a duplicate into a 500.
        taken = db.session.query(User.id).filter(User.email == clean_email, User.id != user.id).first()
        if taken:
            raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"})
        user.email = clean_email
    if "phone" in changes:
        user.phone = validate_phone(changes["phone"], required=False)
    if "role" in changes:
        role = str(changes["role"] or "").upper()
        if role not in MANAGEABLE_ROLES:
            raise ValidationError("Only Customer, Valet or Manager roles can be assigned here.", details={"field": "role"})
        user.role = role
    after = {key: getattr(user, key) for key in changes}
    if before != after:
        _audit(action=AuditAction.UPDATE, outcome=str(AuditOutcome.SUCCESS),
               entity_id=user.id, actor=actor, description="Administrator updated an account.",
               new_values=after, ip_address=ip_address,
               metadata={"changed_fields": sorted(changes)})
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            raise ConflictError(EMAIL_TAKEN_MESSAGE, details={"field": "email"}) from exc
    return user


def reset_account_password(
    *, user_id: str, password: str, actor: User | None, ip_address: str | None = None,
) -> User:
    """Set a new password without ever reading or returning the old one."""
    assert_admin(actor)
    user = _managed_account(user_id)
    user.set_password(validate_password(password))
    _audit(action=AuditAction.UPDATE, outcome=str(AuditOutcome.SUCCESS),
           entity_id=user.id, actor=actor, description="Administrator reset an account password.",
           metadata={"changed_fields": ["password"]}, ip_address=ip_address)
    db.session.commit()
    return user


def deactivate_account(*, user_id: str, actor: User | None, ip_address: str | None = None) -> User:
    """Reversible soft delete, keeping every existing audit/ticket relationship."""
    assert_admin(actor)
    user = _managed_account(user_id)
    if user.status == UserStatus.DEACTIVATED.value:
        raise ConflictError("Account is already deactivated.")
    old_status = user.status
    user.status = UserStatus.DEACTIVATED.value
    user.is_active = False
    user.deactivated_at = datetime.now(tz=UTC)
    _audit(action=AuditAction.DELETE, outcome=str(AuditOutcome.SUCCESS),
           entity_id=user.id, actor=actor, description="Administrator deactivated an account (soft delete).",
           new_values={"status": user.status, "previous_status": old_status}, ip_address=ip_address)
    db.session.commit()
    return user


def reactivate_account(*, user_id: str, actor: User | None, ip_address: str | None = None) -> User:
    assert_admin(actor)
    user = _managed_account(user_id)
    if user.status != UserStatus.DEACTIVATED.value:
        raise ConflictError("Only deactivated accounts can be reactivated.")
    user.status = UserStatus.ACTIVE.value
    user.is_active = True
    user.deactivated_at = None
    _audit(action=AuditAction.STATUS_CHANGE, outcome=str(AuditOutcome.SUCCESS),
           entity_id=user.id, actor=actor, description="Administrator reactivated an account.",
           new_values={"status": user.status}, ip_address=ip_address)
    db.session.commit()
    return user


def assert_admin(actor: User | None) -> None:
    """Guard for callers that must be an administrator.

    Routes use the ``require_role`` decorator; this exists for service-level
    callers so the rule is not only enforced at the HTTP boundary.
    """
    if actor is None or actor.role != Role.ADMIN.value:
        raise AuthorizationError("Only an administrator may perform this action.")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def public_profile(user: User) -> dict:
    """What the API may reveal about an account. Never includes the hash."""
    payload = user.to_dict()
    payload.pop("password_hash", None)  # to_dict already omits it; belt and braces
    return payload


__all__ = [
    "EMAIL_TAKEN_MESSAGE",
    "STAFF_ROLES",
    "AccountDisabledError",
    "ApiError",
    "AuthResult",
    "authenticate",
    "assert_admin",
    "create_user",
    "list_accounts",
    "update_account",
    "reset_account_password",
    "deactivate_account",
    "reactivate_account",
    "get_current_profile",
    "get_user_by_id",
    "is_token_revoked",
    "logout",
    "prune_revoked_tokens",
    "public_profile",
    "register",
    "revoke_token",
]
