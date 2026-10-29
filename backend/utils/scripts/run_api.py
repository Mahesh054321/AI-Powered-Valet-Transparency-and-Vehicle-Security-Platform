"""Start the API for local development.

    python scripts/run_api.py                 # Flask dev server on :5000
    python scripts/run_api.py --port 8000
    python scripts/run_api.py --production    # waitress (Windows) / gunicorn

The Flask development server is fine for a demo but is single-threaded and
unhardened; ``--production`` switches to a real WSGI server.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Valet Transparency API.")
    # 0.0.0.0 is the default so a demo phone or a sandboxed preview host can
    # reach the API. Pass --host 127.0.0.1 to bind locally.
    parser.add_argument("--host", default=os.getenv("API_HOST", "0.0.0.0"))  # noqa: S104
    parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "5000")))
    parser.add_argument("--production", action="store_true", help="use waitress/gunicorn")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    from app import create_app

    app = create_app()

    print("\n" + "=" * 74)
    print("  AI-Powered Valet Transparency and Vehicle Security Platform - API")
    print("=" * 74)
    print(f"  environment : {app.config['APP_ENV']}")
    print(f"  api version : {app.config['API_VERSION']}")
    print(f"  database    : {app.config['SQLALCHEMY_DATABASE_URI'].split('@')[-1]}")
    print(f"  telemetry   : {app.config.get('TELEMETRY_SOURCE')}")
    print(f"  listening   : http://{args.host}:{args.port}")
    print(f"  health      : http://127.0.0.1:{args.port}/api/health")
    print("=" * 74 + "\n")

    if args.production:
        if sys.platform == "win32":
            from waitress import serve

            print("Serving with waitress.")
            serve(app, host=args.host, port=args.port, threads=8)
        else:
            # gunicorn imports the WSGI callable itself. Resolve it to an
            # absolute path so we never execute a lookalike from the CWD.
            gunicorn_bin = shutil.which("gunicorn")
            if not gunicorn_bin:
                print("gunicorn is not installed or not on PATH.", file=sys.stderr)
                return 1
            os.execv(  # noqa: S606 - explicit operator request, absolute path
                gunicorn_bin,
                [
                    gunicorn_bin,
                    "--bind",
                    f"{args.host}:{args.port}",
                    "--workers",
                    str(args.workers),
                    "wsgi:app",
                ],
            )
    else:
        app.run(host=args.host, port=args.port, debug=app.config.get("DEBUG", False), threaded=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
