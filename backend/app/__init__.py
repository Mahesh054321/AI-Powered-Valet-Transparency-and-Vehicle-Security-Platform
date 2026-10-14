"""Application factory - the single composition root.

``create_app()`` is the only place where extensions, routes, middleware and
error handlers are assembled. The dev server, the WSGI server and the test
suite all call it, so behaviour cannot drift between them.

Assembly order matters and is fixed:

    1. settings              config.get_settings()
    2. logging               app/logging_config.py
    3. database              app/extensions.db
    4. CORS                  app/cors_config.py
    5. routes                routes/register_routes()
    6. middleware            middleware/request_context.py
    7. error handlers        app/error_handlers.py
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify

from app.cors_config import configure_cors
from app.error_handlers import register_error_handlers
from app.extensions import db
from app.logging_config import configure_logging
from config import Settings, get_settings
from middleware import register_fallback_routes, register_request_context

# `routes` is deliberately NOT imported at module level. Importing it here would
# run routes -> services -> models while `models` is still initialising itself
# (models/__init__ imports app.extensions, which imports this module), so
# `from models import AuditAction` inside a service would fail with a partially
# initialised package. The cycle is broken by importing routes when an
# application is actually built, which is the only time it is needed.
#
# Symptom if this is ever moved back: `python -c "from models import Vehicle"`
# raises ImportError, even though the test suite still passes (its first test
# module imports app.extensions, which warms every package in a safe order).


def create_app(
    env_name: str | None = None,
    settings_object: Settings | None = None,
    config_overrides: dict[str, object] | None = None,
) -> Flask:
    """Build and configure a Flask application instance.

    Args:
        env_name: ``development`` | ``testing`` | ``production``. Defaults to
            ``FLASK_ENV``.
        settings_object: Pre-built settings instance (used by tests).
        config_overrides: Arbitrary ``app.config`` overrides applied last.
    """
    app = Flask(__name__, instance_relative_config=False)

    settings = settings_object or get_settings(env_name)
    app.config.from_object(settings)
    if config_overrides:
        app.config.update(config_overrides)

    app.config.setdefault("APP_NAME", "valet-transparency-api")
    app.config.setdefault("API_VERSION", "v1")
    app.config["APP_ENV"] = getattr(settings, "ENV_NAME", env_name or "development")

    # 2. logging -------------------------------------------------------------
    configure_logging(app)

    # 3. database ------------------------------------------------------------
    db.init_app(app)

    # Ensure upload storage exists; image services write here.
    upload_dir = app.config.get("UPLOAD_FOLDER")
    if upload_dir:
        Path(upload_dir).mkdir(parents=True, exist_ok=True)

    # 4. CORS ----------------------------------------------------------------
    configure_cors(app)

    # 5. routes --------------------------------------------------------------
    from routes import register_routes  # imported here; see the note above

    register_routes(app)

    @app.get("/")
    def service_root():
        return jsonify(
            {
                "success": True,
                "data": {
                    "service": app.config["APP_NAME"],
                    "api_version": app.config["API_VERSION"],
                    "health": "/api/health",
                    "message": "AI-Powered Valet Transparency and Vehicle Security Platform API",
                },
            }
        )

    # 6. middleware ----------------------------------------------------------
    register_fallback_routes(app)
    register_request_context(app)

    # 7. error handlers ------------------------------------------------------
    register_error_handlers(app)

    app.logger.info(
        "Initialised %s (env=%s, api=%s, telemetry=%s)",
        app.config["APP_NAME"],
        app.config["APP_ENV"],
        app.config["API_VERSION"],
        app.config.get("TELEMETRY_SOURCE", "simulated"),
    )
    return app
