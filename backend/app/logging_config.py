"""Logging configuration.

Plain-text logs by default; set ``JSON_LOGS=1`` for single-line JSON suitable
for a log aggregator. SQLAlchemy's statement echo follows ``DB_ECHO``.
"""

from __future__ import annotations

import logging
import sys

from flask import Flask


def configure_logging(app: Flask) -> None:
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    if app.config.get("JSON_LOGS"):
        handler.setFormatter(
            logging.Formatter(
                '{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )

    app.logger.handlers = [handler]
    app.logger.setLevel(level)
    logging.getLogger().setLevel(level)

    echo_sql = bool(app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}).get("echo"))
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO if echo_sql else logging.WARNING)
