"""Route registration.

Three mount points:

    /api           service-level endpoints, never versioned
                   GET /api/health - the contract with load balancers and
                   uptime monitors, so it must not move when the API versions.

    /api/auth      unversioned authentication
                   The documented auth contract. Kept stable across API
                   versions because every client - including a mobile app in
                   the field - has to be able to sign in regardless of which
                   domain version it was built against.

    /api/<version> versioned domain endpoints
                   Built by ``register_v1_routes()`` in ``routes/v1_registry.py``.
                   Auth is mounted here too, so the versioned surface is
                   complete and self-consistent.

Both auth mounts are served by the same handler functions, built fresh per
prefix because Flask refuses to register one blueprint object twice.
"""

from __future__ import annotations

from flask import Flask

from routes import health
from routes.alerts import create_alerts_blueprint
from routes.auth import create_auth_blueprint
from routes.geofences import create_geofences_blueprint
from routes.gps import create_gps_blueprint
from routes.inspections import create_inspections_blueprint
from routes.media import create_media_blueprint
from routes.ml import create_ml_blueprint
from routes.retrieval import create_retrieval_blueprint
from routes.v1_registry import register_v1_routes


def register_routes(app: Flask) -> None:
    """Mount every blueprint onto the application."""
    app.register_blueprint(health.blueprint, url_prefix="/api")
    # Stored media is bytes, not a versioned contract, so it sits outside /api
    # alongside health. The dev proxy already forwards /media to the backend.
    app.register_blueprint(create_media_blueprint(), url_prefix="/media")

    # Canonical versioned surface first, then the unversioned auth contract.
    app.register_blueprint(register_v1_routes(), url_prefix=f"/api/{app.config['API_VERSION']}")
    app.register_blueprint(create_auth_blueprint(), url_prefix="/api/auth")

    # Inspections are mounted twice. The versioned path is canonical; the
    # unversioned alias exists because the condition-assessment contract was
    # specified as `/api/inspections/...`. Both are served by the same handler
    # functions, built fresh per prefix because Flask refuses to register one
    # blueprint object twice. If a v2 ever changes this contract, the alias is
    # what keeps an older client working.
    app.register_blueprint(create_inspections_blueprint(), url_prefix="/api/inspections")

    # The security module is mounted twice for the same reason: its contract was
    # specified as `/api/gps/events`, `/api/geofences` and so on, while the
    # versioned path is canonical. Same handlers, built fresh per prefix.
    app.register_blueprint(create_gps_blueprint(), url_prefix="/api/gps")
    app.register_blueprint(create_geofences_blueprint(), url_prefix="/api/geofences")
    app.register_blueprint(create_alerts_blueprint(), url_prefix="/api/alerts")
    app.register_blueprint(create_ml_blueprint(), url_prefix="/api/ml")

    # Retrieval prediction is mounted twice for the same reason, and here the
    # alias is not optional: the module's contract was specified as
    # `POST /api/retrieval/predict` and `GET /api/retrieval/{custody_id}`, so the
    # unversioned path is the one callers were given. The versioned path remains
    # canonical for anything written against /api/v1.
    app.register_blueprint(create_retrieval_blueprint(), url_prefix="/api/retrieval")
