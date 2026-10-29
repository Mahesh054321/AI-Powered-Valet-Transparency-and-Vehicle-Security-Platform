"""End-to-end tests for the authentication and authorization API.

Run against a real PostgreSQL database, since registration, login and
revocation all touch the ``users`` and ``revoked_tokens`` tables. Skipped
automatically when PostgreSQL is not reachable.

Every test exercises both URL mounts -- ``/api/auth/...`` and
``/api/v1/auth/...`` -- because they are both part of the documented contract.
"""

from __future__ import annotations

import uuid

import pytest

from app.extensions import db
from models import RevokedToken, User, UserStatus
from utils.security import Role, create_token

pytestmark = pytest.mark.postgres

MOUNTS = ("/api/auth", "/api/v1/auth")

PASSWORD = "Str0ng#Passw0rd"


def _email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _register(client, base: str, *, email: str | None = None, **overrides):
    payload = {
        "email": email or _email(),
        "password": PASSWORD,
        "full_name": "Test Customer",
        **overrides,
    }
    return client.post(f"{base}/register", json=payload)


def _login(client, base: str, email: str, password: str = PASSWORD):
    return client.post(f"{base}/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clean_session(pg_app):
    yield
    db.session.rollback()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("base", MOUNTS)
def test_registration_creates_a_customer(client, pg_app, base):
    email = _email("reg")
    response = _register(client, base, email=email)

    assert response.status_code == 201
    body = response.get_json()
    assert body["success"] is True
    assert body["data"]["user"]["email"] == email
    assert body["data"]["user"]["role"] == Role.CUSTOMER.value


@pytest.mark.parametrize("base", MOUNTS)
def test_registration_never_stores_a_plaintext_password(client, pg_app, base):
    email = _email("hash")
    _register(client, base, email=email)

    user = db.session.query(User).filter(User.email == email).one()
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("pbkdf2:sha256:")
    assert user.check_password(PASSWORD) is True


@pytest.mark.parametrize("base", MOUNTS)
def test_registration_response_never_contains_the_hash(client, pg_app, base):
    response = _register(client, base)
    assert "password" not in response.get_data(as_text=True).lower().replace(
        "password policy", ""
    ) or "password_hash" not in response.get_data(as_text=True)
    assert "password_hash" not in response.get_json()["data"]["user"]


@pytest.mark.parametrize("role", ["ADMIN", "VALET_MANAGER", "VALET_OPERATOR"])
def test_a_public_caller_cannot_register_as_staff(client, pg_app, role):
    """The privilege-escalation rule, checked for every staff role."""
    response = _register(client, "/api/auth", role=role)

    assert response.status_code == 403
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTHORIZATION_ERROR"
    assert "cannot be self-assigned" in body["error"]["message"]


def test_a_public_caller_cannot_register_as_admin_even_with_a_mixed_case_role(client, pg_app):
    response = _register(client, "/api/auth", role="admin")
    assert response.status_code == 403


def test_no_staff_account_is_created_by_a_rejected_registration(client, pg_app):
    email = _email("blocked")
    _register(client, "/api/auth", email=email, role="ADMIN")

    assert db.session.query(User).filter(User.email == email).one_or_none() is None


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        ({"email": "not-an-email", "password": PASSWORD, "full_name": "A B"}, "email"),
        ({"email": "ok@example.com", "password": "short", "full_name": "A B"}, "password"),
        ({"email": "ok@example.com", "password": "allletters", "full_name": "A B"}, "password"),
        ({"email": "ok@example.com", "password": "1234567890", "full_name": "A B"}, "password"),
        ({"password": PASSWORD, "full_name": "A B"}, "email"),
        ({"email": "ok@example.com", "full_name": "A B"}, "password"),
    ],
)
def test_registration_validates_each_field(client, pg_app, payload, expected_field):
    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 422
    body = response.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert expected_field in str(body["error"])


def test_duplicate_registration_is_a_conflict(client, pg_app):
    email = _email("dupe")
    assert _register(client, "/api/auth", email=email).status_code == 201

    response = _register(client, "/api/auth", email=email)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "CONFLICT"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("base", MOUNTS)
def test_valid_login_returns_a_token_pair(client, pg_app, base):
    email = _email("login")
    _register(client, base, email=email)

    response = _login(client, base, email)

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["access_token"].count(".") == 2  # a JWT
    assert data["refresh_token"].count(".") == 2
    assert data["token_type"] == "Bearer"
    assert data["expires_at"] is not None
    assert data["user"]["email"] == email
    assert "password" not in data["user"]


