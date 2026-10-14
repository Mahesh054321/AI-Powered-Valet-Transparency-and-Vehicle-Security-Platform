"""Authentication endpoints.

    POST /auth/register   public  - create a CUSTOMER account
    POST /auth/login      public  - exchange credentials for tokens
    POST /auth/logout     auth    - revoke the presented access token
    GET  /auth/me         auth    - the signed-in account
    POST /auth/users      ADMIN   - create a staff account (the only path that can)

Mounted twice, deliberately: the canonical versioned path
``/api/v1/auth/...`` and the unversioned ``/api/auth/...``. Both are served so
the documented contract holds whichever form a client uses.

Handlers stay thin. Validation lives in ``utils/validation``, every decision in
``services/auth``, and the response shape in ``utils/responses`` -- so there is
exactly one place each rule is written.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import auth as auth_service
from utils.responses import created, no_content, ok
from utils.security import (
    Role,
    current_user,
    load_current_user,
    require_authentication,
    require_role,
)
from utils.validation import require_fields


def _client_ip() -> str | None:
    """Best-effort client address for the audit trail.

    ``remote_addr`` is the peer address, which behind a reverse proxy is the
    proxy. Reading ``X-Forwarded-For`` would be spoofable unless the proxy
    strips it, so it is recorded only as a separate, clearly-untrusted hint.
    """
    return request.remote_addr


def _payload() -> dict:
    body = request.get_json(silent=True)
    if body is None:
        from utils.exceptions import ValidationError

        raise ValidationError(
            "Request body must be JSON with a Content-Type of application/json."
        )
    return body


def create_auth_blueprint() -> Blueprint:
    """Build a fresh auth blueprint.

    A new instance per call, because Flask refuses to register the same
    blueprint object twice -- and this one is registered at two prefixes, in
    every app the test suite builds.
    """
    # No url_prefix here on purpose: `register_blueprint(url_prefix=...)`
    # *overrides* a prefix set in the constructor rather than appending to it.
    # Because this blueprint is mounted at two different prefixes, the caller
    # must be the one that decides where it lands.
    blueprint = Blueprint("auth", __name__)

    @blueprint.post("/register")
    def register():
        """Create a customer account.

        A public caller cannot choose their role: ``services.auth.register``
        hard-codes ``CUSTOMER`` and ignores any ``role`` field. Supplying a
        staff role is rejected explicitly rather than silently downgraded, so a
        client cannot believe it was granted privileges.
        """
        body = require_fields(_payload(), "email", "password", "full_name")

        requested_role = body.get("role")
        if requested_role is not None and str(requested_role).upper() != Role.CUSTOMER.value:
            from utils.exceptions import AuthorizationError

            raise AuthorizationError(
                f"Role '{requested_role}' cannot be self-assigned. Public "
                "registration creates customer accounts; staff accounts are "
                "created by an administrator.",
                details={"field": "role", "permitted": Role.CUSTOMER.value},
            )

        user = auth_service.register(
            email=body["email"],
            password=body["password"],
            full_name=body["full_name"],
            phone=body.get("phone"),
            ip_address=_client_ip(),
        )
        return created(
            {
                "user": auth_service.public_profile(user),
                "message": "Account created. You can sign in now.",
            }
        )

    @blueprint.post("/login")
    def login():
        """Exchange an email and password for an access/refresh token pair."""
        body = require_fields(_payload(), "email", "password")

        result = auth_service.authenticate(
            email=body["email"], password=body["password"], ip_address=_client_ip()
        )
        return ok(result.to_dict())

    @blueprint.post("/logout")
    @require_authentication
    def logout():
        """Revoke the presented access token.

        JWTs are stateless, so the client discarding its copy is not enough:
        the ``jti`` is denylisted here so the token stops working everywhere,
        including any copy that was captured before the sign-out.
        """
        auth_service.logout(context=current_user(), ip_address=_client_ip())
        return ok({"message": "Signed out. The access token has been revoked."})

    @blueprint.get("/me")
    @require_authentication
    def me():
        """The signed-in account, as recorded in the database.

        Read from the ``users`` row rather than the token claims, so a role
        change or a suspension takes effect immediately instead of at the next
        sign-in.
        """
        context = load_current_user(required=True)
        user = auth_service.get_current_profile(context)
        return ok({"user": auth_service.public_profile(user)})

    @blueprint.post("/users")
    @require_role(Role.ADMIN)
    def create_user():
        """Create a staff account. Administrators only.

        This is the only endpoint that can create a ``VALET_OPERATOR``,
        ``VALET_MANAGER`` or ``ADMIN``.
        """
        body = require_fields(_payload(), "email", "password", "full_name", "role")

        user = auth_service.create_user(
            email=body["email"],
            password=body["password"],
            full_name=body["full_name"],
            role=str(body["role"]).upper(),
            phone=body.get("phone"),
            actor=auth_service.get_user_by_id(current_user().user_id),
            ip_address=_client_ip(),
        )
        return created(
            {"user": auth_service.public_profile(user), "message": f"{user.role} account created."}
        )

    return blueprint


__all__ = ["create_auth_blueprint", "no_content"]
