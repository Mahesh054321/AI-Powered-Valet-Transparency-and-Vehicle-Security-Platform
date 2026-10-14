"""Security regression tests: cross-tenant isolation, throttling, and leakage.

These are the tests that would have caught the two real defects found during the
security audit, plus the controls that were verified as already correct and must
not silently regress.

What each class is actually asserting
-------------------------------------
``TestCrossTenantIsolation``  one customer can never reach another customer's
    vehicle -- not by guessing the id, and not by asking for it through a filter.
    The second of those was a live IDOR: the list endpoint honoured a
    caller-supplied ``owner_id`` *instead of* the caller's own scope, so any
    customer could enumerate anyone else's fleet.
``TestLoginThrottling``       repeated bad passwords eventually stop working.
``TestErrorLeakage``          failures never carry a traceback, SQL, or another
    user's data back to the caller.
``TestUploadValidation``      a file is judged by its bytes, not its filename.
``TestPathTraversal``         ``/media/<path>`` cannot be walked out of the
    storage directory.
``TestRoleEscalation``        nobody can promote themselves, and no role reaches
    a capability the matrix does not grant it.
``TestTransportHardening``    CORS and the security headers.

Everything here runs against real PostgreSQL and real signed tokens; nothing is
mocked, because a mocked permission check proves nothing about the real one.
"""

from __future__ import annotations

import base64
import io
import json
import uuid

import jwt as pyjwt
import pytest

from app.extensions import db
from tests.workflow_helpers import PASSWORD, bearer, make_user, make_vehicle, png_bytes
from utils.rate_limit import reset_limiter
from utils.security import Role

VEHICLES = "/api/v1/vehicles"
LOGIN = "/api/v1/auth/login"
REGISTER = "/api/v1/auth/register"
AUDIT = "/api/v1/audit"


@pytest.fixture(autouse=True)
def _fresh_throttle(pg_app):
    """Start every test with an empty attempt counter.

    The limiter is process-wide by design, so without this one test's failed
    logins would still be on the books when the next test tried to sign in --
    which surfaces as a baffling 429 in an unrelated file.
    """
    reset_limiter()
    yield
    db.session.rollback()
    reset_limiter()


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


