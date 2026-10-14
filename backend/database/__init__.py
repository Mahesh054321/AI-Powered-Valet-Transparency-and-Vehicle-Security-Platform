"""Database access layer.

    database/__init__.py   session helpers and a connectivity ping
    database/checks.py     schema assertions shared by migrations and tests
    database/seed.py       idempotent reference-data seeding

Alembic migrations live in ``backend/migrations`` (the tool's default location,
configured by ``alembic.ini``) and are the only thing that changes the schema.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.extensions import db


def get_session() -> Session:
    """Return the scoped session bound to the current application context."""
    return db.session


def ping() -> dict[str, object]:
    """Execute a trivial round trip and report PostgreSQL server metadata.

    Used by CLI scripts and by the health endpoint's readiness probe.
    """
    version = db.session.execute(text("SHOW server_version")).scalar_one()
    current_db = db.session.execute(text("SELECT current_database()")).scalar_one()
    return {"database": current_db, "server_version": version}


__all__ = ["db", "get_session", "ping"]
