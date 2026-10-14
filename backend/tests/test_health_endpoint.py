"""Contract tests for the health endpoints.

``GET /api/health`` is the integration point for load balancers and uptime
monitors, so its shape is pinned here: a rename or a moved path breaks a test
rather than breaking production monitoring.
"""

from __future__ import annotations

from datetime import datetime

REQUIRED_DATA_KEYS = {
    "status",
    "service",
    "api_version",
    "environment",
    "uptime_seconds",
    "server_time",
    "checks",
    "telemetry_source",
    "database_healthy",
}


class TestHealthEndpoint:
    def test_health_lives_at_api_health(self, client):
        assert client.get("/api/health").status_code == 200

    def test_health_uses_the_standard_envelope(self, client):
        body = client.get("/api/health").get_json()
        assert set(body) == {"success", "data", "meta"}
        assert body["success"] is True
        assert body["meta"]["request_id"]

    def test_health_payload_contains_every_documented_field(self, client):
        data = client.get("/api/health").get_json()["data"]
        missing = REQUIRED_DATA_KEYS - set(data)
        assert not missing, f"health payload is missing: {sorted(missing)}"

    def test_health_reports_process_as_up_regardless_of_dependencies(self, client):
        data = client.get("/api/health").get_json()["data"]
        assert data["status"] == "up"
        assert data["service"] == "valet-transparency-api"
        assert data["environment"] in {"development", "testing", "production"}

    def test_health_server_time_is_iso8601(self, client):
        data = client.get("/api/health").get_json()["data"]
        parsed = datetime.fromisoformat(data["server_time"])
        assert parsed.tzinfo is not None, "server_time must be timezone-aware"

    def test_health_uptime_is_a_non_negative_number(self, client):
        data = client.get("/api/health").get_json()["data"]
        assert isinstance(data["uptime_seconds"], int | float)
        assert data["uptime_seconds"] >= 0

    def test_health_declares_telemetry_provenance(self, client):
        data = client.get("/api/health").get_json()["data"]
        assert data["telemetry_source"] in {"simulated", "hardware"}

    def test_health_flags_an_unreachable_database_without_crashing(self, app, client):
        """Liveness must stay 200; only `database_healthy` flips."""
        from app.extensions import db

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://u:p@127.0.0.1:1/none"
            db.engines.clear()

        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["status"] == "up"
        assert data["database_healthy"] is False
        assert data["checks"]["database"]["status"] == "down"

    def test_readyz_returns_503_when_a_dependency_is_down(self, app):
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
        assert response.get_json()["data"]["status"] == "degraded"

    def test_health_is_not_versioned(self, client):
        """The contract is /api/health, not /api/v1/health."""
        assert client.get("/api/v1/health").status_code == 404

    def test_unknown_api_route_still_returns_the_json_envelope(self, client):
        body = client.get("/api/v1/nothing-here").get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"