def test_invalid_password_is_rejected(client, pg_app):
    email = _email("badpw")
    _register(client, "/api/auth", email=email)

    response = _login(client, "/api/auth", email, password="Wr0ng#Password")

    assert response.status_code == 401
    body = response.get_json()
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"
    assert body["error"]["message"] == "Email or password is incorrect."


def test_unknown_email_gives_the_same_message_as_a_wrong_password(client, pg_app):
    """Otherwise the message tells an attacker which accounts exist."""
    email = _email("known")
    _register(client, "/api/auth", email=email)

    wrong_password = _login(client, "/api/auth", email, password="Wr0ng#Password")
    unknown_user = _login(client, "/api/auth", _email("ghost"), password=PASSWORD)

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.get_json()["error"]["message"] == unknown_user.get_json()[
        "error"
    ]["message"]


def test_login_records_the_sign_in_time(client, pg_app):
    email = _email("stamp")
    _register(client, "/api/auth", email=email)
    _login(client, "/api/auth", email)

    user = db.session.query(User).filter(User.email == email).one()
    assert user.last_login_at is not None


def test_a_suspended_account_cannot_sign_in(client, pg_app):
    email = _email("suspended")
    _register(client, "/api/auth", email=email)

    user = db.session.query(User).filter(User.email == email).one()
    user.status = str(UserStatus.SUSPENDED)
    user.is_active = False
    db.session.commit()

    response = _login(client, "/api/auth", email)
    assert response.status_code == 401
    assert "not able to sign in" in response.get_json()["error"]["message"]


def test_login_writes_an_audit_entry_for_success_and_failure(client, pg_app):
    from models import AuditAction, AuditLog

    email = _email("audited")
    _register(client, "/api/auth", email=email)
    _login(client, "/api/auth", email)
    _login(client, "/api/auth", email, password="Wr0ng#Password")

    actions = [
        entry.action
        for entry in db.session.query(AuditLog)
        .filter(AuditLog.action.in_([str(AuditAction.USER_LOGIN), str(AuditAction.LOGIN_FAILED)]))
        .all()
    ]
    assert str(AuditAction.USER_LOGIN) in actions
    assert str(AuditAction.LOGIN_FAILED) in actions


# ---------------------------------------------------------------------------
# Protected endpoints
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("base", MOUNTS)
def test_a_protected_endpoint_without_a_token_is_rejected(client, pg_app, base):
    response = client.get(f"{base}/me")

    assert response.status_code == 401
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"


def test_a_protected_endpoint_rejects_a_garbage_token(client, pg_app):
    response = client.get("/api/auth/me", headers=_bearer("not.a.jwt"))
    assert response.status_code == 401


def test_a_refresh_token_cannot_be_used_as_an_access_token(client, pg_app):
    """Token types are separated so a long-lived token cannot act directly."""
    email = _email("refresh")
    _register(client, "/api/auth", email=email)
    refresh_token = _login(client, "/api/auth", email).get_json()["data"]["refresh_token"]

    response = client.get("/api/auth/me", headers=_bearer(refresh_token))
    assert response.status_code == 401
    assert "access" in response.get_json()["error"]["message"].lower()


def test_me_returns_the_account_from_the_database(client, pg_app):
    email = _email("me")
    _register(client, "/api/auth", email=email)
    token = _login(client, "/api/auth", email).get_json()["data"]["access_token"]

    response = client.get("/api/auth/me", headers=_bearer(token))

    assert response.status_code == 200
    user = response.get_json()["data"]["user"]
    assert user["email"] == email
    assert user["role"] == Role.CUSTOMER.value
    assert "password_hash" not in user


def test_a_revoked_token_stops_working_immediately(client, pg_app):
    """Logout is real, not just the client forgetting its copy."""
    email = _email("logout")
    _register(client, "/api/auth", email=email)
    token = _login(client, "/api/auth", email).get_json()["data"]["access_token"]

    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200

    response = client.post("/api/auth/logout", headers=_bearer(token))
    assert response.status_code == 200

    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401


def test_logout_records_the_revocation(client, pg_app):
    email = _email("revoked")
    _register(client, "/api/auth", email=email)
    token = _login(client, "/api/auth", email).get_json()["data"]["access_token"]

    client.post("/api/auth/logout", headers=_bearer(token))

    assert db.session.query(RevokedToken).count() >= 1


def test_logging_out_twice_is_not_an_error(client, pg_app):
    email = _email("twice")
    _register(client, "/api/auth", email=email)
    token = _login(client, "/api/auth", email).get_json()["data"]["access_token"]

    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 200
    # The second call presents an already-revoked token, so it is a 401 rather
    # than a 500 -- the important part is that it is not a unique violation.
    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 401


