"""Print the resolved configuration with every secret redacted.

Usage:  python scripts/show_config.py [--env production]

Answers the question "what will the app actually use?" without ever printing a
password, token secret or full DSN.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import get_settings  # noqa: E402


def _render(value: object) -> str:
    """`as_safe_dict()` has already removed credentials; just stringify."""
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show resolved, redacted configuration.")
    parser.add_argument("--env", default=None)
    args = parser.parse_args()

    settings = get_settings(args.env)
    snapshot = settings.as_safe_dict()

    width = max(len(key) for key in snapshot)
    print(f"Resolved configuration (environment: {args.env or 'from FLASK_ENV'})\n")
    for key in sorted(snapshot):
        print(f"  {key.ljust(width)} : {_render(snapshot[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
