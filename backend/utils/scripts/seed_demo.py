"""Reset and reseed the demonstration database in one command.

Usage (from the ``backend`` directory)::

    python utils/scripts/seed_demo.py                 # reset + seed + 90 days of history
    python utils/scripts/seed_demo.py --days 30       # shorter history, faster
    python utils/scripts/seed_demo.py --no-history    # curated demo rows only
    python utils/scripts/seed_demo.py --keep          # top up without dropping tables
    python utils/scripts/seed_demo.py --seed 7        # a different-but-plausible history

This exists because the demo dataset is built in layers, and running the layers
by hand is how a reviewer ends up with a half-built database and no idea which
half. One command, one report.

**Everything this script writes is synthetic and says so.** GPS fixes are
stored with ``source = SIMULATED`` per row; generated rows carry
``metadata_ = {"is_demo": true, "origin": "operational_history"}`` or a
``[DEMO]`` marker where the schema has no metadata column. The verification
report at the end re-checks that from the database rather than asserting it
from the code that wrote it, so a row that lost its label is a failure here.

Nothing in this script changes application behaviour. There is no demo flag the
API reads to relax a validation, widen a permission or lower a threshold: demo
data is ordinary data that happens to be synthetic, and the endpoints that
produce synthetic input on demand (``POST /inspections/demo``,
``POST /gps/simulate``) are staff-authenticated and opt-in per request.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from utils.scripts.init_db import apply_migrations, reset_database  # noqa: E402

#: Tables the report checks, in the order a reviewer would read them.
REPORT_TABLES = (
    "users",
    "vehicles",
    "parking_zones",
    "valet_tickets",
    "custody_sessions",
    "gps_events",
    "vehicle_inspections",
    "damage_detections",
    "security_alerts",
    "retrieval_predictions",
    "audit_logs",
)


def _scalar(sql: str) -> int:
    return int(db.session.execute(text(sql)).scalar_one())


def verify(app) -> tuple[dict[str, int], list[str]]:
    """Re-read the database and report what is actually there.

    Returns the row counts plus a list of problems. Problems are returned
    rather than raised so the report prints in full before the exit code says
    whether it passed.
    """
    problems: list[str] = []
    with app.app_context():
        # Table names come from the REPORT_TABLES constant above, never from
        # input, so the interpolation is not a query-injection surface.
        counts = {
            table: _scalar(f"SELECT count(*) FROM {table}")  # noqa: S608 - allow-listed above
            for table in REPORT_TABLES
        }

        customers = _scalar("SELECT count(*) FROM users WHERE role = 'CUSTOMER'")
        valets = _scalar("SELECT count(*) FROM users WHERE role = 'VALET_OPERATOR'")
        managers = _scalar("SELECT count(*) FROM users WHERE role = 'VALET_MANAGER'")
        zones = _scalar("SELECT count(*) FROM parking_zones")

        for label, actual, minimum in (
            ("customers", customers, 3),
            ("valet operators", valets, 2),
            ("managers", managers, 1),
            ("parking zones", zones, 2),
            ("vehicles", counts["vehicles"], 3),
        ):
            if actual < minimum:
                problems.append(f"expected at least {minimum} {label}, found {actual}")

        # Provenance, checked against the rows rather than the writer.
        unlabelled_gps = _scalar("SELECT count(*) FROM gps_events WHERE source <> 'SIMULATED'")
        if unlabelled_gps:
            problems.append(f"{unlabelled_gps} GPS fix(es) not labelled SIMULATED")

        hardware = _scalar("SELECT count(*) FROM gps_events WHERE source = 'HARDWARE'")
        if hardware:
            problems.append(f"{hardware} GPS fix(es) claim HARDWARE provenance")

        normal = _scalar("SELECT count(*) FROM gps_events WHERE movement_status = 'NORMAL'")
        flagged = _scalar(
            "SELECT count(*) FROM gps_events WHERE movement_status IN ('SUSPICIOUS', 'BREACH')"
        )
        if counts["gps_events"] and not normal:
            problems.append("no NORMAL movement samples - the dashboard would be all alarms")
        if counts["gps_events"] and not flagged:
            problems.append("no SUSPICIOUS/BREACH samples - nothing for the alert queue to show")

        orphan_alerts = _scalar(
            "SELECT count(*) FROM security_alerts a "
            "WHERE a.gps_event_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM gps_events g WHERE g.id = a.gps_event_id)"
        )
        if orphan_alerts:
            problems.append(f"{orphan_alerts} alert(s) cite a GPS fix that does not exist")

        unlabelled_demo = _scalar(
            # The ORM attribute is `metadata_`; the column is plain `metadata`,
            # because `metadata` is reserved on SQLAlchemy's declarative base.
            "SELECT count(*) FROM valet_tickets "
            "WHERE metadata ->> 'origin' = 'operational_history' "
            "AND coalesce((metadata ->> 'is_demo')::boolean, false) IS NOT TRUE"
        )
        if unlabelled_demo:
            problems.append(f"{unlabelled_demo} generated ticket(s) missing the is_demo marker")

        # The curated layer, counted separately. The totals above mix in the
        # generated history, so a reviewer checking "did the seed make three
        # customers and three vehicles?" needs the seed's own rows on their own
        # line rather than having to subtract 40 generated accounts.
        curated_customers = _scalar(
            "SELECT count(*) FROM users WHERE role = 'CUSTOMER' "
            "AND notes NOT LIKE '%Generated operational history%'"
        )
        curated_vehicles = _scalar(
            "SELECT count(*) FROM vehicles WHERE notes = '[DEMO] Seeded demonstration vehicle.'"
        )

        counts.update(
            {
                "customers": customers,
                "valet_operators": valets,
                "managers": managers,
                "movement_normal": normal,
                "movement_flagged": flagged,
                "curated_customers": curated_customers,
                "curated_vehicles": curated_vehicles,
            }
        )

        if curated_customers < 3:
            problems.append(f"curated seed has {curated_customers} customers, expected 3")
        if curated_vehicles < 3:
            problems.append(f"curated seed has {curated_vehicles} vehicles, expected 3")
    return counts, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset and reseed the demonstration database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days", type=int, default=90, help="days of generated history")
    parser.add_argument("--seed", type=int, default=20260909, help="RNG seed for the history")
    parser.add_argument(
        "--no-history", action="store_true", help="curated demo rows only, no generated history"
    )
    parser.add_argument(
        "--keep", action="store_true", help="do not drop tables; top up the existing database"
    )
    parser.add_argument("--env", default=None, help="development | testing | production")
    args = parser.parse_args(argv)

    app = create_app(env_name=args.env)
    target = app.config["SQLALCHEMY_DATABASE_URI"].rsplit("@", 1)[-1]
    print(f"[seed_demo] environment={app.config['APP_ENV']} target={target}")

    if not args.keep:
        print("[seed_demo] resetting schema...")
        reset_database(app)
    print("[seed_demo] applying migrations...")
    apply_migrations(app)

    from database.seed import run

    print("[seed_demo] writing curated demo rows...")
    result = run(app)
    for item in result.created:
        print(f"  + {item}")
    if result.generated_password:
        print(
            "\n  A bootstrap admin password was generated because SEED_ADMIN_PASSWORD is empty."
            f"\n  Copy it now, it is not stored anywhere: {result.generated_password}"
        )

    if not args.no_history:
        from utils.scripts.generate_operational_history import generate

        print(f"[seed_demo] generating {args.days} day(s) of operational history...")
        # `generate` expects a caller-supplied context; its own `main` pushes
        # one, and this script is that caller here.
        with app.app_context():
            stats = generate(days=args.days, seed=args.seed, reset=not args.keep)
        print(f"  + {stats.tickets} tickets / {stats.sessions} custody sessions")
        print(f"  + {stats.inspections} inspections / {stats.detections} damage findings")
        print(f"  + {stats.alerts} security alerts / {stats.predictions} retrieval predictions")
        print(f"  + {stats.gps_events} simulated GPS fixes")

    counts, problems = verify(app)

    print("\n[seed_demo] Curated seed rows")
    print(f"  {'customers':<24} {counts['curated_customers']}")
    print(f"  {'vehicles':<24} {counts['curated_vehicles']}")
    print(f"  {'valet operators':<24} {counts['valet_operators']}")
    print(f"  {'managers':<24} {counts['managers']}")
    print(f"  {'parking zones':<24} {counts['parking_zones']}")

    print("\n[seed_demo] Verification (curated + generated history)")
    for label in (
        "customers",
        "vehicles",
        "valet_tickets",
        "custody_sessions",
        "gps_events",
        "movement_normal",
        "movement_flagged",
        "vehicle_inspections",
        "damage_detections",
        "security_alerts",
        "retrieval_predictions",
        "audit_logs",
    ):
        print(f"  {label.replace('_', ' '):<24} {counts.get(label, 0)}")

    print()
    print("  " + "!" * 68)
    print("  SYNTHETIC DATA. Every row above was generated for demonstration.")
    print("  GPS fixes are stored source=SIMULATED; generated rows carry")
    print("  is_demo/origin markers. None of it is a record of a real valet")
    print("  operation, and no model here is trained on real customer data.")
    print("  " + "!" * 68)

    # The curated logins are what a reviewer signs in with. The history
    # generator adds ~40 more customers so the charts have spread; listing all
    # of them would bury the six accounts that matter under generated filler.
    print("\n[seed_demo] Sign-in accounts (password: Demo#Passw0rd)")
    with app.app_context():
        rows = db.session.execute(
            text(
                "SELECT email, role FROM users "
                "WHERE notes IS NULL OR notes NOT LIKE '%Generated operational history%' "
                "ORDER BY role, email"
            )
        ).fetchall()
        extra = _scalar(
            "SELECT count(*) FROM users "
            "WHERE notes LIKE '%Generated operational history%'"
        )
    for email, role in rows:
        print(f"  {email:<34} {role}")
    if extra:
        print(f"  (+ {extra} generated customer accounts used only to populate history)")

    if problems:
        print("\n[seed_demo] FAILED verification:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("\n[seed_demo] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