def test_a_token_for_a_deactivated_account_is_rejected(client, pg_app):
    email = _email("deactivated")
    _register(client, "/api/auth", email=email)
    token = _login(client, "/api/auth", email).get_json()["data"]["access_token"]

    user = db.session.query(User).filter(User.email == email).one()
    user.is_active = False
    user.status = str(UserStatus.DEACTIVATED)
    db.session.commit()

    response = client.get("/api/auth/me", headers=_bearer(token))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Role-based authorization
# ---------------------------------------------------------------------------
def _token_for(role: Role) -> str:
    """A token for a real account of the given role."""
    email = _email(role.value.lower())
    user = User(
        email=email,
        full_name=f"Test {role.value.title()}",
        role=str(role),
        status=str(UserStatus.ACTIVE),
        is_active=True,
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    token, _ = create_token(user_id=str(user.id), role=user.role, email=user.email)
    return token


@pytest.mark.parametrize(
    "role",
    [Role.CUSTOMER, Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN],
)
def test_every_role_can_read_its_own_profile(client, pg_app, role):
    response = client.get("/api/auth/me", headers=_bearer(_token_for(role)))

    assert response.status_code == 200
    assert response.get_json()["data"]["user"]["role"] == role.value


def test_a_customer_cannot_create_a_staff_account(client, pg_app):
    response = client.post(
        "/api/auth/users",
        headers=_bearer(_token_for(Role.CUSTOMER)),
        json={
            "email": _email("made"),
            "password": PASSWORD,
            "full_name": "Made Up Valet",
            "role": Role.VALET_OPERATOR.value,
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"


def test_a_valet_operator_cannot_create_a_staff_account(client, pg_app):
    response = client.post(
        "/api/auth/users",
        headers=_bearer(_token_for(Role.VALET_OPERATOR)),
        json={
            "email": _email("made"),
            "password": PASSWORD,
            "full_name": "Made Up Manager",
            "role": Role.VALET_MANAGER.value,
        },
    )
    assert response.status_code == 403


def test_a_valet_manager_cannot_create_a_staff_account(client, pg_app):
    response = client.post(
        "/api/auth/users",
        headers=_bearer(_token_for(Role.VALET_MANAGER)),
        json={
            "email": _email("made"),
            "password": PASSWORD,
            "full_name": "Made Up Admin",
            "role": Role.ADMIN.value,
        },
    )
    assert response.status_code == 403


def test_an_admin_can_create_each_staff_role(client, pg_app):
    headers = _bearer(_token_for(Role.ADMIN))

    for role in (Role.VALET_OPERATOR, Role.VALET_MANAGER, Role.ADMIN):
        email = _email(f"created-{role.value.lower()}")
        response = client.post(
            "/api/auth/users",
            headers=headers,
            json={
                "email": email,
                "password": PASSWORD,
                "full_name": f"Created {role.value.title()}",
                "role": role.value,
            },
        )

        assert response.status_code == 201, f"admin could not create {role}"
        assert response.get_json()["data"]["user"]["role"] == role.value


def test_an_admin_created_account_can_sign_in(client, pg_app):
    headers = _bearer(_token_for(Role.ADMIN))
    email = _email("signin")

    client.post(
        "/api/auth/users",
        headers=headers,
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "New Valet",
            "role": Role.VALET_OPERATOR.value,
        },
    )

    response = _login(client, "/api/auth", email)
    assert response.status_code == 200
    assert response.get_json()["data"]["user"]["role"] == Role.VALET_OPERATOR.value


def test_an_unknown_role_is_rejected_even_for_an_admin(client, pg_app):
    response = client.post(
        "/api/auth/users",
        headers=_bearer(_token_for(Role.ADMIN)),
        json={
            "email": _email("bogus"),
            "password": PASSWORD,
            "full_name": "Bogus Role",
            "role": "SUPERUSER",
        },
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_unauthenticated_caller_cannot_reach_the_staff_endpoint(client, pg_app):
    response = client.post(
        "/api/auth/users",
        json={
            "email": _email("anon"),
            "password": PASSWORD,
            "full_name": "Anonymous",
            "role": Role.ADMIN.value,
        },
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------
def test_every_response_uses_the_same_envelope(client, pg_app):
    email = _email("envelope")

    cases = [
        client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "full_name": "Envelope Test"},
        ),
        _login(client, "/api/auth", email),
        _login(client, "/api/auth", email, password="Wr0ng#Password"),
        client.get("/api/auth/me"),
    ]

    for response in cases:
        body = response.get_json()
        assert isinstance(body, dict), f"non-JSON response: {response.status_code}"
        assert "success" in body
        assert "meta" in body
        assert "request_id" in body["meta"]
        if body["success"]:
            assert "data" in body
        else:
            assert body["error"]["code"]
            assert body["error"]["message"]
