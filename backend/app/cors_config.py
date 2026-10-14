"""CORS configuration.

Origins come from ``CORS_ALLOWED_ORIGINS``. A wildcard is never combined with
credentials - ``ProductionSettings.validate()`` rejects ``*`` outright, and the
explicit list here means a browser on any other origin is refused.
"""

from __future__ import annotations

from flask import Flask

from app.extensions import cors

ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_HEADERS = ["Content-Type", "Authorization", "X-Request-Id"]
EXPOSED_HEADERS = ["X-Request-Id", "Content-Disposition"]


def configure_cors(app: Flask) -> None:
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ALLOWED_ORIGINS"]}},
        supports_credentials=True,
        methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSED_HEADERS,
        max_age=600,
    )
