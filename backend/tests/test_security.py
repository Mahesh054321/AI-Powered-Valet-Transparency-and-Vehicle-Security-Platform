"""Phase 1 tests: password hashing, JWT lifecycle and role-based authorization."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from utils.exceptions import AuthenticationError, AuthorizationError
from utils.security import (
    Role,
    TokenType,
    capabilities_for,
    create_token,
    decode_token,
    has_capability,
    hash_password,
    parse_roles,
    verify_password,
)


class TestPasswords:
    def test_hash_is_salted_and_not_plaintext(self, app):
        with app.app_context():
            digest = hash_password("Correct-Horse-1")
        assert digest != "Correct-Horse-1"
        assert "Correct-Horse-1" not in digest
        assert digest.startswith("pbkdf2:sha256:")

    def test_same_password_hashes_differently(self, app):
        with app.app_context():
            first = hash_password("Correct-Horse-1")
            second = hash_password("Correct-Horse-1")
        assert first != second  # distinct salts

    def test_verify_accepts_correct_password(self, app):
        with app.app_context():
            digest = hash_password("Correct-Horse-1")
            assert verify_password("Correct-Horse-1", digest) is True

    def test_verify_rejects_wrong_password(self, app):
        with app.app_context():
            digest = hash_password("Correct-Horse-1")
            assert verify_password("Wrong-Horse-2", digest) is False

    def test_verify_rejects_malformed_hash(self, app):
        with app.app_context():
            assert verify_password("anything", "not-a-real-hash") is False
            assert verify_password("", "pbkdf2:sha256:1$x$y") is False

    def test_empty_password_is_rejected(self, app):
        with app.app_context(), pytest.raises(ValueError, match="must not be empty"):
            hash_password("")


class TestJwt:
    def test_access_token_round_trips(self, app):
        with app.app_context():
            token, expires_at = create_token(
                user_id="user-1", role=Role.VALET_OPERATOR, email="op@valet.local"
            )
            context = decode_token(token)
        assert context.user_id == "user-1"
        assert context.role == Role.VALET_OPERATOR
        assert context.email == "op@valet.local"
        assert context.token_type == TokenType.ACCESS
        assert expires_at.tzinfo is not None

    def test_token_carries_required_claims(self, app):
        with app.app_context():
            token, _ = create_token(user_id="user-1", role=Role.CUSTOMER)
            claims = pyjwt.decode(
                token,
                app.config["JWT_SECRET_KEY"],
                algorithms=[app.config["JWT_ALGORITHM"]],
                audience=app.config["JWT_AUDIENCE"],
                issuer=app.config["JWT_ISSUER"],
            )
        for claim in ("sub", "role", "typ", "jti", "iss", "aud", "iat", "nbf", "exp"):
            assert claim in claims, f"missing claim: {claim}"

    def test_expired_token_is_rejected(self, app):
        with app.app_context():
            app.config["JWT_ACCESS_TOKEN_TTL_MINUTES"] = -1
            token, _ = create_token(user_id="user-1", role=Role.CUSTOMER)
            app.config["JWT_ACCESS_TOKEN_TTL_MINUTES"] = 30
            with pytest.raises(AuthenticationError, match="expired"):
                decode_token(token)

    def test_tampered_signature_is_rejected(self, app):
        with app.app_context():
            token, _ = create_token(user_id="user-1", role=Role.CUSTOMER)
        tampered = token[:-4] + ("abcd" if not token.endswith("abcd") else "wxyz")
        with app.app_context(), pytest.raises(AuthenticationError, match="invalid or malformed"):
            decode_token(tampered)

    def test_wrong_audience_is_rejected(self, app):
        with app.app_context():
            forged = pyjwt.encode(
                {
                    "sub": "user-1",
                    "role": Role.ADMIN,
                    "typ": "access",
                    "jti": "j",
                    "iss": app.config["JWT_ISSUER"],
                    "aud": "somebody-else",
                    "iat": int(time.time()),
                    "nbf": int(time.time()),
                    "exp": int(time.time()) + 600,
                },
                app.config["JWT_SECRET_KEY"],
                algorithm="HS256",
            )
            with pytest.raises(AuthenticationError, match="audience"):
                decode_token(forged)

    def test_algorithm_none_is_rejected(self, app):
        forged = pyjwt.encode(
            {"sub": "u", "role": Role.ADMIN, "typ": "access", "jti": "j"},
            key="",
            algorithm="none",
        )
        with app.app_context(), pytest.raises(AuthenticationError):
            decode_token(forged)

    def test_refresh_token_cannot_be_used_as_access_token(self, app):
        with app.app_context():
            refresh, _ = create_token(
                user_id="user-1", role=Role.CUSTOMER, token_type=TokenType.REFRESH
            )
            with pytest.raises(AuthenticationError, match="Expected a access token"):
                decode_token(refresh, expected_type=TokenType.ACCESS)
            context = decode_token(refresh, expected_type=TokenType.REFRESH)
        assert context.token_type == TokenType.REFRESH

    def test_unknown_role_in_token_is_rejected(self, app):
        with app.app_context():
            forged = pyjwt.encode(
                {
                    "sub": "user-1",
                    "role": "SUPERUSER",
                    "typ": "access",
                    "jti": "j",
                    "iss": app.config["JWT_ISSUER"],
                    "aud": app.config["JWT_AUDIENCE"],
                    "iat": int(time.time()),
                    "nbf": int(time.time()),
                    "exp": int(time.time()) + 600,
                },
                app.config["JWT_SECRET_KEY"],
                algorithm="HS256",
            )
            with pytest.raises(AuthenticationError, match="unknown role"):
                decode_token(forged)


class TestRbac:
    def test_every_role_is_represented(self):
        assert Role.values() == ["CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN"]

    def test_customer_cannot_read_all_tickets(self):
        assert has_capability(Role.CUSTOMER, "ticket:read_all") is False
        assert has_capability(Role.CUSTOMER, "ticket:read_own") is True

    def test_manager_inherits_operator_capabilities(self):
        operator_caps = capabilities_for(Role.VALET_OPERATOR)
        manager_caps = capabilities_for(Role.VALET_MANAGER)
        assert operator_caps <= manager_caps

    def test_admin_inherits_manager_capabilities(self):
        assert capabilities_for(Role.VALET_MANAGER) <= capabilities_for(Role.ADMIN)

    def test_unknown_role_has_no_capabilities(self):
        assert capabilities_for("GUEST") == set()

    def test_parse_roles_normalises_and_validates(self):
        assert parse_roles(["customer", "ADMIN"]) == ["CUSTOMER", "ADMIN"]
        with pytest.raises(ValueError, match="Unknown role"):
            parse_roles(["ROOT"])

    def test_roles_required_blocks_wrong_role(self, app, client, auth_headers):
        from flask import Blueprint

        from utils.responses import ok
        from utils.security import roles_required

        probe = Blueprint("probe", __name__)

        @probe.get("/manager-only")
        @roles_required(Role.VALET_MANAGER)
        def manager_only():
            return ok({"granted": True})

        app.register_blueprint(probe, url_prefix="/api/v1/probe")

        allowed = client.get("/api/v1/probe/manager-only", headers=auth_headers(Role.ADMIN))
        assert allowed.status_code == 200

        blocked = client.get("/api/v1/probe/manager-only", headers=auth_headers(Role.CUSTOMER))
        assert blocked.status_code == 403
        assert blocked.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"

    def test_endpoint_requires_authentication(self, app, client):
        from flask import Blueprint

        from utils.responses import ok
        from utils.security import require_auth

        probe = Blueprint("probe2", __name__)

        @probe.get("/protected")
        @require_auth
        def protected():
            return ok({"ok": True})

        app.register_blueprint(probe, url_prefix="/api/v1/probe2")

        anonymous = client.get("/api/v1/probe2/protected")
        assert anonymous.status_code == 401
        assert anonymous.get_json()["error"]["code"] == "AUTHENTICATION_ERROR"

    def test_authorization_error_carries_required_roles(self):
        error = AuthorizationError("nope")
        assert error.status_code == 403
        assert error.to_dict()["code"] == "AUTHORIZATION_ERROR"


class TestExpiryMath:
    def test_access_expiry_matches_configured_ttl(self, app):
        with app.app_context():
            before = datetime.now(tz=UTC)
            _, expires_at = create_token(user_id="u", role=Role.CUSTOMER)
            expected = before + timedelta(minutes=app.config["JWT_ACCESS_TOKEN_TTL_MINUTES"])
        assert abs((expires_at - expected).total_seconds()) < 5
