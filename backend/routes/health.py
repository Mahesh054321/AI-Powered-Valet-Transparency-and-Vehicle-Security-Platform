"""Health-check endpoints.

    GET /api/health   single endpoint returning service and dependency state
    GET /api/readyz   readiness only, for orchestrators that want them separate

``/api/health`` is unversioned on purpose: uptime monitors and load balancers
depend on it, and it must not move when the domain API is versioned.
"""

from __future__ import annotations

from flask import Blueprint

from services.health import liveness_report, readiness_report
from utils.responses import ok

blueprint = Blueprint("health", __name__)


@blueprint.get("/health")
def health():
    """Liveness plus a database check in one response.

    ``status`` is ``"up"`` whenever the process can answer. ``checks.database``
    carries the PostgreSQL result, so a caller can distinguish "API alive but
    database down" from a total outage without a second request.
    """
    readiness, database_healthy = readiness_report()
    payload = liveness_report()
    payload["checks"] = readiness["checks"]
    payload["telemetry_source"] = readiness["telemetry_source"]
    # The process is up even when a dependency is not; the caller decides
    # whether a degraded database makes the instance unusable.
    payload["database_healthy"] = database_healthy
    return ok(payload)


@blueprint.get("/readyz")
def readiness():
    """Readiness only. Returns 503 when a dependency is down."""
    report, healthy = readiness_report()
    return ok(report, status=200 if healthy else 503)
