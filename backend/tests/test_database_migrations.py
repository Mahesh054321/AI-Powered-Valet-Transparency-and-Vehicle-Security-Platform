"""Phase 1 tests: real PostgreSQL migration and schema verification.

Marked ``postgres`` and skipped automatically when no server is reachable, so
CI on a machine without PostgreSQL still passes on the rest of the suite.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

pytestmark = pytest.mark.postgres


def _alembic_config(url: str) -> Config:
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


class TestMigrations:
    def test_migration_history_is_linear(self, pg_app):
        """A branching history would make deployments non-deterministic."""
        cfg = _alembic_config(pg_app.config["SQLALCHEMY_DATABASE_URI"])
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"expected a single head, found {heads}"

        revisions = list(script.walk_revisions("base", "heads"))
        assert revisions, "no revisions found"
        # Every revision except the first must have exactly one down_revision.
        for revision in revisions:
            if revision.down_revision is not None:
                assert isinstance(revision.down_revision, str)

    def test_database_is_at_the_head_revision(self, pg_app):
        """The applied version must equal Alembic's single head.

        Compared against ``ScriptDirectory`` rather than a literal so adding a
        revision cannot silently leave this test asserting a stale number.
        """
        from app.extensions import db

        cfg = _alembic_config(pg_app.config["SQLALCHEMY_DATABASE_URI"])
        head = ScriptDirectory.from_config(cfg).get_current_head()

        with pg_app.app_context():
            statement = text("SELECT version_num FROM alembic_version")
            version = db.session.execute(statement).scalar_one()
        assert version == head

    def test_the_core_domain_revision_exists_in_history(self, pg_app):
        """Guards against the eleven-table revision being dropped or rebased."""
        cfg = _alembic_config(pg_app.config["SQLALCHEMY_DATABASE_URI"])
        revisions = {
            revision.revision for revision in ScriptDirectory.from_config(cfg).walk_revisions()
        }
        assert {"0001", "0002"} <= revisions

    def test_every_head_revision_applies_cleanly_from_base(self, pg_app):
        """A full downgrade/upgrade cycle must be reversible.

        The session fixture already migrated up; this walks back to base and
        forward again, which is what `downgrade()` correctness buys us in
        production rollbacks.
        """
        from alembic import command

        from app.extensions import db

        cfg = _alembic_config(pg_app.config["SQLALCHEMY_DATABASE_URI"])
        with pg_app.app_context():
            db.session.rollback()
            command.downgrade(cfg, "base")
            remaining = set(inspect(db.engine).get_table_names())
            assert remaining <= {"alembic_version"}, (
                f"downgrade to base left tables behind: {sorted(remaining)}"
            )

            command.upgrade(cfg, "head")
            restored = set(inspect(db.engine).get_table_names())
            assert {"users", "custody_sessions", "audit_logs"} <= restored

    def test_required_extensions_are_installed(self, pg_app):
        from app.extensions import db
        from database.checks import REQUIRED_EXTENSIONS, installed_extensions

        with pg_app.app_context():
            installed = installed_extensions(db.engine.connect())
        for name in REQUIRED_EXTENSIONS:
            assert name in installed, f"extension {name} was not created"

    def test_schema_meta_baseline_row_exists(self, pg_app):
        from app.extensions import db

        with pg_app.app_context():
            value = db.session.execute(
                text("SELECT value FROM schema_meta WHERE key = 'baseline'")
            ).scalar_one()
        assert value == "0001-baseline"

    def test_database_reports_postgres_dialect(self, pg_app):
        from app.extensions import db

        with pg_app.app_context():
            assert db.engine.dialect.name == "postgresql"
            assert int(db.session.execute(text("SHOW server_version_num")).scalar_one()) >= 130000

    def test_downgrade_removes_schema_meta(self, pg_app):
        """Proves the baseline migration is genuinely reversible."""
        from alembic import command

        from app.extensions import db

        cfg = _alembic_config(pg_app.config["SQLALCHEMY_DATABASE_URI"])
        with pg_app.app_context():
            command.downgrade(cfg, "base")
            assert "schema_meta" not in inspect(db.engine).get_table_names()
            command.upgrade(cfg, "head")
            assert "schema_meta" in inspect(db.engine).get_table_names()

    def test_readiness_probe_is_healthy_against_real_database(self, pg_client):
        response = pg_client.get("/api/readyz")
        assert response.status_code == 200
        body = response.get_json()
        assert body["data"]["status"] == "ready"
        assert body["data"]["checks"]["database"]["status"] == "up"
        assert "server_version" in body["data"]["checks"]["database"]

    def test_health_endpoint_reports_database_against_real_database(self, pg_client):
        response = pg_client.get("/api/health")
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["status"] == "up"
        assert data["database_healthy"] is True
        assert data["checks"]["database"]["status"] == "up"
        assert data["telemetry_source"] in {"simulated", "hardware"}

    def test_ping_helper_returns_metadata(self, pg_app):
        from database import ping

        with pg_app.app_context():
            report = ping()
        assert report["database"].endswith("_test")
        assert report["server_version"]
