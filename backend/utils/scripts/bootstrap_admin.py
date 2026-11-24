"""One-time, idempotent ADMIN bootstrap for Render Free (no interactive shell).

Does not create public demo accounts. Requires explicit credentials from the
hosting environment; never prints the password or any generated password.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from database.seed import bootstrap_admin  # noqa: E402


def main() -> int:
    if not os.environ.get("SEED_ADMIN_EMAIL") or not os.environ.get("SEED_ADMIN_PASSWORD"):
        print("[bootstrap_admin] SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD are required", file=sys.stderr)
        return 1

    app = create_app()
    with app.app_context():
        result = bootstrap_admin(app)
        db.session.commit()

    for account in result.created:
        print(f"[bootstrap_admin] Created: {account}")
    for account in result.skipped:
        print(f"[bootstrap_admin] Already exists: {account}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
