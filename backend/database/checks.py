"""Schema checks reused by Alembic migrations and by the test suite.

Keeping these as plain functions means the same assertions run at migration
time and under ``pytest``, so a migration can never quietly produce a schema
that the tests do not expect.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

REQUIRED_EXTENSIONS = ("pgcrypto",)


def ensure_extensions(
    connection: Connection, extensions: tuple[str, ...] = REQUIRED_EXTENSIONS
) -> list[str]:
    """Create required PostgreSQL extensions if they are missing.

    Returns the list of extensions that were created by this call. Creating an
    extension needs a role with sufficient privilege; if that fails the caller
    decides whether it is fatal.
    """
    installed = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT extname FROM pg_extension"
        ).fetchall()
    }
    created: list[str] = []
    for name in extensions:
        if name not in installed:
            connection.exec_driver_sql(f'CREATE EXTENSION IF NOT EXISTS "{name}"')
            created.append(name)
    return created


def installed_extensions(connection: Connection) -> list[str]:
    rows = connection.exec_driver_sql("SELECT extname FROM pg_extension ORDER BY extname")
    return [row[0] for row in rows.fetchall()]


def existing_tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def assert_extensions_present(
    connection: Connection, extensions: tuple[str, ...] = REQUIRED_EXTENSIONS
) -> None:
    missing = [name for name in extensions if name not in installed_extensions(connection)]
    if missing:
        raise AssertionError(f"Required PostgreSQL extension(s) missing: {', '.join(missing)}")
