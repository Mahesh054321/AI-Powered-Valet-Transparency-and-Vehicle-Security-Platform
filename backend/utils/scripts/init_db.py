"""Initialise the database: create extensions, apply migrations, verify schema.

Usage (from the ``backend`` directory, with the virtual environment active):

    python scripts/init_db.py                 # apply migrations + verify
    python scripts/init_db.py --reset         # drop everything, then rebuild
    python scripts/init_db.py --seed          # also run the reference-data seeder
    python scripts/init_db.py --check-only    # verify without changing anything

This script is the single "does my database work?" command referenced by the
README and by CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from database.checks import assert_extensions_present, installed_extensions  # noqa: E402


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


def apply_migrations(app) -> None:
    with app.app_context():
        command.upgrade(_alembic_config(), "head")


def reset_database(app) -> None:
    """Drop every table in the schema. Intended for local development only."""
    if app.config.get("APP_ENV") == "production":
        raise SystemExit("Refusing to reset a production database.")
    with app.app_context():
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if tables:
            quoted = ", ".join(f'"{table}"' for table in reversed(tables))
            db.session.execute(text(f"DROP TABLE IF EXISTS {quoted} CASCADE"))
            db.session.commit()
            print(f"  dropped {len(tables)} table(s): {', '.join(tables)}")
        else:
            print("  (no tables present)")

        # Migrations also install trigger functions. `DROP TABLE ... CASCADE`
        # removes the triggers but leaves the functions behind, and the
        # migrations create them with a plain `CREATE FUNCTION`, so a second
        # run died on "function already exists". Dropping them here is what
        # makes `--reset` genuinely reset rather than reset-once.
        #
        # Extension-owned functions (pgcrypto) are excluded via pg_depend, so
        # this only ever removes functions this application installed.
        signatures = [
            row[0]
            for row in db.session.execute(
                text(
                    """
                    SELECT p.oid::regprocedure::text
                      FROM pg_proc p
                      JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'public'
                       AND NOT EXISTS (
                           SELECT 1 FROM pg_depend d
                            WHERE d.objid = p.oid AND d.deptype = 'e'
                       )
                    """
                )
            ).fetchall()
        ]
        for signature in signatures:
            db.session.execute(text(f"DROP FUNCTION IF EXISTS {signature} CASCADE"))
        db.session.commit()
        if signatures:
            print(f"  dropped {len(signatures)} function(s): {', '.join(signatures)}")


def verify_schema(app) -> dict[str, object]:
    with app.app_context():
        raw = db.engine.connect()
        assert_extensions_present(raw)
        tables = sorted(inspect(db.engine).get_table_names())
        baseline = db.session.execute(
            text("SELECT value FROM schema_meta WHERE key = 'baseline'")
        ).scalar_one_or_none()
        return {
            "database": db.session.execute(text("SELECT current_database()")).scalar_one(),
            "server_version": db.session.execute(text("SHOW server_version")).scalar_one(),
            "extensions": installed_extensions(raw),
            "tables": tables,
            "baseline": baseline,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the platform database.")
    parser.add_argument("--reset", action="store_true", help="drop all tables before migrating")
    parser.add_argument("--seed", action="store_true", help="run the reference-data seeder")
    parser.add_argument(
        "--check-only", action="store_true", help="verify the schema, change nothing"
    )
    parser.add_argument("--env", default=None, help="development | testing | production")
    args = parser.parse_args()

    app = create_app(env_name=args.env)
    print(f"[init_db] environment={app.config['APP_ENV']}")
    print(f"[init_db] target={app.config['SQLALCHEMY_DATABASE_URI'].rsplit('@', 1)[-1]}")

    try:
        if args.check_only:
            report = verify_schema(app)
        else:
            if args.reset:
                print("[init_db] resetting schema...")
                reset_database(app)
            print("[init_db] applying migrations...")
            apply_migrations(app)
            report = verify_schema(app)
    except Exception as exc:  # noqa: BLE001 - report a clean message, not a traceback
        print(f"\n[init_db] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "[init_db] Hint: confirm PostgreSQL is running and that POSTGRES_* values in "
            "backend/.env match a database you have created.",
            file=sys.stderr,
        )
        return 1

    print("\n[init_db] Schema report")
    print(f"  database      : {report['database']}")
    print(f"  server        : {report['server_version']}")
    print(f"  extensions    : {', '.join(report['extensions'])}")
    print(f"  baseline      : {report['baseline']}")
    print(f"  tables ({len(report['tables'])}) : {', '.join(report['tables']) or '(none yet)'}")

    if args.seed:
        from database.seed import run

        result = run(app)
        print("\n[init_db] Seeder")
        for item in result.created:
            print(f"  + {item}")
        for item in result.skipped:
            print(f"  = {item}")
        if result.generated_password:
            print(
                "\n  A bootstrap admin password was generated because SEED_ADMIN_PASSWORD is empty."
                f"\n  Copy it now, it is not stored anywhere: {result.generated_password}"
            )

    print("\n[init_db] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
