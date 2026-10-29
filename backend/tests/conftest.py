"""Shared pytest fixtures.

Two application flavours are available:

* ``app`` / ``client``  - no database required. Used for config, security,
  validation and HTTP-envelope tests so `pytest` works even before PostgreSQL
  is installed.
* ``pg_app`` / ``pg_client`` - bound to a real PostgreSQL database, created and
  dropped per session. Marked with ``@pytest.mark.postgres`` and skipped
  automatically when no server is reachable.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("POSTGRES_PASSWORD", os.environ.get("POSTGRES_PASSWORD", "change-me-locally"))

# Tests render QR codes and store inspection photos as real files, because the
# code under test writes real files. Redirect that output to a scratch
# directory: a full run otherwise drops ~2,600 generated images into
# `backend/storage/media`, which is runtime output masquerading as source and
# bloats any snapshot or archive of the repository. Set before `config` is
# imported, since UPLOAD_FOLDER is read once at settings construction.
os.environ.setdefault("UPLOAD_FOLDER", tempfile.mkdtemp(prefix="valet-test-media-"))

from config import TestingSettings, build_database_url  # noqa: E402


@pytest.fixture()
def app():
    """In-memory application (no DB connection is opened by these tests)."""
    from app import create_app

    settings = TestingSettings()
    application = create_app(settings_object=settings, config_overrides={"TESTING": True})
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite+pysqlite:///:memory:"
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# PostgreSQL-backed fixtures
# ---------------------------------------------------------------------------
def _postgres_available(url: str) -> bool:
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_database_url() -> str:
    config = TestingSettings()
    return config.SQLALCHEMY_DATABASE_URI


def _drop_test_connections(url: str) -> None:
    """Close every connection to the test database except our own.

    Flask-SQLAlchemy's session is pooled and may still hold a lock from the
    last query a test ran. Alembic runs migrations on its *own* connection, so
    ``upgrade``/``downgrade`` would block forever waiting for that lock --
    which presents as a hung test session rather than an error.

    Terminating the app's idle connections is safe here: this database exists
    only for tests, and the pooled connections are re-established on demand.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            current_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> :pid"
                ),
                {"pid": current_pid},
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def pg_app(pg_database_url):
    if not _postgres_available(pg_database_url):
        pytest.skip(
            "PostgreSQL is not reachable at the configured test DSN. "
            "Create the test database (see README) to run these tests."
        )

    from alembic import command
    from alembic.config import Config

    from app import create_app

    application = create_app(env_name="testing")
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", pg_database_url)

    # ``load_current_user`` memoises the caller on ``flask.g``, and ``g`` lives
    # on the *app* context. This fixture is session-scoped and keeps one app
    # context pushed for the whole run, and Flask reuses an already-pushed app
    # context rather than making a fresh one per request -- so that single ``g``
    # would be shared by every request the session makes. The first
    # authenticated request would then pin the caller for the rest of the run:
    # an anonymous request returns 200, and an operator calling a manager-only
    # endpoint gets 201. Clearing the memo per request restores the real
    # semantics -- each request is authenticated from its own token.
    #
    # The function-scoped ``app`` fixture never had this problem: each of those
    # tests builds a new application and therefore a new ``g``.
    @application.before_request
    def _reset_cached_auth():  # pragma: no cover - test harness only
        from flask import g

        g.pop("auth_context", None)

    with application.app_context():
        _drop_test_connections(pg_database_url)
        command.upgrade(cfg, "head")
        yield application
        # Discard the ORM session's work, drop its pooled connections, then
        # migrate down. Without this the DROP TABLE waits on the session's lock.
        from app.extensions import db

        db.session.rollback()
        db.session.remove()
        _drop_test_connections(pg_database_url)
        command.downgrade(cfg, "base")


@pytest.fixture(autouse=True)
def _rollback_session(pg_app=None):
    """Leave no open transaction behind, so teardown cannot deadlock.

    Applies to every test; a no-op for tests that never touched the database.
    """
    yield
    try:
        from app.extensions import db

        db.session.rollback()
    except RuntimeError:
        # No application context was ever pushed (a non-database test), so
        # there is nothing to roll back. Teardown must not mask a real failure.
        pass


@pytest.fixture()
def pg_client(pg_app):
    return pg_app.test_client()


# ---------------------------------------------------------------------------
# Helpers exposed to tests
# ---------------------------------------------------------------------------
@pytest.fixture()
def auth_headers(app):
    """Build a valid Authorization header for a given role."""
    from utils.security import Role, create_token

    def _make(role: str = Role.CUSTOMER, user_id: str = "11111111-1111-1111-1111-111111111111"):
        with app.app_context():
            token, _ = create_token(user_id=user_id, role=role, email=f"{role.lower()}@valet.local")
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture()
def database_url() -> str:
    return build_database_url()
