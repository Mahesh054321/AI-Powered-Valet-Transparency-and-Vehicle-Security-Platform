"""Phase 1 tests: app factory, configuration and the JSON error envelope."""

from __future__ import annotations

import pytest

from config import SETTINGS_MAP, DevelopmentSettings, ProductionSettings, get_settings


class TestAppFactory:
    def test_create_app_returns_flask_instance(self, app):
        assert app.config["TESTING"] is True
        assert app.config["API_VERSION"] == "v1"

    def test_liveness_endpoint_is_up(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "up"
        assert body["data"]["environment"] in {"development", "production", "testing"}

    def test_response_envelope_has_meta_and_request_id(self, client):
        response = client.get("/api/health")
        body = response.get_json()
        assert set(body) == {"success", "data", "meta"}
        assert "request_id" in body["meta"]
        assert response.headers.get("X-Request-Id")

    def test_client_supplied_request_id_is_echoed(self, client):
        response = client.get("/api/health", headers={"X-Request-Id": "rid-abc-123"})
        assert response.headers["X-Request-Id"] == "rid-abc-123"

    def test_security_headers_present(self, client):
        headers = client.get("/api/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"

    def test_unknown_api_route_returns_json_envelope(self, client):
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        body = response.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"

    def test_readiness_reports_degraded_without_database(self):
        # Build a dedicated app pointed at an unreachable DSN so the probe's
        # failure branch executes. (The shared `app` fixture resolves to an
        # in-memory SQLite URL, which would answer SELECT 1 and make this
        # assertion meaningless.)
        from app import create_app
        from config import TestingSettings

        settings = TestingSettings()
        broken = create_app(
            settings_object=settings,
            config_overrides={
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u:p@127.0.0.1:1/none",
            },
        )
        response = broken.test_client().get("/api/readyz")
        assert response.status_code == 503
        body = response.get_json()
        assert body["data"]["status"] == "degraded"
        assert body["data"]["checks"]["database"]["status"] == "down"

    def test_readiness_exposes_telemetry_source_label(self, client):
        body = client.get("/api/readyz").get_json()
        # The probe must always state where telemetry comes from.
        assert body["data"]["telemetry_source"] in {"simulated", "hardware"}


class TestConfig:
    def test_config_map_covers_all_environments(self):
        assert set(SETTINGS_MAP) == {"development", "testing", "test", "production"}

    def test_unknown_environment_raises(self):
        with pytest.raises(ValueError, match="Unknown FLASK_ENV"):
            get_settings("staging-that-does-not-exist")

    def test_development_generates_ephemeral_secret_when_weak(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        config = DevelopmentSettings()
        config.validate()
        assert len(config.JWT_SECRET_KEY) >= 32

    def test_production_rejects_weak_secret(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "weak")
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            get_settings("production")

    def test_production_rejects_wildcard_cors(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            get_settings("production")

    def test_database_url_is_built_from_parts_and_quoted(self, monkeypatch):
        from config import build_database_url

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_USER", "valet_user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:word/1")
        monkeypatch.setenv("POSTGRES_HOST", "db.internal")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "valet_db")

        url = build_database_url()
        assert url.startswith("postgresql+psycopg2://")
        # Special characters must be percent-encoded, not left raw.
        assert "p%40ss%3Aword%2F1" in url
        assert "@db.internal:5433/valet_db" in url

    def test_missing_database_password_raises(self, monkeypatch):
        from config import build_database_url

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_PASSWORD", "")
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            build_database_url()

    def test_explicit_database_url_wins(self, monkeypatch):
        from config import build_database_url

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg2://a:b@localhost:5432/db"
        )
        assert build_database_url() == "postgresql+psycopg2://a:b@localhost:5432/db"

    def test_boolean_and_numeric_coercion(self, monkeypatch):
        monkeypatch.setenv("DB_ECHO", "1")
        monkeypatch.setenv("DB_POOL_SIZE", "4")
        monkeypatch.setenv("MAX_CONTENT_LENGTH_MB", "3")
        config = DevelopmentSettings()
        assert config.SQLALCHEMY_ENGINE_OPTIONS["echo"] is True
        assert config.SQLALCHEMY_ENGINE_OPTIONS["pool_size"] == 4
        assert config.MAX_CONTENT_LENGTH == 3 * 1024 * 1024

    def test_invalid_integer_environment_raises(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_SIZE", "many")
        with pytest.raises(ValueError, match="DB_POOL_SIZE"):
            DevelopmentSettings()

    def test_upload_folder_is_absolute(self, app):
        from pathlib import Path

        assert Path(app.config["UPLOAD_FOLDER"]).is_absolute()

    def test_safe_dict_redacts_secrets(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-value-that-must-not-leak")
        config = DevelopmentSettings()
        snapshot = config.as_safe_dict()
        assert snapshot["JWT_SECRET_KEY"] == "***REDACTED***"
        assert "super-secret-value-that-must-not-leak" not in str(snapshot.values())

    def test_safe_dict_masks_dsn_but_keeps_host(self, monkeypatch):
        from config import mask_database_url

        masked = mask_database_url("postgresql+psycopg2://valet_user:s3cr3t@db.internal:5432/valet_db")
        assert masked == "postgresql+psycopg2://***:***@db.internal:5432/valet_db"
        assert "s3cr3t" not in masked
        assert mask_database_url("not-a-url") == "***REDACTED***"

    def test_telemetry_source_defaults_to_simulated(self, monkeypatch):
        monkeypatch.delenv("TELEMETRY_SOURCE", raising=False)
        assert DevelopmentSettings().TELEMETRY_SOURCE == "simulated"

    def test_testing_config_points_at_separate_database(self):
        from config import TestingSettings

        config = TestingSettings()
        assert config.SQLALCHEMY_DATABASE_URI.endswith("/valet_db_test")
        assert ProductionSettings is not TestingSettings
