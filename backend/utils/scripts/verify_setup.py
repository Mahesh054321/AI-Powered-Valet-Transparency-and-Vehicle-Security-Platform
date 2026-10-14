"""One-command pre-flight check for a fresh clone.

    python scripts/verify_setup.py

Verifies, in order:

  1. Python version and every required dependency imports.
  2. backend/.env exists and contains a non-placeholder JWT secret.
  3. The configuration object builds without error.
  4. PostgreSQL is reachable and reports its version.
  5. Migrations are applied (alembic_version present).
  6. The Flask app boots and answers GET /api/health.

Exits non-zero on the first hard failure so it can be used in CI.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REQUIRED_MODULES = [
    "flask",
    "flask_cors",
    "flask_sqlalchemy",
    "sqlalchemy",
    "alembic",
    "psycopg2",
    "dotenv",
    "jwt",
    "cv2",
    "numpy",
    "PIL",
    "sklearn",
    "qrcode",
]

MIN_PYTHON = (3, 10)

# Console markers, not credentials.
PASS = "  [ OK ]"  # noqa: S105
FAIL = "  [FAIL]"
WARN = "  [WARN]"


def step(message: str) -> None:
    print(f"\n{message}")


def main() -> int:
    failures: list[str] = []
    print("=" * 74)
    print("  Valet Transparency Platform - setup verification")
    print("=" * 74)

    step("1. Python version")
    print(f"{PASS} {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < MIN_PYTHON:
        failures.append(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required.")

    step("2. Dependencies")
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
            print(f"{PASS} {module}")
        except ImportError as exc:
            print(f"{FAIL} {module} - {exc}")
            failures.append(f"Missing dependency: {module}")

    step("3. Environment file")
    env_file = BACKEND_ROOT / ".env"
    if not env_file.is_file():
        print(f"{FAIL} backend/.env is missing. Run: Copy-Item .env.example .env")
        failures.append("backend/.env missing")
    else:
        contents = env_file.read_text(encoding="utf-8")
        print(f"{PASS} backend/.env present ({len(contents)} bytes)")
        if "replace-with-64-char-random-secret" in contents:
            print(f"{WARN} JWT_SECRET_KEY still holds the placeholder value.")
            failures.append("JWT_SECRET_KEY is still the placeholder")

    step("4. Configuration")
    try:
        from config import get_settings

        settings = get_settings()
        print(f"{PASS} environment resolves to '{settings.ENV_NAME}'")
        print(f"{PASS} database target: {settings.SQLALCHEMY_DATABASE_URI.split('@')[-1]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} configuration error: {exc}")
        failures.append("configuration")
        return finish(failures)

    step("5. PostgreSQL connectivity")
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
            current_db = connection.execute(text("SELECT current_database()")).scalar_one()
        engine.dispose()
        print(f"{PASS} connected to '{current_db}' on PostgreSQL {version}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} cannot connect: {type(exc).__name__}")
        print("       Start PostgreSQL, create the database, and check backend/.env.")
        failures.append("database connection")
        return finish(failures)

    step("6. Migrations")
    try:
        from sqlalchemy import create_engine, inspect

        engine = create_engine(settings.SQLALCHEMY_DATABASE_URI)
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
        if "alembic_version" in tables:
            print(f"{PASS} migrations applied ({len(tables)} table(s) present)")
        else:
            print(f"{WARN} alembic_version not found - run: python scripts/init_db.py")
            failures.append("migrations not applied")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        failures.append("migration check")

    step("7. Application boot")
    try:
        from app import create_app

        app = create_app()
        response = app.test_client().get("/api/health")
        payload = response.get_json() or {}
        data = payload.get("data") or {}
        if response.status_code == 200 and payload.get("success"):
            db_state = (data.get("checks") or {}).get("database", {}).get("status", "unknown")
            print(
                f"{PASS} GET /api/health -> 200 "
                f"(status={data.get('status')}, database={db_state})"
            )
            if data.get("database_healthy") is False:
                print(f"{WARN} the API is up but PostgreSQL is not reachable")
        else:
            print(f"{FAIL} GET /api/health returned {response.status_code}")
            failures.append("health endpoint")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} app failed to start: {type(exc).__name__}: {exc}")
        failures.append("app boot")

    return finish(failures)


def finish(failures: list[str]) -> int:
    print("\n" + "=" * 74)
    if failures:
        print(f"  Setup verification FAILED ({len(failures)} problem(s)):")
        for item in failures:
            print(f"    - {item}")
        print("=" * 74)
        return 1
    print("  Setup verification PASSED - the platform is ready to run.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
