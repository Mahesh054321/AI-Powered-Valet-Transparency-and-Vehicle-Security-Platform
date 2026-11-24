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
from models import User  # noqa: E402
from utils.security import Role  # noqa: E402


def main() -> int:
    if not os.environ.get("SEED_ADMIN_EMAIL") or not os.environ.get("SEED_ADMIN_PASSWORD"):
        print("[bootstrap_admin] SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD are required", file=sys.stderr)
        return 1

    app = create_app()
    with app.app_context():
        result = bootstrap_admin(app)
        # An explicit one-time reset is needed: normal bootstrap never updates
        # existing accounts. Only permit resetting this exact ADMIN account.
        # Keeping this behind an opt-in env var protects existing accounts.
        if os.environ.get("RESET_BOOTSTRAP_ADMIN_PASSWORD") == "1":
            email = app.config["SEED_ADMIN_EMAIL"]
            admin = db.session.query(User).filter(User.email == email).one_or_none()
            if admin is None or admin.role != Role.ADMIN.value:
                print("[bootstrap_admin] Password reset refused: ADMIN account not found", file=sys.stderr)
                db.session.rollback()
                return 1
            password = os.environ["SEED_ADMIN_PASSWORD"]
            if len(password) < 12:
                print("[bootstrap_admin] Password reset refused: use at least 12 characters", file=sys.stderr)
                db.session.rollback()
                return 1
            admin.set_password(password)
            print("[bootstrap_admin] Administrator password updated")
        db.session.commit()

    for account in result.created:
        print(f"[bootstrap_admin] Created: {account}")
    for account in result.skipped:
        print(f"[bootstrap_admin] Already exists: {account}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
