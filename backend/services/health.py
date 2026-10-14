"""Service-level health probes.

Two distinct questions, deliberately kept apart:

* **liveness** - is the process alive? Must never touch a dependency, so a
  load balancer can distinguish "restarting" from "database is down".
* **readiness** - can it actually serve traffic? Performs a real round trip to
  PostgreSQL and reports latency.

This module contains no business logic; it reports infrastructure state only.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from flask import current_app
from sqlalchemy import text

from app.extensions import db

_START_TIME = time.monotonic()


def liveness_report() -> dict[str, Any]:
    """Process is alive. No dependencies consulted."""
    return {
        "status": "up",
        "service": current_app.config.get("APP_NAME"),
        "api_version": current_app.config.get("API_VERSION"),
        "environment": current_app.config.get("APP_ENV"),
        "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
        "server_time": datetime.now(tz=UTC).isoformat(),
    }


def readiness_report() -> tuple[dict[str, Any], bool]:
    """Check dependencies. Returns ``(report, healthy)``.

    Never raises: a health probe that throws is worse than one that reports
    failure, because the caller cannot tell a crash from an outage.
    """
    checks: dict[str, Any] = {"database": {"status": "unknown"}}
    healthy = True

    try:
        started = time.monotonic()
        with db.session.no_autoflush:
            db.session.execute(text("SELECT 1"))
            version = db.session.execute(text("SHOW server_version")).scalar_one()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        checks["database"] = {
            "status": "up",
            "latency_ms": latency_ms,
            "server_version": version,
        }
    except Exception as exc:  # noqa: BLE001 - health probes must never raise
        healthy = False
        checks["database"] = {"status": "down", "error": type(exc).__name__}

    report = {
        "status": "ready" if healthy else "degraded",
        "checks": checks,
        # Provenance is part of the readiness contract: the UI labels every map
        # with it so simulated telemetry is never mistaken for hardware.
        "telemetry_source": current_app.config.get("TELEMETRY_SOURCE", "simulated"),
    }
    return report, healthy