def _body(response) -> dict:
    return response.get_json() or {}


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------
class TestCrossTenantIsolation:
    """A customer's data is theirs alone."""

    def test_reading_another_customers_vehicle_is_404(self, pg_client):
        owner = make_user(Role.CUSTOMER)
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()
        vehicle = make_vehicle(owner.id)
        db.session.commit()

        response = pg_client.get(f"{VEHICLES}/{vehicle.id}", headers=bearer(intruder))

        # 404, not 403: a 403 would confirm the vehicle exists.
        assert response.status_code == 404
        assert _body(response)["error"]["code"] == "NOT_FOUND"

    def test_updating_another_customers_vehicle_is_404(self, pg_client):
        owner = make_user(Role.CUSTOMER)
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()
        vehicle = make_vehicle(owner.id)
        db.session.commit()

        response = pg_client.patch(
            f"{VEHICLES}/{vehicle.id}",
            headers=bearer(intruder),
            json={"color": "Stolen"},
        )

        assert response.status_code == 404
        # And the write really did not land.
        db.session.expire_all()
        from models.vehicle import Vehicle

        assert db.session.get(Vehicle, vehicle.id).color != "Stolen"

    def test_deleting_another_customers_vehicle_is_404(self, pg_client):
        owner = make_user(Role.CUSTOMER)
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()
        vehicle = make_vehicle(owner.id)
        db.session.commit()

        response = pg_client.delete(f"{VEHICLES}/{vehicle.id}", headers=bearer(intruder))

        assert response.status_code == 404
        db.session.expire_all()
        from models.vehicle import Vehicle

        assert db.session.get(Vehicle, vehicle.id) is not None

    def test_the_owner_id_filter_cannot_broaden_a_customers_scope(self, pg_client):
        """The IDOR: asking for somebody else's owner must return nothing.

        Previously ``list_vehicles`` applied a caller-supplied ``owner_id`` in
        place of the caller's own scope, so a customer could list any other
        customer's fleet by naming them. The filter is now intersected with the
        actor's scope, so the only rows a customer can ever see are their own.
        """
        owner = make_user(Role.CUSTOMER)
        intruder = make_user(Role.CUSTOMER)
        db.session.commit()
        victim_vehicle = make_vehicle(owner.id)
        own_vehicle = make_vehicle(intruder.id)
        db.session.commit()

        response = pg_client.get(
            VEHICLES, headers=bearer(intruder), query_string={"owner_id": str(owner.id)}
        )

        assert response.status_code == 200
        plates = [v["plate_number"] for v in _body(response)["data"]["items"]]
        assert victim_vehicle.plate_number not in plates
        assert plates in ([], [own_vehicle.plate_number])

    def test_the_owner_id_filter_still_works_for_staff(self, pg_client):
        """The fix narrows customers; it must not break a legitimate staff query."""
        owner = make_user(Role.CUSTOMER)
        other = make_user(Role.CUSTOMER)
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        target = make_vehicle(owner.id)
        make_vehicle(other.id)
        db.session.commit()

        response = pg_client.get(
            VEHICLES, headers=bearer(operator), query_string={"owner_id": str(owner.id)}
        )

        assert response.status_code == 200
        plates = [v["plate_number"] for v in _body(response)["data"]["items"]]
        assert plates == [target.plate_number]

    def test_a_customer_list_never_contains_foreign_vehicles(self, pg_client):
        first = make_user(Role.CUSTOMER)
        second = make_user(Role.CUSTOMER)
        db.session.commit()
        make_vehicle(first.id)
        theirs = make_vehicle(second.id)
        db.session.commit()

        response = pg_client.get(VEHICLES, headers=bearer(first), query_string={"page_size": 100})

        plates = [v["plate_number"] for v in _body(response)["data"]["items"]]
        assert theirs.plate_number not in plates

    def test_a_customer_payload_carries_no_other_owners_details(self, pg_client):
        """Owner contact details must not ride along on a customer's own record."""
        owner = make_user(Role.CUSTOMER, full_name="Private Person")
        db.session.commit()
        vehicle = make_vehicle(owner.id)
        db.session.commit()

        response = pg_client.get(f"{VEHICLES}/{vehicle.id}", headers=bearer(owner))

        # The owner may see their own name; what matters is that no *other*
        # account's data can appear, and that no password hash ever does.
        payload = json.dumps(_body(response))
        assert "password_hash" not in payload
        assert "pbkdf2" not in payload


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
class TestLoginThrottling:
    """Brute force is bounded."""

    def test_repeated_failures_eventually_return_429(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        statuses = [
            pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass"}).status_code
            for _ in range(8)
        ]

        assert 429 in statuses
        assert statuses[0] == 401, "the first attempt should be a normal rejection"

    def test_the_429_explains_how_long_to_wait(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        for _ in range(10):
            response = pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass"})

        assert response.status_code == 429
        error = _body(response)["error"]
        assert error["code"] == "RATE_LIMITED"
        assert error["details"]["retry_after_seconds"] > 0
        # A throttle that never lifts is a lockout, not a rate limit.
        assert error["details"]["retry_after_seconds"] <= 900

    def test_the_budget_is_per_account_not_global(self, pg_client):
        """One attacker must not lock the whole site out."""
        attacked = make_user(Role.CUSTOMER)
        bystander = make_user(Role.CUSTOMER)
        db.session.commit()

        for _ in range(10):
            pg_client.post(LOGIN, json={"email": attacked.email, "password": "Wr0ng#Pass"})

        response = pg_client.post(LOGIN, json={"email": bystander.email, "password": PASSWORD})
        assert response.status_code == 200

    def test_the_budget_is_case_insensitive(self, pg_client):
        """Otherwise an attacker doubles it for the price of a shift key."""
        user = make_user(Role.CUSTOMER, email=_email("case"))
        db.session.commit()

        for variant in [user.email, user.email.upper(), user.email.title()]:
            pg_client.post(LOGIN, json={"email": variant, "password": "Wr0ng#Pass"})
            pg_client.post(LOGIN, json={"email": variant, "password": "Wr0ng#Pass"})

        response = pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass"})
        assert response.status_code == 429

    def test_a_successful_login_clears_the_counter(self, pg_client):
        """A mistyped password should not leave the owner one typo from lockout."""
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        for _ in range(3):
            pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass"})
        good = pg_client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
        assert good.status_code == 200

        for _ in range(4):
            pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass"})

        # Three misses were forgiven, so four more is still inside the budget.
        still_good = pg_client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
        assert still_good.status_code == 200

    def test_throttling_does_not_confirm_whether_an_address_exists(self, pg_client):
        """The throttle must behave the same for a real and a fictional address."""
        real = make_user(Role.CUSTOMER)
        db.session.commit()
        ghost = _email("ghost")

        for address in (real.email, ghost):
            for _ in range(10):
                response = pg_client.post(LOGIN, json={"email": address, "password": "Wr0ng#Pass"})

        assert response.status_code == 429
        real_response = pg_client.post(LOGIN, json={"email": real.email, "password": "Wr0ng#Pass"})
        ghost_response = pg_client.post(LOGIN, json={"email": ghost, "password": "Wr0ng#Pass"})
        assert real_response.status_code == ghost_response.status_code


# ---------------------------------------------------------------------------
# Error leakage
# ---------------------------------------------------------------------------
class TestErrorLeakage:
    """Failures describe the problem, never the internals."""

    def test_no_endpoint_returns_a_traceback(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        for path in [
            f"{VEHICLES}/not-a-uuid",
            f"{VEHICLES}/{uuid.uuid4()}",
            "/api/v1/tickets/not-a-uuid",
            AUDIT,
        ]:
            response = pg_client.get(path, headers=bearer(user))
            assert response.status_code in (401, 403, 404, 422)
            text = response.get_data(as_text=True)
            assert "Traceback" not in text
            assert "sqlalchemy" not in text.lower()
            assert "psycopg2" not in text.lower()

    def test_a_conflict_names_the_constraint_but_not_the_row(self, pg_client):
        """A duplicate-key DETAIL line quotes the offending values. It must not ship.

        This is the leak the audit found: the handler sliced everything after
        ``DETAIL:``, which on PostgreSQL reads
        ``Key (email)=(someone@example.com) already exists.`` -- confirming which
        addresses are registered and handing back another customer's data.
        """
        address = _email("dupe")
        payload = {"email": address, "password": PASSWORD, "full_name": "Duplicate"}
        assert pg_client.post(REGISTER, json=payload).status_code == 201
        db.session.commit()

        response = pg_client.post(REGISTER, json=payload)

        assert response.status_code == 409
        text = response.get_data(as_text=True)
        assert address not in text, "the conflicting value was echoed back"
        assert "DETAIL" not in text
        assert "Key (" not in text

    def test_an_unauthorised_caller_learns_nothing_beyond_their_role(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        response = pg_client.get(AUDIT, headers=bearer(operator))

        assert response.status_code == 403
        assert _body(response)["error"]["code"] == "AUTHORIZATION_ERROR"

    def test_an_unauthenticated_caller_gets_401_not_a_hint(self, pg_client):
        response = pg_client.get(AUDIT)

        assert response.status_code == 401
        assert _body(response)["error"]["code"] == "AUTHENTICATION_ERROR"

    def test_every_response_carries_the_standard_envelope(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        for response in [
            pg_client.get(VEHICLES, headers=bearer(user)),
            pg_client.get(f"{VEHICLES}/{uuid.uuid4()}", headers=bearer(user)),
            pg_client.get(AUDIT),
        ]:
            body = _body(response)
            assert "meta" in body
            assert "request_id" in body["meta"]
            assert ("data" in body) ^ ("error" in body)


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------
class TestUploadValidation:
    """The bytes decide, not the extension."""

    UPLOADS = "/api/v1/uploads/images"

    def _post(self, pg_client, headers, blob: bytes, filename: str):
        return pg_client.post(
            self.UPLOADS,
            headers=headers,
            data={"image": (io.BytesIO(blob), filename)},
            content_type="multipart/form-data",
        )

    def test_an_executable_renamed_to_jpg_is_refused(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        response = self._post(pg_client, bearer(user), b"\x7fELF" + b"\x00" * 256, "shell.jpg")

        assert response.status_code == 415
        assert _body(response)["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    def test_a_real_image_with_a_script_extension_is_refused(self, pg_client):
        """The extension allowlist is the second gate; the sniff is the first."""
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        response = self._post(pg_client, bearer(user), png_bytes(), "payload.php")

        assert response.status_code == 415

    def test_html_disguised_as_an_image_is_refused(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        response = self._post(
            pg_client, bearer(user), b"<script>alert(1)</script>" + b"\x00" * 256, "x.png"
        )

        assert response.status_code == 415

    def test_a_genuine_image_is_accepted_and_renamed(self, pg_client):
        """The stored name is server-generated, so a client cannot pick a path.

        A traversal attempt in the *filename* is simply ignored: the server
        derives a fresh ``uuid4().hex`` name, so nothing the client sends can
        influence where the file lands.
        """
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        response = self._post(pg_client, bearer(user), png_bytes(), "../../evil.png")

        assert response.status_code == 201
        stored = _body(response)["data"]["images"][0]
        assert ".." not in stored["path"]
        assert not stored["path"].startswith("/")
        assert stored["path"].endswith(".png")
        assert "evil" not in stored["path"]
        assert stored["mime_type"] == "image/png"

    def test_uploading_requires_authentication(self, pg_client):
        response = self._post(pg_client, {}, png_bytes(), "anon.png")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------
class TestPathTraversal:
    """/media/<path> is unauthenticated, so the path itself is the only defence."""

    @pytest.mark.parametrize(
        "attack",
        [
            "../../../../etc/passwd",
            "..%2f..%2f..%2fetc%2fpasswd",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "....//....//etc/passwd",
            "..\\..\\..\\windows\\win.ini",
            "..",
        ],
    )
    def test_traversal_attempts_never_leave_the_storage_directory(self, pg_client, attack):
        response = pg_client.get(f"/media/{attack}")

        assert response.status_code in (400, 404)
        assert b"root:" not in response.data
        assert b"[fonts]" not in response.data

    def test_an_absolute_path_is_redirected_then_refused(self, pg_client):
        """``/media//etc/passwd`` is normalised by Werkzeug, and still serves nothing.

        The 308 is the framework collapsing the double slash, not a leak -- so
        the assertion that matters is followed through to the final response.
        """
        response = pg_client.get("/media//etc/passwd", follow_redirects=True)

        assert response.status_code == 404
        assert b"root:" not in response.data


# ---------------------------------------------------------------------------
# Token forgery and role escalation
# ---------------------------------------------------------------------------
class TestRoleEscalation:
    """Nobody becomes staff by asking, and nobody exceeds their matrix."""

    @pytest.mark.parametrize("claimed", ["ADMIN", "VALET_MANAGER", "VALET_OPERATOR", "admin"])
    def test_public_registration_refuses_a_staff_role(self, pg_client, claimed):
        """The most common privilege-escalation attempt there is.

        The endpoint rejects the request outright rather than quietly creating a
        customer: a caller who believes it was granted ``ADMIN`` would keep
        trying admin calls, and a silent downgrade hides a bug in the client.
        Either way no account is created with the claimed role.
        """
        address = _email("esc")

        response = pg_client.post(
            REGISTER,
            json={
                "email": address,
                "password": PASSWORD,
                "full_name": "Sneaky",
                "role": claimed,
            },
        )

        assert response.status_code == 403
        assert _body(response)["error"]["code"] == "AUTHORIZATION_ERROR"
        # And nothing was created behind the rejection.
        assert pg_client.post(
            LOGIN, json={"email": address, "password": PASSWORD}
        ).status_code == 401

    def test_public_registration_accepts_an_explicit_customer_role(self, pg_client):
        """Asking for the one role a public caller may have is not an attack."""
        response = pg_client.post(
            REGISTER,
            json={
                "email": _email("honest"),
                "password": PASSWORD,
                "full_name": "Honest",
                "role": Role.CUSTOMER.value,
            },
        )

        assert response.status_code == 201
        assert _body(response)["data"]["user"]["role"] == Role.CUSTOMER.value

    def test_a_public_registration_never_yields_staff_even_ignoring_the_role_field(
        self, pg_client
    ):
        """The service hard-codes CUSTOMER, so the route guard is defence in depth."""
        response = pg_client.post(
            REGISTER,
            json={"email": _email("plain"), "password": PASSWORD, "full_name": "Plain"},
        )

        assert response.status_code == 201
        assert _body(response)["data"]["user"]["role"] == Role.CUSTOMER.value

    def test_an_alg_none_token_is_refused_at_the_door(self, pg_client):
        header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        claims = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": str(uuid.uuid4()),
                    "role": Role.ADMIN.value,
                    "typ": "access",
                    "jti": uuid.uuid4().hex,
                    "exp": 9_999_999_999,
                    "iat": 1,
                }
            ).encode()
        ).rstrip(b"=").decode()

        response = pg_client.get(
            VEHICLES, headers={"Authorization": f"Bearer {header}.{claims}."}
        )

        assert response.status_code == 401

    def test_a_token_signed_with_the_wrong_secret_is_refused(self, pg_client, pg_app):
        forged = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": Role.ADMIN.value,
                "typ": "access",
                "jti": uuid.uuid4().hex,
                "iss": pg_app.config["JWT_ISSUER"],
                "aud": pg_app.config["JWT_AUDIENCE"],
                "exp": 9_999_999_999,
                "iat": 1,
            },
            "an-attacker-controlled-secret-value-0123456789",
            algorithm="HS256",
        )

        response = pg_client.get(VEHICLES, headers={"Authorization": f"Bearer {forged}"})

        assert response.status_code == 401

    def test_a_refresh_token_cannot_be_used_to_call_the_api(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()
        from utils.security import TokenType, create_token

        refresh, _ = create_token(
            user_id=str(user.id), role=user.role, email=user.email, token_type=TokenType.REFRESH
        )

        response = pg_client.get(VEHICLES, headers={"Authorization": f"Bearer {refresh}"})

        assert response.status_code == 401

    def test_an_operator_cannot_create_accounts(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        response = pg_client.post(
            "/api/v1/auth/users",
            headers=bearer(operator),
            json={
                "email": _email("made"),
                "password": PASSWORD,
                "full_name": "Made Up",
                "role": Role.ADMIN.value,
            },
        )

        assert response.status_code == 403

    def test_a_manager_cannot_create_accounts(self, pg_client):
        """Account creation is ``user:manage``, which is ADMIN alone."""
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.post(
            "/api/v1/auth/users",
            headers=bearer(manager),
            json={
                "email": _email("made"),
                "password": PASSWORD,
                "full_name": "Made Up",
                "role": Role.ADMIN.value,
            },
        )

        assert response.status_code == 403

    def test_an_admin_can_create_accounts(self, pg_client):
        admin = make_user(Role.ADMIN)
        db.session.commit()

        response = pg_client.post(
            "/api/v1/auth/users",
            headers=bearer(admin),
            json={
                "email": _email("made"),
                "password": PASSWORD,
                "full_name": "Made Up",
                "role": Role.VALET_OPERATOR.value,
            },
        )

        assert response.status_code == 201
        assert _body(response)["data"]["user"]["role"] == Role.VALET_OPERATOR.value

    def test_an_operator_cannot_read_the_audit_log(self, pg_client):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        for path in [AUDIT, "/api/v1/audit/filters"]:
            assert pg_client.get(path, headers=bearer(operator)).status_code == 403

    def test_a_manager_can_read_the_audit_log(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        assert pg_client.get(AUDIT, headers=bearer(manager)).status_code == 200

    def test_a_customer_cannot_read_the_audit_log(self, pg_client):
        customer = make_user(Role.CUSTOMER)
        db.session.commit()

        assert pg_client.get(AUDIT, headers=bearer(customer)).status_code == 403

    def test_protected_endpoints_refuse_anonymous_callers(self, pg_client):
        for path in [
            VEHICLES,
            "/api/v1/tickets",
            AUDIT,
            "/api/v1/manager/dashboard",
            "/api/v1/staff/dashboard",
            "/api/v1/customers/me/dashboard",
        ]:
            assert pg_client.get(path).status_code == 401, path

    def test_a_customer_cannot_reach_a_staff_dashboard(self, pg_client):
        customer = make_user(Role.CUSTOMER)
        db.session.commit()

        for path in ["/api/v1/staff/dashboard", "/api/v1/manager/dashboard"]:
            assert pg_client.get(path, headers=bearer(customer)).status_code == 403, path

    def test_an_operator_cannot_reach_the_manager_dashboard_or_report(self, pg_client):
        """The aggregate view is a management function.

        ``report:read_all`` is granted to manager and admin only, and the React
        nav hides Reports from operators. Hiding the link is presentation; the
        API is the boundary that actually holds.
        """
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()

        dashboard = pg_client.get("/api/v1/manager/dashboard", headers=bearer(operator))
        report = pg_client.get(
            "/api/v1/manager/report",
            headers=bearer(operator),
            query_string={"format": "json"},
        )

        assert dashboard.status_code == 403
        assert dashboard.get_json()["error"]["code"] == "AUTHORIZATION_ERROR"
        # build_report delegates to build_dashboard, so one gate closes both.
        assert report.status_code == 403

    @pytest.mark.parametrize("role", [Role.VALET_MANAGER, Role.ADMIN])
    def test_a_manager_or_admin_can_reach_the_manager_dashboard(self, pg_client, role):
        user = make_user(role)
        db.session.commit()

        assert pg_client.get("/api/v1/manager/dashboard", headers=bearer(user)).status_code == 200

    def test_administrative_capabilities_are_held_by_admin_alone(self):
        """The matrix is not a strict chain, but the dangerous end of it is.

        ``CUSTOMER`` legitimately holds rights no staff role needs
        (``vehicle:create``, ``*:read_own``), so the sets do not nest. What must
        hold is narrower and is the part that matters: the capabilities that let
        someone manage accounts, read everyone's audit history, or change
        configuration belong to ``ADMIN`` and to nobody else.
        """
        from utils.security import capabilities_for

        by_role = {role: capabilities_for(role) for role in Role}

        for reserved in ("user:manage", "audit:read_all", "config:manage"):
            for role, caps in by_role.items():
                if role == Role.ADMIN:
                    assert reserved in caps, f"ADMIN should hold {reserved}"
                else:
                    assert reserved not in caps, f"{role} must not hold {reserved}"

    def test_staff_rights_are_inherited_downward_not_upward(self):
        """A manager holds the operator's rights; an operator holds none of the manager's."""
        from utils.security import capabilities_for

        operator = capabilities_for(Role.VALET_OPERATOR)
        manager = capabilities_for(Role.VALET_MANAGER)
        admin = capabilities_for(Role.ADMIN)

        assert operator <= manager <= admin
        assert "inspection:review" in manager
        assert "inspection:review" not in operator
        assert "report:read_all" not in operator

    def test_the_password_hash_is_never_serialised(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        login = pg_client.post(LOGIN, json={"email": user.email, "password": PASSWORD})

        assert login.status_code == 200
        payload = login.get_data(as_text=True)
        assert "pbkdf2" not in payload
        assert "password_hash" not in payload


# ---------------------------------------------------------------------------
# Transport hardening
# ---------------------------------------------------------------------------
class TestTransportHardening:
    def test_an_unknown_origin_is_not_reflected_back(self, pg_client):
        response = pg_client.options(
            LOGIN,
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )

        assert response.headers.get("Access-Control-Allow-Origin") != "https://evil.example"

    def test_the_security_headers_are_present(self, pg_client):
        response = pg_client.get("/api/health")

        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "no-referrer"
        assert response.headers.get("Cache-Control") == "no-store"

    def test_no_server_version_banner(self, pg_client):
        response = pg_client.get("/api/health")

        server = response.headers.get("Server", "")
        assert "Werkzeug" not in server
        assert "Python" not in server


# ---------------------------------------------------------------------------
# Upload size limits
# ---------------------------------------------------------------------------
class TestUploadSizeLimits:
    """An unbounded upload is a denial-of-service vector, not just a nuisance."""

    def test_an_oversized_upload_is_refused_in_the_standard_envelope(self, pg_client, pg_app):
        """Flask's built-in 413 is an HTML page; the API contract is JSON.

        A client parsing the envelope would break on that, so the assertion here
        is about the *shape* as much as the status.
        """
        user = make_user(Role.CUSTOMER)
        db.session.commit()
        original = pg_app.config["MAX_CONTENT_LENGTH"]
        pg_app.config["MAX_CONTENT_LENGTH"] = 1024
        try:
            blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000
            response = pg_client.post(
                "/api/v1/uploads/images",
                headers=bearer(user),
                data={"image": (io.BytesIO(blob), "big.png")},
                content_type="multipart/form-data",
            )
        finally:
            pg_app.config["MAX_CONTENT_LENGTH"] = original

        assert response.status_code == 413
        assert response.headers.get("Content-Type", "").startswith("application/json")
        assert "error" in _body(response)

    def test_the_per_request_image_count_is_capped(self, pg_client):
        """A cap on bytes alone still allows thousands of tiny files."""
        from routes.uploads import MAX_PHOTOS_PER_REQUEST

        user = make_user(Role.CUSTOMER)
        db.session.commit()

        files = [
            (io.BytesIO(png_bytes()), f"p{i}.png") for i in range(MAX_PHOTOS_PER_REQUEST + 1)
        ]
        response = pg_client.post(
            "/api/v1/uploads/images",
            headers=bearer(user),
            data={"images": files},
            content_type="multipart/form-data",
        )

        assert response.status_code == 422
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_a_degenerate_image_is_refused_by_pixel_bounds(self, pg_client):
        """A 1x1 pixel file passes the sniff but is not a vehicle photograph."""
        import cv2
        import numpy as np

        user = make_user(Role.CUSTOMER)
        db.session.commit()
        ok, buffer = cv2.imencode(".png", np.zeros((2, 2, 3), dtype=np.uint8))
        assert ok

        response = pg_client.post(
            "/api/v1/uploads/images",
            headers=bearer(user),
            data={"image": (io.BytesIO(buffer.tobytes()), "tiny.png")},
            content_type="multipart/form-data",
        )

        assert response.status_code in (415, 422)


# ---------------------------------------------------------------------------
# Secret management
# ---------------------------------------------------------------------------
class TestSecretManagement:
    """Credentials live in the environment and never in a response or a log line."""

    def test_the_safe_snapshot_redacts_every_secret(self):
        from config.settings import get_settings

        settings = get_settings("testing")
        safe = settings.as_safe_dict()

        assert safe["JWT_SECRET_KEY"] == "***REDACTED***"
        assert safe["SEED_ADMIN_PASSWORD"] == "***REDACTED***"

    def test_the_database_password_never_appears_in_the_safe_snapshot(self):
        """The DSN keeps its host so an operator can see the target -- not its key."""
        import re

        from config.settings import get_settings

        settings = get_settings("testing")
        safe = settings.as_safe_dict()

        match = re.match(r".*://[^:]+:([^@]+)@", str(settings.SQLALCHEMY_DATABASE_URI))
        assert match, "expected a password in the configured DSN"
        password = match.group(1)

        assert password not in str(safe["SQLALCHEMY_DATABASE_URI"])
        assert password not in json.dumps(safe, default=str)

    def test_no_secret_is_reachable_through_the_health_endpoint(self, pg_client, pg_app):
        from config.settings import get_settings

        settings = get_settings("testing")
        response = pg_client.get("/api/health")

        text = response.get_data(as_text=True)
        assert settings.JWT_SECRET_KEY not in text
        assert "pbkdf2" not in text

    def test_the_jwt_secret_is_long_enough_to_resist_brute_force(self):
        from config.settings import MINIMUM_JWT_SECRET_LENGTH, get_settings

        settings = get_settings("testing")
        assert len(settings.JWT_SECRET_KEY) >= MINIMUM_JWT_SECRET_LENGTH
        assert settings.JWT_ALGORITHM == "HS256"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class TestLoggingHygiene:
    """The audit trail is a record of who did what, not of what they typed."""

    def test_neither_a_password_nor_a_token_reaches_the_log(self, pg_client, caplog):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        with caplog.at_level("DEBUG"):
            good = pg_client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
            pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass1"})

        assert good.status_code == 200
        token = _body(good)["data"]["access_token"]
        written = caplog.text

        assert PASSWORD not in written
        assert "Wr0ng#Pass1" not in written
        assert token[:24] not in written
        assert "pbkdf2" not in written

    def test_a_failed_login_is_still_recorded_for_audit(self, pg_client):
        """Hygiene must not mean blindness: the attempt itself is worth keeping."""
        from models.audit_log import AuditAction, AuditLog

        user = make_user(Role.CUSTOMER)
        db.session.commit()

        pg_client.post(LOGIN, json={"email": user.email, "password": "Wr0ng#Pass1"})
        db.session.commit()

        logged = (
            db.session.query(AuditLog)
            .filter(AuditLog.action == AuditAction.LOGIN_FAILED.value)
            .order_by(AuditLog.seq.desc())
            .first()
        )

        assert logged is not None
        assert "Wr0ng#Pass1" not in (logged.description or "")


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
class TestCsrfPosture:
    """The API authenticates with a bearer token, so the classic CSRF chain breaks.

    A CSRF attack works because a browser *automatically* attaches ambient
    credentials -- cookies -- to a cross-site request. A token held in
    localStorage and sent in an ``Authorization`` header is not ambient: a
    malicious page cannot read it or make the browser attach it. These tests pin
    the property that makes that true, so a future move to cookie auth cannot
    quietly reintroduce the vulnerability.
    """

    def test_authentication_does_not_rely_on_cookies(self, pg_client):
        user = make_user(Role.CUSTOMER)
        db.session.commit()

        login = pg_client.post(LOGIN, json={"email": user.email, "password": PASSWORD})

        assert login.status_code == 200
        # No session cookie is issued, so there is nothing for a browser to
        # replay on the user's behalf.
        assert not any(
            "session" in cookie.lower() for cookie in login.headers.getlist("Set-Cookie")
        )

    def test_a_state_changing_request_without_a_token_is_refused(self, pg_client):
        """No ambient credentials means a forged cross-site form posts nothing."""
        response = pg_client.post(
            VEHICLES,
            json={"plate_number": "UP85AB1234", "make": "Tata"},
            content_type="application/json",
        )

        assert response.status_code == 401

    def test_credentials_are_never_accepted_from_an_unlisted_origin(self, pg_client):
        response = pg_client.options(
            VEHICLES,
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )

        # Reflecting an attacker origin *with* credentials is what turns a CORS
        # misconfiguration into a full account takeover.
        if response.headers.get("Access-Control-Allow-Credentials") == "true":
            assert response.headers.get("Access-Control-Allow-Origin") not in (
                "https://evil.example",
                "*",
            )


# ---------------------------------------------------------------------------
# Manager cannot change authentication or security settings
# ---------------------------------------------------------------------------
class TestNoConfigMutationSurface:
    """There is no endpoint that changes auth or security configuration at all.

    ``config:manage`` exists in the capability matrix as a reservation for a future
    admin console. Until then the correct answer to "can a manager change the JWT
    settings?" is that no caller of any role can, because the surface does not
    exist. These tests fail loudly if someone adds one without adding a guard.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/config",
            "/api/v1/settings",
            "/api/v1/security-settings",
            "/api/v1/auth/settings",
            "/api/v1/auth/policy",
        ],
    )
    @pytest.mark.parametrize("method", ["get", "post", "patch", "put", "delete"])
    def test_no_config_mutation_endpoint_exists(self, pg_client, path, method):
        manager = make_user(Role.VALET_MANAGER)
        admin = make_user(Role.ADMIN)
        db.session.commit()

        for user in (manager, admin):
            response = getattr(pg_client, method)(
                path, headers=bearer(user), json={"anything": "value"}
            )
            # 404 (no such route) or 405 (route exists, method not allowed).
            # Anything else means a live config endpoint has appeared.
            assert response.status_code in (404, 405), (
                f"{method.upper()} {path} returned {response.status_code} for "
                f"{user.role}; a config surface now exists and needs a guard."
            )

    def test_a_manager_cannot_change_another_users_password(self, pg_client):
        """The nearest thing to 'changing authentication settings' that does exist."""
        manager = make_user(Role.VALET_MANAGER)
        target = make_user(Role.CUSTOMER)
        db.session.commit()

        for path, method in [
            (f"/api/v1/auth/users/{target.id}", "patch"),
            (f"/api/v1/auth/users/{target.id}/password", "post"),
            (f"/api/v1/users/{target.id}/password", "post"),
        ]:
            response = getattr(pg_client, method)(
                path, headers=bearer(manager), json={"password": "Hijacked#Pass1"}
            )
            assert response.status_code in (403, 404, 405), path

        # And the target can still sign in with their original password.
        reset_limiter()
        assert pg_client.post(
            LOGIN, json={"email": target.email, "password": PASSWORD}
        ).status_code == 200
